# aggregate-search — Multi-Engine Search MCP Server

<p align="center">
  <em>One <code>web_search</code> MCP tool over five search engines: parallel dispatch → URL-normalized dedup → weighted RRF fusion → top-K, with language-aware weighting and automatic overseas-engine failover.</em>
</p>

## Overview

aggregate-search turns multiple search backends — **Bocha / Zhipu (China-friendly)**, **Tavily / Serper (overseas)** and an optional self-hosted **SearXNG** — into a single MCP tool pair (`web_search`, `engine_status`) served over stdio with FastMCP. Chinese queries automatically boost domestic engines (weight 1.3) and English queries boost overseas ones; when an overseas engine is unreachable it lands in a 300 s health blacklist and the request falls back to domestic sources instead of timing out.

> 🇨🇳 中文版说明见 [README_CN.md](README_CN.md)

## Highlights

- 🔀 **Weighted RRF fusion** — `score = Σ w/(60+rank)` with a cross-engine hit bonus (+0.25), so results confirmed by multiple engines rank higher; each result carries an `engines` field listing its sources
- 🩺 **Health blacklist & failure taxonomy** — failing engines are benched for 300 s; every failure is classified (`quota_exhausted` / `auth_failed` / `network_unreachable` / `timeout` / `rate_limited` / `server_error`) with an actionable hint in `engine_errors`
- 🌐 **Proxy auto-negotiation** — overseas engines go through `AGG_PROXY` (env → common Clash ports → Windows registry proxy), validated by a real CONNECT + TLS handshake before use, with automatic direct-retry fallback; domestic engines always connect directly
- 🧪 **35 offline tests + stdio E2E + real-quota check** — pytest suite (fusion / engines / orchestrator / failure classification) runs without network or quota; `verify_e2e.py` drives the server through a real MCP stdio client; `real_check.py` exercises live keys
- 🧩 **Pluggable engines** — add one adapter file + one `config.SPECS` registration; per-engine kill switch via `AGG_ENABLE_<NAME>=0`

## Tech Stack

<div>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB"/>
  <img src="https://img.shields.io/badge/MCP-1.28%20FastMCP-7C4DFF"/>
  <img src="https://img.shields.io/badge/httpx-async-1F6FEB"/>
  <img src="https://img.shields.io/badge/RRF-fusion_k60-0F9D58"/>
  <img src="https://img.shields.io/badge/pytest-35_tests-6DA55F"/>
</div>

## Architecture

```
             query (zh/en auto-detected)
                      │
        ┌──────── orchestrator ────────┐
        │  parallel dispatch + timeouts│
        ▼                              ▼
  domestic engines              overseas engines
  Bocha · Zhipu                 Tavily · Serper   (optional SearXNG)
  always direct                 via validated proxy or direct
        │                              │
        └────────── fusion ────────────┘
      URL normalization → dedup → weighted RRF (k=60)
      + cross-engine bonus → top-K + engine_errors
                      │
                      ▼
        MCP tools: web_search / engine_status (stdio)
```

## Getting Started

```bash
bash setup.sh                                   # uv venv (Python 3.12) + deps
cp .env.example .env                            # fill at least one engine key
.venv/Scripts/python.exe -m pytest -q           # 35 offline tests (no network)
.venv/Scripts/python.exe verify_e2e.py          # stdio end-to-end (mocked)
.venv/Scripts/python.exe real_check.py          # live quota check
```

Register with any MCP client (ZCode / Claude Code etc.):

```json
"aggregate-search": {
  "type": "stdio",
  "command": "<abs-path>/.venv/Scripts/python.exe",
  "args": ["<abs-path>/server.py"],
  "cwd": "<abs-path>"
}
```

No key ships with this repository — obtain your own: **Bocha** <https://open.bochaai.com> (CN, 1000 free), **Zhipu** <https://open.bigmodel.cn>, **Tavily** <https://app.tavily.com> (1000/month free), **Serper** <https://serper.dev>, or point `SEARXNG_BASE_URL` at a self-hosted instance (no key). See `.env.example`.

## Configuration

- `BOCHA_API_KEY` / `ZHIPU_API_KEY` / `TAVILY_API_KEY` / `SERPER_API_KEY` / `SEARXNG_BASE_URL` — all optional; whichever is set gets used
- `AGG_PROXY` — egress proxy for overseas engines only; empty = env → probe ports 7897/7890/10809 → Windows registry proxy
- `AGG_ENABLE_<NAME>=0` — force-disable an engine; `AGG_TIMEOUT_S` (20) / `AGG_CONNECT_TIMEOUT_S` (10) / `AGG_UNHEALTHY_TTL_S` (300)
- `ZHIPU_SEARCH_ENGINE` — `search_std` (default) / `search_pro` / `search_pro_sogou` / `search_pro_quark`

## Evaluation

A frozen 14-query gold set across 5 dimensions (CN/EN freshness, authoritative-source locating, off-topic resistance, precise facts), each result scored 0/1/2 against the built-in WebSearch baseline; plus an 8-query hold-out set (never used for tuning) to check filter over-fitting. Methodology, per-item scoring evidence and raw engine outputs are all kept in [eval/](eval/).

| Metric (v6) | aggregate-search | built-in WebSearch |
|---|---|---|
| Gold Hit@5 (11 applicable) | 11/11 | 11/11 |
| MRR | 0.848 | 1.000 |
| Junk results (score=0) | 0/70 | 0/62 |
| Result coverage | 70/70 | 62/70 |
| Latency | mean 2.7 s / median 2.6 s | not measured |

- v1→v6 evolution (coverage filter → per-engine cap & dedup fixes → semantic re-rank → near-duplicate merge): junk 5/70 → 0/70, Hit@5 10/11 → 11/11 — [eval/report.md](eval/report.md)
- Hold-out A/B: with the filter on, junk 5/40 → 0/40 and zero false kills on normal queries — [eval/holdout_report.md](eval/holdout_report.md)

## Repository Layout

```
server.py              MCP entry (stdio): web_search / engine_status
aggregate_search/
  config.py            engine registry + zh/en weights + key detection
  models.py            unified SearchResult model
  fusion.py            URL normalization, dedup, weighted RRF, cross-source bonus
  orchestrator.py      parallel dispatch, health blacklist, failure taxonomy, proxy probing
  engines/             per-engine adapters (add file + register in config.SPECS)
tests/                 35 mocked tests (fusion / engines / orchestrator / failures)
eval/                  frozen gold set, per-version scores, hold-out report, raw outputs
verify_e2e.py          stdio E2E via a real MCP client
real_check.py          live-key verification (spends quota, prints failure kinds)
HANDOFF_接管诊断.md     engineering diagnosis report (CN)
.env.example           key template
```

## Known Limitations

- Engine adapters follow official docs; after API changes only the adapter file needs updating
- Fusion is weighted RRF + cross-source bonus in v1; LLM re-ranking is planned
- Overseas engines need a working egress proxy; on machines without one, disable them via `AGG_ENABLE_<NAME>=0` to skip cold-start timeouts

## License

**MIT** © 2026 TZH-user — see [LICENSE](LICENSE).
