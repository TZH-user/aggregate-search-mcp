"""统一结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """单个搜索结果。各引擎适配器负责把自家响应映射到该结构。"""

    title: str
    url: str
    snippet: str = ""
    content: str = ""  # LLM 友好的清洗正文(Tavily/博查 summary 有,其他引擎可空)
    published_date: str | None = None
    source_name: str | None = None  # 站点名(如媒体名),可空
    engine: str = ""  # 产出该结果的引擎
    rank: int = 0  # 在该引擎结果中的名次(1 起)
    engines_hit: list[str] = field(default_factory=list)  # 融合阶段填充
    score: float = 0.0  # 融合阶段填充

    def merged_payload(self) -> dict[str, object]:
        """对外输出形态。"""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content,
            "published_date": self.published_date,
            "source_name": self.source_name,
            "score": round(self.score, 5),
            "engines": self.engines_hit,
        }
