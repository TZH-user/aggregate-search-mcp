"""端到端启动验证:用 MCP stdio 客户端真实连接 server.py。

验证 1) initialize/list_tools 握手 2) engine_status 调用
3) web_search 无 key 时的降级提示(不出网)。
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = r"F:\MCP\aggregate-search\server.py"
PY = r"F:\MCP\aggregate-search\.venv\Scripts\python.exe"


async def main() -> int:
    params = StdioServerParameters(command=PY, args=[SERVER], cwd=r"F:\MCP\aggregate-search")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("TOOLS:", names)
            assert "web_search" in names and "engine_status" in names, "tool list mismatch"

            status = await session.call_tool("engine_status", {})
            print("ENGINE_STATUS:", status.structuredContent)

            # 不配 key 时 web_search 应返回引导性错误,而不是崩溃
            empty_env = {k: "" for k in (
                "BOCHA_API_KEY", "ZHIPU_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY")}
            params2 = StdioServerParameters(
                command=PY, args=[SERVER], cwd=r"F:\MCP\aggregate-search", env=empty_env)
            async with stdio_client(params2) as (r2, w2):
                async with ClientSession(r2, w2) as s2:
                    await s2.initialize()
                    res = await s2.call_tool("web_search", {"query": "test"})
                    payload = res.structuredContent or {}
                    print("NO_KEY_RESULT:", payload)
                    assert "error" in payload, "expected friendly error without keys"
    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
