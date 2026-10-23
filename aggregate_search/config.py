"""引擎注册与权重配置。

Key 全部走环境变量(.env / 系统环境)。没配 key 的引擎自动禁用;
各引擎可再用 AGG_ENABLE_<NAME>=0/1 强制开关。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class EngineSpec:
    name: str
    module: str  # engines 子模块名
    cls_name: str  # 适配器类名
    env_key: str  # API key 环境变量名,None 表示无需 key
    zh_weight: float
    en_weight: float
    base_url_env: str | None = None  # 自定义服务地址的环境变量名(如 SearXNG)
    domestic: bool = False  # 国内直连引擎:不走代理客户端


def _enabled_by_env(name: str) -> bool:
    return os.getenv(f"AGG_ENABLE_{name.upper()}", "1") not in ("0", "false", "no")


SPECS: list[EngineSpec] = [
    EngineSpec("bocha", "bocha", "BochaEngine", "BOCHA_API_KEY", 1.3, 0.7, domestic=True),
    EngineSpec("zhipu", "zhipu", "ZhipuEngine", "ZHIPU_API_KEY", 1.3, 0.7, domestic=True),
    EngineSpec("tavily", "tavily", "TavilyEngine", "TAVILY_API_KEY", 0.8, 1.3),
    EngineSpec("serper", "serper", "SerperEngine", "SERPER_API_KEY", 0.9, 1.3),
    EngineSpec("searxng", "searxng", "SearXNGEngine", None, 1.0, 1.0, "SEARXNG_BASE_URL"),
]


def weights_map() -> dict[str, dict[str, float]]:
    return {
        s.name: {"zh": s.zh_weight, "en": s.en_weight}
        for s in SPECS
    }


def available_specs() -> list[EngineSpec]:
    """返回已配置(有 key / 有 base_url)且未被显式禁用的引擎定义。"""
    out = []
    for s in SPECS:
        if not _enabled_by_env(s.name):
            continue
        if s.env_key and not os.getenv(s.env_key):
            continue
        if s.base_url_env and not os.getenv(s.base_url_env):
            continue
        out.append(s)
    return out


def status_report() -> list[dict[str, object]]:
    """engine_status 工具用的诊断信息。"""
    rows = []
    for s in SPECS:
        has_key = bool(os.getenv(s.env_key)) if s.env_key else bool(os.getenv(s.base_url_env))
        rows.append({
            "name": s.name,
            "key_configured": has_key,
            "explicitly_disabled": not _enabled_by_env(s.name),
            "active": s in available_specs(),
            "weights": {"zh": s.zh_weight, "en": s.en_weight},
        })
    return rows
