"""过拟合对照实验:全新保留题(不参与过滤器调参)上,关/开相关性过滤器各跑一遍。

设计:
- 4 道歧义陷阱题(测垃圾清除是否泛化)
- 4 道普通查询(测误杀:过滤器把好结果降权/剔除了没有)
同一进程内先 OFF 后 OFF->ON 两次调用(环境变量在调用时读取,可热切换)。
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

import os  # noqa: E402

from aggregate_search.orchestrator import aggregate_search  # noqa: E402

HOLDOUT = [
    {"id": "H1", "kind": "trap", "query": "Transformers movie 2026 box office", "intent": "变形金刚电影(非ML transformer论文)"},
    {"id": "H2", "kind": "trap", "query": "Go board game rules beginners", "intent": "围棋规则(非golang)"},
    {"id": "H3", "kind": "trap", "query": "mermaid diagram syntax tutorial", "intent": "mermaid图表工具(非神话/电影)"},
    {"id": "H4", "kind": "trap", "query": "苹果公司 最新股价", "intent": "Apple公司股票(非水果)"},
    {"id": "H5", "kind": "normal", "query": "Rust 安装 教程 windows", "intent": "Rust语言Windows安装(测误杀)"},
    {"id": "H6", "kind": "normal", "query": "HTTP 418 status code meaning", "intent": "418状态码含义(与E1同族但未见)"},
    {"id": "H7", "kind": "normal", "query": "swiftui vs flutter 2026 比较", "intent": "框架对比(测误杀)"},
    {"id": "H8", "kind": "normal", "query": "Django 官方文档", "intent": "Django官方文档(助推是否泛化到未见站点)"},
]


async def main() -> int:
    out = {}
    for h in HOLDOUT:
        rec = {"query": h["query"], "kind": h["kind"], "intent": h["intent"]}
        for tag in ("off", "on"):
            os.environ["AGG_RELEVANCE"] = "0" if tag == "off" else "1"
            t0 = time.perf_counter()
            r = await aggregate_search(h["query"], max_results=5, per_engine=8)
            rec[tag] = {
                "latency_s": round(time.perf_counter() - t0, 2),
                "relevance": r.get("relevance"),
                "results": [{k: x.get(k) for k in ("title", "url")} for x in r.get("results", [])[:5]],
            }
            await asyncio.sleep(1)
        out[h["id"]] = rec
        r_on, r_off = rec["on"]["relevance"], rec["off"]["relevance"]
        print(f"{h['id']} {h['query'][:36]:38} on: drop={r_on['dropped']} down={r_on['downweighted']} boost={r_on['boosted']}")
    Path(ROOT / "eval" / "raw" / "holdout_ab.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved -> eval/raw/holdout_ab.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
