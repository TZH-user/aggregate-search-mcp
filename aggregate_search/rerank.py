"""语义重排(可选):把语义信号融进排序分数。两档:

- embedding 档(`AGG_RERANK=embedding`):智谱 embedding-3 余弦相似度,快(+0.5s)、确定性;
- LLM 档(`AGG_RERANK=llm` 或 "embedding,llm" 叠加):GLM chat 按查询意图对候选整体排序,
  意图理解最强(默认 glm-4-flash 免费),+1~3s、引入提示词敏感性,失败自动降级保序。

动机(2026-10-23 评测):词面覆盖率是必要条件而非语义条件——垃圾页正文真的包含
查询词时(时尚杂志 "October 2026" 专题混入 "MCP news October 2026" 查询)无法区分,
且其相似度分差(实测 cos 0.41 vs 0.80)远大于 RRF 名次分差,是干净有效的第四路信号。

- 只对融合池前 AGG_RERANK_POOL(默认 20)条做重排(一次批量调用);
  任一档失败时优雅降级保持原序——重排是增强,绝不能成为新的单点故障
- embedding 档分数 × (AGG_RERANK_ALPHA + cos),alpha 默认 0.4
"""

from __future__ import annotations

import json
import os
import re

from .models import SearchResult

ZHIPU_EMBEDDINGS_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
ZHIPU_CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


async def _zhipu_embed(texts: list[str], client=None, model: str | None = None) -> list[list[float]]:
    """批量调智谱 embeddings;client 缺省时用调度层的国内直连客户端(测试可注入 mock)。"""
    if client is None:
        from .orchestrator import get_client  # 局部导入避免循环依赖
        client = get_client(domestic=True)
    model = model or os.getenv("AGG_RERANK_MODEL", "embedding-3")
    resp = await client.post(
        ZHIPU_EMBEDDINGS_URL,
        json={"model": model, "input": texts},
        headers={"Authorization": f"Bearer {os.getenv('ZHIPU_API_KEY', '')}"},
    )
    resp.raise_for_status()
    data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
    if len(data) != len(texts):
        raise RuntimeError(f"embedding count mismatch: got {len(data)}, want {len(texts)}")
    return [d["embedding"] for d in data]


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def _result_text(r: SearchResult) -> str:
    return f"{r.title} {r.snippet} {r.content[:500]}".strip()


async def _zhipu_chat(prompt: str, client=None, model: str | None = None) -> str:
    if client is None:
        from .orchestrator import get_client  # 局部导入避免循环依赖
        client = get_client(domestic=True)
    model = model or os.getenv("AGG_RERANK_LLM_MODEL", "glm-4-flash")
    resp = await client.post(
        ZHIPU_CHAT_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        },
        headers={"Authorization": f"Bearer {os.getenv('ZHIPU_API_KEY', '')}"},
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_llm_order(raw: str, n: int) -> list[int] | None:
    """从 LLM 回复解析下标排列。容忍真实模型的两种瑕疵:
    - 漏项:返回其中出现的去重合法下标为前缀,未提及的按原序补尾(LLM 漏提 = 视为靠后);
    - 包裹噪声:从回复中抽取第一个 {...} 片段(兼容 ```json 围栏与说明文字)。
    完全解析不出任何合法下标时返回 None(调用方降级保序)。
    """
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        order = json.loads(m.group(0)).get("order")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(order, list):
        return None
    seen: list[int] = []
    for x in order:
        if isinstance(x, int) and 0 <= x < n and x not in seen:
            seen.append(x)
    if not seen:
        return None
    rest = [i for i in range(n) if i not in seen]
    return seen + rest


_LLM_PROMPT = """你是搜索结果重排序器。给定查询和编号候选(标题+摘要),按与查询意图的相关性从高到低排序。
注意区分同形异义词(如 Apple 公司/水果),只依据语义相关性,不考虑域名偏好。
只输出 JSON,格式:{{"order": [编号...]}}(包含全部 {n} 个编号,从 0 开始)。

查询:{query}

候选:
{candidates}"""


async def apply_llm_rerank(
    query: str,
    results: list[SearchResult],
    *,
    chat_fn=None,
    enabled: bool | None = None,
    pool: int | None = None,
) -> tuple[list[SearchResult], dict[str, object]]:
    """GLM 按意图对池内候选整体排序;失败/解析非法时保持原序(优雅降级)。"""
    stats: dict[str, object] = {"enabled": False, "reranked": 0, "error": None}
    if enabled is None:
        modes = [m.strip() for m in os.getenv("AGG_RERANK", "").split(",")]
        stats["enabled"] = "llm" in modes
    else:
        stats["enabled"] = enabled
    if not stats["enabled"] or len(results) < 2:
        return results, stats
    if chat_fn is None and not os.getenv("ZHIPU_API_KEY"):
        stats["error"] = "no ZHIPU_API_KEY, skip"
        return results, stats

    pool = int(os.getenv("AGG_RERANK_POOL", "20")) if pool is None else pool
    target = results[:pool]
    tail = results[pool:]
    candidates = "\n".join(
        f"{i}. {(r.title or '')[:80]} — {(r.snippet or r.content or '')[:120]}"
        for i, r in enumerate(target)
    )
    prompt = _LLM_PROMPT.format(query=query, candidates=candidates, n=len(target))

    chat = chat_fn or _zhipu_chat
    try:
        raw = await chat(prompt)
    except Exception as exc:  # noqa: BLE001 — 降级保序
        stats["error"] = f"{type(exc).__name__}: {exc}"[:160]
        return results, stats

    order = parse_llm_order(raw, len(target))
    if order is None:
        stats["error"] = f"unparseable order: {raw[:80]}"
        return results, stats

    stats["reranked"] = len(target)
    return [target[i] for i in order] + tail, stats


async def _apply_embedding_rerank(
    query: str,
    results: list[SearchResult],
    *,
    embed_fn=None,
    pool: int | None = None,
    alpha: float | None = None,
) -> tuple[list[SearchResult], dict[str, object]]:
    """embedding 档实现。失败原样返回,绝不抛出。"""
    stats: dict[str, object] = {"enabled": True, "embedded": 0, "error": None,
                                "sim_min": None, "sim_max": None}
    if not results:
        return results, stats
    if embed_fn is None and not os.getenv("ZHIPU_API_KEY"):
        # 只有用默认智谱 embedder 才需要 key;注入的自定义 embed_fn(测试/替代实现)不受限
        stats["error"] = "no ZHIPU_API_KEY, skip"
        return results, stats

    pool = int(os.getenv("AGG_RERANK_POOL", "20")) if pool is None else pool
    alpha = float(os.getenv("AGG_RERANK_ALPHA", "0.4")) if alpha is None else alpha
    target = results[:pool]
    tail = results[pool:]

    embed = embed_fn or _zhipu_embed
    try:
        vecs = await embed([query] + [_result_text(r) for r in target])
    except Exception as exc:  # noqa: BLE001 — 降级保序,重排不能成为单点故障
        stats["error"] = f"{type(exc).__name__}: {exc}"[:160]
        return results, stats

    qv = vecs[0]
    sims = [_cos(qv, v) for v in vecs[1:]]
    for r, sim in zip(target, sims):
        r.score *= alpha + sim
    target.sort(key=lambda r: r.score, reverse=True)

    stats["embedded"] = len(target)
    if sims:
        stats["sim_min"] = round(min(sims), 4)
        stats["sim_max"] = round(max(sims), 4)
    return target + tail, stats


async def apply_rerank(
    query: str,
    results: list[SearchResult],
    *,
    embed_fn=None,
    chat_fn=None,
    modes: list[str] | None = None,
    pool: int | None = None,
    alpha: float | None = None,
) -> tuple[list[SearchResult], dict[str, object]]:
    """语义重排调度器:按 AGG_RERANK(modes)依次施加 embedding / llm 档,可叠加。

    返回 (新列表, {"modes": [...], "embedding": {...}|None, "llm": {...}|None})。
    """
    if modes is None:
        modes = [m.strip().lower() for m in os.getenv("AGG_RERANK", "").split(",") if m.strip()]
    stats: dict[str, object] = {"modes": modes, "embedding": None, "llm": None}
    cur = results
    if "embedding" in modes:
        cur, stats["embedding"] = await _apply_embedding_rerank(
            query, cur, embed_fn=embed_fn, pool=pool, alpha=alpha)
    if "llm" in modes:
        cur, stats["llm"] = await apply_llm_rerank(query, cur, chat_fn=chat_fn,
                                                   enabled=True, pool=pool)
    return cur, stats
