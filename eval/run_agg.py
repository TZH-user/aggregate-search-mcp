"""aggregate-search 侧评测数据采集:跑 goldset 全部查询,原始结果落盘 eval/raw/。

会真实调用引擎、消耗少量配额(14 题 x 4 引擎)。延迟为真实墙钟时间。
"""

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from aggregate_search import orchestrator  # noqa: E402
from aggregate_search.orchestrator import aggregate_search  # noqa: E402

EVAL = ROOT / "eval"
RAW = EVAL / "raw"
RAW.mkdir(parents=True, exist_ok=True)

goldset = json.loads((EVAL / "goldset.json").read_text(encoding="utf-8"))
TOP_K = goldset["meta"]["top_k"]
PER_ENGINE = goldset["meta"]["per_engine"]


async def main() -> int:
    print(f"[egress proxy] {orchestrator.detect_egress_proxy() or '(direct)'}")
    out_all = {}
    for q in goldset["queries"]:
        t0 = time.perf_counter()
        try:
            out = await aggregate_search(q["query"], max_results=TOP_K, per_engine=PER_ENGINE)
            err = None
        except Exception as e:  # noqa: BLE001 - 单题失败不影响其余题
            out, err = {}, repr(e)
        dt = time.perf_counter() - t0
        rec = {
            "query_id": q["id"],
            "query": q["query"],
            "latency_s": round(dt, 2),
            "engines_used": out.get("engines_used", []),
            "engines_failed": out.get("engines_failed_or_empty", []),
            "engine_errors": out.get("engine_errors") or {},
            "relevance": out.get("relevance"),
            "results": [
                {k: r.get(k) for k in ("title", "url", "snippet", "published_date", "engines")}
                for r in out.get("results", [])[:TOP_K]
            ],
            "error": err,
        }
        out_all[q["id"]] = rec
        (RAW / f"agg_{q['id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{q['id']:>3} {dt:6.2f}s  n={len(rec['results'])}  "
              f"used={len(rec['engines_used'])} failed={rec['engines_failed']}  {q['query'][:30]}")
        await asyncio.sleep(1)  # 温和限速

    (RAW / "agg_results.json").write_text(
        json.dumps(out_all, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved -> {RAW / 'agg_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
