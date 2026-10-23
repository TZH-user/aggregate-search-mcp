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
    opt_in_env: str | None = None  # 显式开启才启用的环境变量名(免 key 兜底引擎用)


def _enabled_by_env(name: str) -> bool:
    return os.getenv(f"AGG_ENABLE_{name.upper()}", "1") not in ("0", "false", "no")


def _opted_in(spec: EngineSpec) -> bool:
    return os.getenv(spec.opt_in_env, "") in ("1", "true", "yes")


SPECS: list[EngineSpec] = [
    EngineSpec("bocha", "bocha", "BochaEngine", "BOCHA_API_KEY", 1.3, 0.7, domestic=True),
    EngineSpec("zhipu", "zhipu", "ZhipuEngine", "ZHIPU_API_KEY", 1.3, 0.7, domestic=True),
    EngineSpec("tavily", "tavily", "TavilyEngine", "TAVILY_API_KEY", 0.8, 1.3),
    EngineSpec("serper", "serper", "SerperEngine", "SERPER_API_KEY", 0.9, 1.3),
    EngineSpec("searxng", "searxng", "SearXNGEngine", None, 1.0, 1.0, "SEARXNG_BASE_URL"),
    # 免 key 兜底引擎:默认关闭,AGG_ENABLE_DUCKDUCKGO=1 显式开启
    EngineSpec("duckduckgo", "duckduckgo", "DuckDuckGoEngine", None, 0.6, 1.0,
               opt_in_env="AGG_ENABLE_DUCKDUCKGO"),
]


def weights_map() -> dict[str, dict[str, float]]:
    return {
        s.name: {"zh": s.zh_weight, "en": s.en_weight}
        for s in SPECS
    }


def available_specs() -> list[EngineSpec]:
    """返回已配置(有 key / 有 base_url / 已显式开启)且未被显式禁用的引擎定义。"""
    out = []
    for s in SPECS:
        if not _enabled_by_env(s.name):
            continue
        if s.opt_in_env and not _opted_in(s):
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
        if s.opt_in_env:
            # 免 key 兜底引擎:key 恒视为就绪,是否生效只看显式开关
            has_key, note = True, f"opt-in:设 {s.opt_in_env}=1 启用"
            disabled = not _opted_in(s)
        else:
            has_key = bool(os.getenv(s.env_key)) if s.env_key else bool(os.getenv(s.base_url_env))
            note, disabled = "", not _enabled_by_env(s.name)
        rows.append({
            "name": s.name,
            "key_configured": has_key,
            "explicitly_disabled": disabled,
            "active": s in available_specs(),
            "weights": {"zh": s.zh_weight, "en": s.en_weight},
            **({"note": note} if note else {}),
        })
    return rows
