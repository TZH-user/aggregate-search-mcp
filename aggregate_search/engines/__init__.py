"""引擎适配器集合。"""

from __future__ import annotations

import importlib

from .base import SearchEngine


def build_engine(spec_name: str, client, api_key: str = "", base_url: str = "") -> SearchEngine:
    mod = importlib.import_module(f".{spec_name}", __package__)
    cls = getattr(mod, {
        "bocha": "BochaEngine",
        "zhipu": "ZhipuEngine",
        "tavily": "TavilyEngine",
        "serper": "SerperEngine",
        "searxng": "SearXNGEngine",
    }[spec_name])
    return cls(client, api_key=api_key, base_url=base_url)
