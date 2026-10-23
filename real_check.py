"""真实 key 端到端验证(会实际调用搜索引擎、消耗配额)。

跑两条 query:中文(博查/智谱权重高)+ 英文(tavily/serper 权重高),
检查 engines_used / engines_failed 与结果数量。key 从 .env 读取。
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")

from aggregate_search import orchestrator  # noqa: E402
from aggregate_search.orchestrator import aggregate_search  # noqa: E402

# 代理探测已下沉到 orchestrator(server 侧同样生效),这里只做展示
print(f"[egress proxy] {orchestrator.detect_egress_proxy() or '(direct)'}")


async def main() -> int:
    failures = 0
    for query in ("Transformer 注意力机制 原理", "best web search API 2026"):
        out = await aggregate_search(query, max_results=5, per_engine=5)
        used = out.get("engines_used", [])
        failed = out.get("engines_failed_or_empty", [])
        n = len(out.get("results", []))
        print(f"\n=== {query!r}")
        print(f"lang={out.get('language')} used={used} failed={failed} results={n}")
        for name, info in (out.get("engine_errors") or {}).items():
            print(f"  ! {name}: {info['kind']} (status={info['status']}) {info['hint']}")
            print(f"    detail: {str(info['detail'])[:160]}")
        for r in out.get("results", [])[:3]:
            print(f"  [{','.join(r['engines'])}] {r['title'][:60]}  {r['url'][:70]}")
        if not used:
            failures += 1
    print("\nREAL CHECK " + ("FAILED" if failures else "OK"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
