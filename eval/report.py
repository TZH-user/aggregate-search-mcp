"""从 scores.json + raw 数据计算评测指标,输出 eval/report.md 与控制台摘要。"""

import json
from pathlib import Path

EVAL = Path(__file__).resolve().parent
scores = json.loads((EVAL / "scores.json").read_text(encoding="utf-8"))
agg_raw = json.loads((EVAL / "raw" / "agg_results.json").read_text(encoding="utf-8"))
web_raw = json.loads((EVAL / "raw" / "web_results.json").read_text(encoding="utf-8"))
goldset = json.loads((EVAL / "goldset.json").read_text(encoding="utf-8"))

GOLD_APPLICABLE = {q["id"] for q in goldset["queries"] if q["gold_type"] in ("fact", "fact_consistency", "domain")}
DIRS = {q["id"]: q["direction"] for q in goldset["queries"]}
DIR_CN = {"zh_recency": "A 中文时效", "en_authority": "B 英文定位", "zh_authority": "C 中文定位",
          "noise_resist": "D 跑题抵抗", "exact_fact": "E 精确事实"}


def tool_scores(qid, tool):
    if tool == "web" and qid == "E1":
        recs = scores[qid]["web"]
        n = len(scores[qid].get("web_all_scores", []))
        if n and n != len(recs):
            return [{"rank": i + 1, "score": s, "evidence": recs[min(i, len(recs) - 1)]["evidence"]}
                    for i, s in enumerate(scores[qid]["web_all_scores"])]
    return scores[qid][tool]


def first_gold_rank(qid, tool):
    """金标命中名次:score>=1 且 evidence 标注'金标'字样视为命中(域名命中不要求相关性=2,
    如 C3 官方论坛子域:命中域名金标但作为'官网'答案只值1分),取最靠前。"""
    for r in tool_scores(qid, tool):
        if r["score"] >= 1 and "金标" in r["evidence"]:
            return r["rank"]
    return None


rows, agg_mrr, web_mrr, agg_hits, web_hits = [], 0.0, 0.0, 0, 0
for q in goldset["queries"]:
    qid = q["id"]
    a, w = tool_scores(qid, "agg"), tool_scores(qid, "web")
    a_avg = sum(r["score"] for r in a) / len(a)
    w_avg = sum(r["score"] for r in w) / len(w)
    a_junk = sum(1 for r in a if r["score"] == 0)
    w_junk = sum(1 for r in w if r["score"] == 0)
    ga, gw = first_gold_rank(qid, "agg"), first_gold_rank(qid, "web")
    if qid in GOLD_APPLICABLE:
        agg_mrr += 0 if ga is None else 1 / ga
        web_mrr += 0 if gw is None else 1 / gw
        agg_hits += ga is not None
        web_hits += gw is not None
        gtxt = f"agg@{ga or '×'} web@{gw or '×'}"
    else:
        gtxt = "(D向不适用)"
    rows.append((qid, DIR_CN[DIRS[qid]], q["query"][:26], f"{a_avg:.2f}", f"{w_avg:.2f}",
                 str(a_junk), str(w_junk), gtxt, f"{len(a)}", f"{len(w)}"))

n_gold = len(GOLD_APPLICABLE)
a_all = [r["score"] for qid in scores if not qid.startswith("_") for r in tool_scores(qid, "agg")]
w_all = [r["score"] for qid in scores if not qid.startswith("_") for r in tool_scores(qid, "web")]
lat = [agg_raw[q["id"]]["latency_s"] for q in goldset["queries"]]
lat_sorted = sorted(lat)

hdr = ["ID", "方向", "查询", "agg均分", "web均分", "agg垃圾", "web垃圾", "金标命中", "agg条数", "web条数"]
table = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
table += ["| " + " | ".join(r) + " |" for r in rows]

dir_stats = []
for dcn in DIR_CN.values():
    rs = [r for r in rows if r[1] == dcn]
    if not rs:
        continue
    aa = sum(float(r[3]) for r in rs) / len(rs)
    wa = sum(float(r[4]) for r in rs) / len(rs)
    dir_stats.append(f"{dcn}: agg {aa:.2f} / web {wa:.2f}")

summary = f"""
## 总指标

| 指标 | aggregate-search | 内置 WebSearch |
|---|---|---|
| 金标 Hit@5({n_gold} 题适用) | **{agg_hits}/{n_gold} ({agg_hits / n_gold:.0%})** | **{web_hits}/{n_gold} ({web_hits / n_gold:.0%})** |
| MRR | {agg_mrr / n_gold:.3f} | {web_mrr / n_gold:.3f} |
| 相关性均分(0-2,全部结果) | {sum(a_all) / len(a_all):.3f} | {sum(w_all) / len(w_all):.3f} |
| 垃圾条数(score=0) | {sum(1 for s in a_all if s == 0)}/{len(a_all)} | {sum(1 for s in w_all if s == 0)}/{len(w_all)} |
| 结果条数覆盖 | {len(a_all)}/70(恒为5) | {len(w_all)}/70(3题不足5) |
| 延迟 | 均值 {sum(lat) / len(lat):.1f}s / 中位 {lat_sorted[len(lat) // 2]:.1f}s / 最大 {max(lat):.1f}s | 未测 |

分方向相关性均分: {'; '.join(dir_stats)}
"""

report = f"""# aggregate-search vs 内置 WebSearch 对比评测报告

- 日期:2026-10-24(相关性过滤启用后);方法与金标准见 [goldset.md](goldset.md),逐条判分留证见 [scores.json](scores.json),原始数据见 raw/
- 半盲声明:判分者见过两侧原始输出,属半盲;每条判分附 evidence 可复核
- 过滤前基线见 [report_before.md](report_before.md) / [scores_before.json](scores_before.json)

{chr(10).join(table)}
{summary}
"""

# ---- 版本趋势对比(v1 无过滤 → v2 +过滤 → v3 +限条/去重 → v4 +语义重排 → v6 +近重复合并) ----
BASELINES = [
    ("v1 无过滤", EVAL / "scores_before.json"),
    ("v2 +覆盖率过滤", EVAL / "scores_filter_only.json"),
    ("v3 +限条+去重修复", EVAL / "scores_v3.json"),
    ("v4 +语义重排", EVAL / "scores_v4.json"),
]
qids = [q["id"] for q in goldset["queries"]]


def agg_metrics(sc):
    gold = mrr = 0.0
    rel = junk = n = 0
    for qid in qids:
        rs = sc[qid]["agg"] if qid in sc else []
        for r in rs:
            rel += r["score"]
            junk += r["score"] == 0
            n += 1
        if qid in GOLD_APPLICABLE:
            g = next((r["rank"] for r in rs
                      if r["score"] >= 1 and "金标" in r["evidence"]), None)
            gold += g is not None
            mrr += 0 if g is None else 1 / g
    return {"hit": int(gold), "mrr": mrr / len(GOLD_APPLICABLE), "rel": rel / max(1, n),
            "junk": int(junk), "n": n}


versions = [(name, agg_metrics(json.loads(pth.read_text(encoding="utf-8"))))
            for name, pth in BASELINES if pth.exists()]
versions.append(("v6 +近重复合并(当前)", agg_metrics(scores)))

report += """## 版本趋势(aggregate 侧,同一冻结评测集)

| 指标 | """ + " | ".join(n for n, _ in versions) + """ |
|---|""" + "---|" * len(versions) + """
| 金标 Hit@5 | """ + " | ".join(f"{m['hit']}/11" for _, m in versions) + """ |
| MRR | """ + " | ".join(f"{m['mrr']:.3f}" for _, m in versions) + """ |
| 相关性均分 | """ + " | ".join(f"{m['rel']:.3f}" for _, m in versions) + """ |
| 垃圾条数 | """ + " | ".join(f"{m['junk']}/{m['n']}" for _, m in versions) + """ |

注:
- 各轮引擎返回池存在自然波动(单轮指标噪声约 ±0.1),趋势看方向不看个位数;
- **v6 轮智谱配额耗尽(1113),仅 3 引擎参与**(v4 为 4 引擎),对比含引擎池构成的混淆变量;
  近重复合并的效果以定点验证为准:H4 场景(苹果股价)同站参数变体从 2 席降回 1 席(eval/raw/holdout_rerank.json 对照);
- v4(智谱 embedding-3 语义重排)相关性均分 1.800 曾首超内置搜索(1.790),D1 主题撞词垃圾全灭;
- v6 新增 read_url 全文读取与 LLM 重排档(AGG_RERANK=llm,glm-4-flash),不影响本轮排序配置(=v4 的 embedding 档);
- v1/v2/v3/v4 基线判分见 scores_before / scores_filter_only / scores_v3 / scores_v4.json。
"""
(EVAL / "report.md").write_text(report, encoding="utf-8")
print(chr(10).join(table))
print(summary)
print("saved -> eval/report.md")
