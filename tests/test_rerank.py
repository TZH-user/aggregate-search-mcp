"""语义重排测试:余弦相似度 / 重排方向 / 优雅降级 / 池上限 / LLM 档解析与降级。"""

import pytest

from aggregate_search.models import SearchResult
from aggregate_search.rerank import _cos, apply_llm_rerank, apply_rerank, parse_llm_order


def _mk(title, score, url="https://x.com"):
    return SearchResult(title=title, url=url, score=score)


def test_cos_basic():
    assert _cos([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cos([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cos([1, 0], [-1, 0]) == pytest.approx(-1.0)


async def test_rerank_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGG_RERANK", raising=False)
    rs = [_mk("a", 2.0), _mk("b", 1.0)]
    out, stats = await apply_rerank("q", rs, embed_fn=None)
    assert [r.title for r in out] == ["a", "b"]
    assert stats["modes"] == [] and stats["embedding"] is None and stats["llm"] is None


async def test_rerank_semantics_beats_word_overlap():
    # b 基础分更高(词面撞词),但语义相似度低;a 同主题相似度高 → 重排后反超
    # 向量按结果列表位置对应:第1条(时尚页)与 query 正交,第2条(MCP)同向
    async def embed_fn(texts):
        assert texts[0] == "model context protocol news"
        return [[1.0, 0.0], [0.2, 0.98], [0.98, 0.2]]

    rs = [_mk("October 2026 magazine topic", 1.05, url="https://fashion.com"),
          _mk("Model Context Protocol spec", 1.00, url="https://mcp.io")]
    out, stats = await apply_rerank("model context protocol news", rs,
                                    embed_fn=embed_fn, modes=["embedding"])
    assert out[0].url == "https://mcp.io"
    assert stats["embedding"]["embedded"] == 2
    assert stats["embedding"]["error"] is None
    assert stats["embedding"]["sim_max"] > stats["embedding"]["sim_min"]


async def test_rerank_graceful_degradation_on_error():
    async def embed_fn(texts):
        raise RuntimeError("api down")

    rs = [_mk("a", 2.0), _mk("b", 1.0)]
    out, stats = await apply_rerank("q", rs, embed_fn=embed_fn, modes=["embedding"])
    assert [r.title for r in out] == ["a", "b"]  # 原序保留
    assert "RuntimeError" in stats["embedding"]["error"]
    assert stats["embedding"]["embedded"] == 0


async def test_rerank_respects_pool():
    async def embed_fn(texts):
        return [[1.0, 0.0]] + [[1.0, 0.0]] * len(texts[1:])  # 全部同向

    rs = [_mk(f"n{i}", float(10 - i)) for i in range(5)]
    out, stats = await apply_rerank("q", rs, embed_fn=embed_fn, modes=["embedding"], pool=3)
    assert stats["embedding"]["embedded"] == 3
    # 池内分数同乘同因子,顺序不变;池外 2 条原样保留在尾部
    assert [r.title for r in out] == ["n0", "n1", "n2", "n3", "n4"]


async def test_rerank_skips_without_key(monkeypatch):
    monkeypatch.setenv("AGG_RERANK", "embedding")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    rs = [_mk("a", 1.0)]

    out, stats = await apply_rerank("q", rs, embed_fn=None)
    assert len(out) == 1
    assert "no ZHIPU_API_KEY" in stats["embedding"]["error"]
    assert stats["embedding"]["embedded"] == 0


# ---------------------------------------------------------------- LLM 档
def test_parse_llm_order_valid_and_dirty():
    assert parse_llm_order('{"order": [2, 0, 1]}', 3) == [2, 0, 1]
    assert parse_llm_order('好的,结果如下:\n```json\n{"order": [1, 0]}\n```', 2) == [1, 0]
    # 漏项:已排的照用,漏的按原序补尾(RankGPT 式部分排列)
    assert parse_llm_order('{"order": [0, 1]}', 3) == [0, 1, 2]
    assert parse_llm_order('{"order": [2, 0]}', 3) == [2, 0, 1]
    # 重复下标只取一次
    assert parse_llm_order('{"order": [0, 0, 1]}', 3) == [0, 1, 2]
    # 越界下标忽略
    assert parse_llm_order('{"order": [0, 1, 9]}', 3) == [0, 1, 2]
    # 完全解析不出 → 拒绝
    assert parse_llm_order('没有可解析的内容', 3) is None
    assert parse_llm_order('{"order": [5, 9]}', 3) is None  # 全部越界


async def test_llm_rerank_reorders_by_intent():
    async def chat_fn(prompt):
        assert "apple fruit" in prompt
        return '{"order": [1, 0]}'  # 第1条结果被判为更相关

    rs = [_mk("Apple Inc. stock price", 2.0, url="https://apple.com"),
          _mk("Apple fruit nutrition", 1.0, url="https://health.com")]
    out, stats = await apply_llm_rerank("apple fruit nutrition", rs,
                                        chat_fn=chat_fn, enabled=True)
    assert out[0].url == "https://health.com"
    assert stats["reranked"] == 2 and stats["error"] is None


async def test_llm_rerank_degrades_on_bad_output():
    async def chat_fn(prompt):
        return '抱歉,我无法完成该任务。'  # 无 JSON

    rs = [_mk("a", 2.0), _mk("b", 1.0)]
    out, stats = await apply_llm_rerank("q", rs, chat_fn=chat_fn, enabled=True)
    assert [r.title for r in out] == ["a", "b"]  # 原序保留
    assert "unparseable" in stats["error"]


async def test_llm_rerank_degrades_on_api_error():
    async def chat_fn(prompt):
        raise RuntimeError("429 rate limited")

    rs = [_mk("a", 2.0), _mk("b", 1.0)]
    out, stats = await apply_llm_rerank("q", rs, chat_fn=chat_fn, enabled=True)
    assert [r.title for r in out] == ["a", "b"]
    assert "RuntimeError" in stats["error"]


async def test_llm_rerank_single_result_noop():
    async def chat_fn(prompt):
        raise AssertionError("should not call for single result")

    rs = [_mk("only", 1.0)]
    out, stats = await apply_llm_rerank("q", rs, chat_fn=chat_fn, enabled=True)
    assert len(out) == 1 and stats["reranked"] == 0


async def test_llm_rerank_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGG_RERANK", raising=False)
    rs = [_mk("a", 2.0), _mk("b", 1.0)]

    async def chat_fn(prompt):
        raise AssertionError("should not be called when disabled")

    out, stats = await apply_llm_rerank("q", rs, chat_fn=chat_fn)
    assert stats["enabled"] is False and [r.title for r in out] == ["a", "b"]


async def test_combined_modes_llm_after_embedding(monkeypatch):
    """embedding,llm 叠加:embedding 先调分数,LLM 再整体排序。"""
    async def embed_fn(texts):
        return [[1.0, 0.0]] + [[0.99, 0.1]] * len(texts[1:])  # 池内同向,分数同乘

    async def chat_fn(prompt):
        return '{"order": [1, 0]}'

    rs = [_mk("first", 2.0), _mk("second", 1.5)]
    out, stats = await apply_rerank("q", rs, embed_fn=embed_fn, chat_fn=chat_fn,
                                    modes=["embedding", "llm"])
    assert stats["embedding"]["embedded"] == 2 and stats["llm"]["reranked"] == 2
    assert [r.title for r in out] == ["second", "first"]
