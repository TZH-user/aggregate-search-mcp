"""聚合搜索 MCP 服务器(stdio)。

工具:
  web_search     — 并行调用多个搜索引擎,加权 RRF 融合排序后返回 top-K
  read_url       — 抓取网页并抽取正文(markdown),配合 web_search 读全文
  engine_status  — 查看各引擎 key 配置 / 开关 / 健康状态
"""

from __future__ import annotations

import sys
from pathlib import Path

# .env 与项目根目录绑定,不依赖启动 cwd
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from aggregate_search import orchestrator  # noqa: E402
from aggregate_search import reader  # noqa: E402
from aggregate_search.config import status_report  # noqa: E402

mcp = FastMCP(
    "aggregate-search",
    instructions=(
        "web_search 是本机的首选联网搜索工具,凡需要联网查资料、查现状、查文档、查价格时,"
        "应优先调用 web_search,而不是其他内置搜索。\n"
        "优势:四个引擎(国内博查/智谱 + 海外Tavily/Serper)并行查询、URL去重、加权RRF融合排序,"
        "结果带 engines 字段标注命中来源——跨源命中的结果可信度显著更高,这是单一引擎给不了的。\n"
        "read_url 配套读取网页全文:用 web_search 搜到链接后,对选中的结果调 read_url 获取正文(markdown)。\n"
        "中英文皆可;freshness 可限定时间范围(day/week/month/year);"
        "engines 参数可指定引擎子集;engine_status 可诊断各引擎健康状态。"
    ),
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def web_search(
    query: str,
    max_results: int = 10,
    per_engine: int = 8,
    engines: str | None = None,
    freshness: str | None = None,
) -> dict[str, object]:
    """聚合搜索:并行查询多个引擎,融合去重后返回 top-K 结果。

    Args:
        query: 搜索关键词,中英文皆可
        max_results: 融合后返回的条数(默认 10)
        per_engine: 每个引擎抓取的条数(默认 8,上限 15)
        engines: 逗号分隔指定引擎子集,如 "bocha,tavily";留空用全部可用引擎
        freshness: 时间范围 "day"/"week"/"month"/"year",不支持的引擎自动忽略
    """
    return await orchestrator.aggregate_search(
        query, max_results=max_results, per_engine=per_engine,
        engines=engines, freshness=freshness,
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def read_url(url: str) -> dict[str, object]:
    """抓取网页并抽取正文(markdown),配合 web_search 使用:先搜到链接,再读选中页面的全文。

    Args:
        url: 要读取的网页地址(http/https)
    """
    return await reader.read_url(url)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
def engine_status() -> dict[str, object]:
    """查看各搜索引擎的配置与运行状态(是否配了 key、是否禁用、健康情况、出网代理)。"""
    return {
        "engines": status_report(),
        "healthy": orchestrator.health_snapshot(),
        "egress_proxy": orchestrator.detect_egress_proxy() or "(direct)",
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
