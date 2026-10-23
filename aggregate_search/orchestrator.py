"""调度层:并行调用各引擎 → 融合;失败引擎进入健康黑名单(TTL 内跳过)。

海外引擎在无代理环境下会连接超时,但各引擎并行执行,整体延迟只取决于
最慢的一个;失败后记入黑名单,TTL 内不再浪费时间等它,即实现
"海外优先、不可达自动退回国内源"的策略。
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from . import config
from .engines import build_engine
from .engines.base import SearchEngine
from .fusion import detect_language, fuse
from .models import SearchResult

log = logging.getLogger("aggregate-search")

# 本机出网存在秒级抖动(实测握手 0.1~9.2s),connect 阈值必须能覆盖最慢一档,
# 否则国内源会因 5s 硬上限被误杀;失败仍有 300s 健康黑名单兜底。
PER_ENGINE_TIMEOUT = float(os.getenv("AGG_TIMEOUT_S", "20"))
CONNECT_TIMEOUT = float(os.getenv("AGG_CONNECT_TIMEOUT_S", "10"))
UNHEALTHY_TTL = float(os.getenv("AGG_UNHEALTHY_TTL_S", "300"))
MAX_PER_ENGINE = 15

# 出网代理探测端口(Clash / v2ray 常见混合端口)
PROBE_PORTS = (7897, 7890, 10809)
# 代理可用性验证:用该代理对海外主机做一次 CONNECT + TLS 握手
PROXY_PROBE_HOST = os.getenv("AGG_PROXY_PROBE_HOST", "api.tavily.com")
PROXY_PROBE_TIMEOUT = float(os.getenv("AGG_PROXY_PROBE_TIMEOUT_S", "4"))

# {engine_name: monotonic 失效时间}
_unhealthy: dict[str, float] = {}

_clients: dict[str, httpx.AsyncClient] = {}

# 探测结果缓存:None=未探测,""=探测失败(无代理),其余为代理地址
_egress_proxy: str | None = None

# 代理是间歇性可用的坏代理时(实测 7897 五次探测 F/T/T/F/T),失败后降级直连
_proxy_disabled = False
# 记录"当前走代理"的客户端,用于判断某引擎是否值得用直连重试
_proxy_clients: set[int] = set()


# --------------------------------------------------------------------------
# 出网代理探测
# --------------------------------------------------------------------------
def _windows_system_proxy() -> str | None:
    """读取 Windows 系统代理(httpx 的 trust_env 只读环境变量,不读注册表)。"""
    if os.name != "nt":
        return None
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    if not enabled or not server:
        return None
    server = str(server).strip()
    # 形如 "http=127.0.0.1:7897;https=127.0.0.1:7897" 或纯 "127.0.0.1:7897"
    if "=" in server:
        picked = ""
        for part in server.replace(";", " ").split():
            if "=" in part:
                scheme, addr = part.split("=", 1)
                if scheme.lower() in ("https", "http") and addr:
                    picked = addr if scheme.lower() == "https" else picked or addr
        server = picked
    if not server:
        return None
    return server if "://" in server else f"http://{server}"


def _proxy_usable(proxy: str) -> bool:
    """验证代理能否真正完成 HTTPS 隧道(TLS 握手),而不只是接受 CONNECT。

    实测:本机 127.0.0.1:7897 会接受 CONNECT 却在 TLS 阶段掐断
    (SSL UNEXPECTED_EOF_WHILE_READING),而海外源**直连反而可用**。
    若不加这一步验证,探测到"坏代理"会让所有海外引擎一律 network_unreachable。
    """
    import ssl
    import urllib.parse

    try:
        parsed = urllib.parse.urlparse(proxy if "://" in proxy else f"http://{proxy}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
    except ValueError:
        return False

    import socket

    try:
        s = socket.create_connection((host, port), timeout=PROXY_PROBE_TIMEOUT)
    except OSError:
        return False
    try:
        s.sendall(
            f"CONNECT {PROXY_PROBE_HOST}:443 HTTP/1.1\r\n"
            f"Host: {PROXY_PROBE_HOST}:443\r\n\r\n".encode()
        )
        resp = s.recv(256).decode("latin-1", "replace")
        if " 200 " not in resp.split("\r\n")[0]:
            return False
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(s, server_hostname=PROXY_PROBE_HOST):
            return True
    except Exception:  # noqa: BLE001 — 任何握手异常都视为代理不可用
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def detect_egress_proxy() -> str | None:
    """按优先级确定海外引擎出网代理:AGG_PROXY → 环境变量 → 常见端口探测 → Windows 系统代理。

    国内引擎不走这里(实测:博查经代理会 SSL 握手失败,必须直连)。
    """
    global _egress_proxy
    if _egress_proxy is not None:
        return _egress_proxy or None

    found = ""
    explicit = (os.getenv("AGG_PROXY") or "").strip()
    if explicit:
        found = explicit
    else:
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            val = (os.getenv(var) or "").strip()
            if val:
                found = val
                break
    if not found:
        import socket

        for port in PROBE_PORTS:
            s = socket.socket()
            s.settimeout(0.4)
            try:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    found = f"http://127.0.0.1:{port}"
                    break
            except OSError:
                pass
            finally:
                s.close()
    if not found:
        found = _windows_system_proxy() or ""

    # 关键:探测到 ≠ 能用。坏代理会让所有海外引擎直接躺平,不如直连。
    if found and not _proxy_usable(found):
        log.warning("egress proxy %s unusable (TLS handshake failed) -> fallback to direct", found)
        found = ""

    _egress_proxy = found
    log.info("egress proxy: %s", found or "(direct)")
    return found or None


def get_client(domestic: bool = False) -> httpx.AsyncClient:
    """国内引擎强制直连(代理出口 IP 可能被国内源拒绝);
    海外引擎优先用 AGG_PROXY,否则读系统代理环境变量,再否则直连。"""
    key = "direct" if domestic else "egress"
    if key not in _clients or _clients[key].is_closed:
        if domestic:
            _clients[key] = httpx.AsyncClient(
                timeout=httpx.Timeout(PER_ENGINE_TIMEOUT, connect=CONNECT_TIMEOUT),
                headers={"User-Agent": "aggregate-search-mcp/0.1"},
                trust_env=False,
            )
        else:
            if _proxy_disabled:
                proxy, trust_env = None, False
            else:
                proxy, trust_env = detect_egress_proxy(), True
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(PER_ENGINE_TIMEOUT, connect=CONNECT_TIMEOUT),
                headers={"User-Agent": "aggregate-search-mcp/0.1"},
                trust_env=trust_env,
                proxy=proxy,
            )
            if proxy:
                _proxy_clients.add(id(client))
            _clients[key] = client
    return _clients[key]


async def _call(engine: SearchEngine, query: str, count: int, freshness) -> list[SearchResult]:
    results = await engine.search(query, count, freshness)
    for r in results:
        r.engine = engine.name
    return results


def _disable_egress_proxy() -> None:
    """海外引擎调用失败后关掉代理,后续请求一律直连。"""
    global _proxy_disabled
    if not _proxy_disabled:
        _proxy_disabled = True
        _clients.pop("egress", None)
        log.warning("egress proxy failed -> overseas engines fall back to direct")


def _healthy(name: str) -> bool:
    until = _unhealthy.get(name)
    return until is None or time.monotonic() >= until


def mark_unhealthy(name: str) -> None:
    _unhealthy[name] = time.monotonic() + UNHEALTHY_TTL
    log.info("engine %s marked unhealthy for %ss", name, UNHEALTHY_TTL)


def mark_healthy(name: str) -> None:
    _unhealthy.pop(name, None)


def health_snapshot() -> dict[str, bool]:
    return {s.name: _healthy(s.name) for s in config.SPECS}


def _select_engines(only: str | None) -> list[SearchEngine]:
    specs = config.available_specs()
    if only:
        wanted = {x.strip().lower() for x in only.split(",") if x.strip()}
        specs = [s for s in specs if s.name in wanted]
    engines = []
    for s in specs:
        if not _healthy(s.name):
            continue
        kwargs: dict = {"api_key": os.getenv(s.env_key, "") if s.env_key else ""}
        if s.base_url_env:
            kwargs["base_url"] = os.getenv(s.base_url_env, "")
        engines.append(build_engine(s.name, get_client(domestic=s.domestic), **kwargs))
    return engines


# 失败分类 → 可操作提示(之前所有失败对外都只是一个引擎名,无法自查)
_FAILURE_HINT = {
    "quota_exhausted": "配额/余额耗尽,去控制台购买或领取资源包",
    "auth_failed": "API key 无效或过期,核对 .env 中的 key",
    "network_unreachable": "网络不可达(海外源通常需代理),检查 AGG_PROXY 或用 AGG_ENABLE_<NAME>=0 禁用",
    "timeout": "请求超时,可加大 AGG_TIMEOUT_S 或检查网络",
    "rate_limited": "触发限流,稍后重试",
    "server_error": "搜索引擎服务端错误,非本地问题",
    "http_error": "HTTP 异常,见 detail",
    "unknown": "未知错误,见 detail",
}


def classify_failure(exc: BaseException) -> dict[str, object]:
    """把引擎异常归类为结构化信息:区分配额耗尽 / key 无效 / 网络不通 / 超时。"""
    status = None
    body = ""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            body = (exc.response.text or "")[:200]
        except Exception:  # noqa: BLE001 — 读响应体失败不影响分类
            body = ""
    low = body.lower()
    quota_words = ("quota", "package", "not enough", "insufficient", "余额", "配额", "欠费", "arrears")

    if isinstance(exc, httpx.HTTPStatusError):
        if status in (401, 403):
            kind = "quota_exhausted" if any(w in low for w in quota_words) else "auth_failed"
        elif status == 429:
            kind = "rate_limited"
        elif status is not None and status >= 500:
            kind = "server_error"
        else:
            kind = "http_error"
    elif isinstance(exc, httpx.TimeoutException):
        kind = "timeout"
    elif isinstance(exc, httpx.ConnectError):
        kind = "network_unreachable"
    elif isinstance(exc, httpx.HTTPError):
        kind = "http_error"
    else:
        kind = "unknown"

    return {
        "kind": kind,
        "status": status,
        "detail": body or f"{type(exc).__name__}: {exc}"[:200],
        "hint": _FAILURE_HINT.get(kind, ""),
    }


def all_failed_hint(errors: dict[str, dict]) -> str:
    if not errors:
        return "所有引擎均未返回结果"
    parts = [
        f"{name}: {info.get('kind')} — {_FAILURE_HINT.get(info.get('kind'), '')}"
        for name, info in errors.items()
    ]
    return "全部引擎调用失败 → " + "; ".join(parts)


async def _run_one(engine: SearchEngine, query: str, count: int, freshness):
    """返回 (results, failure_info);failure_info 为 None 表示成功。

    海外引擎当前若走代理且失败,自动降级为直连重试一次——
    实测本机 7897 属于间歇性坏代理(接受 CONNECT 却在 TLS 阶段掐断,
    五次探测 F/T/T/F/T),而海外源直连反而可用。坏代理比没代理更糟。
    """
    try:
        mark_healthy(engine.name)
        return await _call(engine, query, count, freshness), None
    except Exception as exc:  # noqa: BLE001 — 单引擎失败只影响自己
        spec = next((s for s in config.SPECS if s.name == engine.name), None)
        if spec and not spec.domestic and id(engine.client) in _proxy_clients:
            _disable_egress_proxy()
            engine.client = get_client(domestic=False)
            try:
                mark_healthy(engine.name)
                return await _call(engine, query, count, freshness), None
            except Exception as exc2:  # noqa: BLE001
                exc = exc2
        mark_unhealthy(engine.name)
        info = classify_failure(exc)
        log.warning("engine %s failed [%s]: %s", engine.name, info["kind"], str(info["detail"])[:120])
        return [], info


async def aggregate_search(
    query: str,
    max_results: int = 10,
    per_engine: int = 8,
    engines: str | None = None,
    freshness: str | None = None,
) -> dict[str, object]:
    import anyio

    query = (query or "").strip()
    if not query:
        return {"error": "query 不能为空"}

    engine_list = _select_engines(engines)
    if not engine_list:
        return {
            "error": "没有可用引擎:请检查 .env 中的 API key 配置(参考 .env.example),"
                     "或用 engine_status 查看各引擎状态",
        }

    per_engine = max(3, min(int(per_engine), MAX_PER_ENGINE))
    lang = detect_language(query)

    runs = list(await anyio.gather(*[
        _run_one(e, query, per_engine, freshness) for e in engine_list
    ]))
    batches = [r[0] for r in runs]

    fused = fuse(batches, lang=lang, weights=config.weights_map())[:max_results]

    used = [e.name for e, (b, _) in zip(engine_list, runs) if b]
    failed = [e.name for e, (b, _) in zip(engine_list, runs) if not b]
    errors = {e.name: info for e, (_, info) in zip(engine_list, runs) if info}

    out: dict[str, object] = {
        "query": query,
        "language": lang,
        "engines_used": used,
        "engines_failed_or_empty": failed,
        "engine_errors": errors,  # {引擎名: {kind, status, detail, hint}}
        "unhealthy_cache": {k: v for k, v in health_snapshot().items() if not v},
        "results": [r.merged_payload() for r in fused],
    }
    if not used:
        # 全部失败时把原因直接摆在返回值里,不再只给一个引擎名
        out["error"] = all_failed_hint(errors)
    return out
