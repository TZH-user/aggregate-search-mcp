# aggregate-search 接管诊断报告

- **工程位置**:`F:\MCP\aggregate-search`(两个目录中唯一的搜索类工程,已排除 graphify / proj-clone-skill / instrument-control-skills 等无关项)
- **诊断日期**:2026-10-20
- **诊断方式**:离线测试套件 + MCP stdio 端到端 + 真实 key 全链路调用 + 逐层网络连通性对照
- **一句话结论**:**代码本体已完工且测试全绿,但"从未接通过真实世界"——当前 5 个引擎无一可用,真实调用返回 0 条结果。烂尾在"供给与验收",不在"代码"。**

---

## 1. 工程是什么

把博查 / 智谱(国内)、Tavily / Serper(海外)、自托管 SearXNG 五个搜索源聚合成一个 `web_search` MCP 工具:
并行查询 → URL 规范化去重 → 加权 RRF 融合排序 → 返回 top-K。中文 query 自动提升国内源权重,英文反之;
海外源不可达时进入 300s 健康黑名单,自动退回国内源。

| 组件 | 文件 | 状态 |
|---|---|---|
| MCP 入口(stdio) | `server.py` | ✅ 完整,暴露 `web_search` / `engine_status` |
| 引擎注册表 + 权重 | `aggregate_search/config.py` | ✅ 完整 |
| 结果模型 | `aggregate_search/models.py` | ✅ 完整 |
| URL 规范化 + 加权 RRF 融合 | `aggregate_search/fusion.py` | ✅ 实现正确(k=60,跨源加分 0.25) |
| 并行调度 + 健康黑名单 | `aggregate_search/orchestrator.py` | ✅ 完整 |
| 5 个引擎适配器 | `aggregate_search/engines/*.py` | ⚠️ 照官方文档实现,**从未实测** |
| 测试 | `tests/`(22 项 mock) | ✅ 22/22 通过 |
| 端到端验证 | `verify_e2e.py` | ✅ E2E OK |
| 真实验收 | `real_check.py` | ❌ **FAILED,0 结果** |

已注册进 `~/.zcode/cli/config.json` 的 `mcp.servers.aggregate-search`(stdio,指向工程自带 `.venv`),配置本身无误。

---

## 2. 实测证据链

| # | 验证动作 | 是否出网 | 结果 |
|---|---|---|---|
| 1 | `pytest -q` | 否 | **22 passed in 0.38s**,`lastfailed` 为空 |
| 2 | `verify_e2e.py`(MCP stdio 客户端) | 否 | **E2E OK**,`TOOLS: ['web_search','engine_status']` |
| 3 | `real_check.py`(真实 key) | 是 | **REAL CHECK FAILED**:`used=[] failed=['bocha','tavily','serper'] results=0` |
| 4 | 逐层网络诊断(TCP / TLS / 代理 CONNECT) | 半 | 直连 TLS 大多 OK;经 `127.0.0.1:7897` 代理后 TLS 被掐断(`SSLEOFError`) |
| 5 | DNS + HTTP 对照 | 是 | `api.bochaai.com` 直连与经代理**均返回 HTTP 404** → 网络层可达;`api.tavily.com` 两通道均"基础连接已关闭" → 海外源不可达 |
| 6 | 博查 API 单次真实调用 | 是 | **HTTP 403** — `{"code":"403","message":"You do not have enough money or package quota"}` |

**第 6 项是根因定位的关键**:博查服务器正常响应了业务错误码,说明链路完全打通,失败原因是 **key 配额耗尽**,而非代码或网络。

---

## 3. 烂尾根因(三层)

### A. 供给层(主因)—— 没有一个是可用引擎

| 引擎 | key | 网络 | 判定 |
|---|---|---|---|
| 博查 bocha | 已填(35 字符) | ✅ 可达 | ❌ **配额耗尽(403)** |
| 智谱 zhipu | ❌ 未填 | 未测 | ❌ 未启用 |
| Tavily | 已填 | ❌ 不可达 | ❌ 无解(除非换网络) |
| Serper | 已填 | ❌ 不可达 | ❌ 无解(除非换网络) |
| SearXNG | 免 key | — | ❌ 未配置实例 |

→ 由于 5 个源全部不可用,`web_search` 永远返回 `没有可用引擎`。这正是"开发完却没人用"的直接原因。

### B. 网络层 —— 海外源在本机实际出不去

- 系统代理 `ProxyEnable=1`、指向 `127.0.0.1:7897`,端口确实在监听;
- 但经该代理发起 TLS 时握手被中断(`UNEXPECTED_EOF_WHILE_READING`),Tavily / Serper 因此**必失败**;
- 环境变量 `HTTP_PROXY` / `HTTPS_PROXY` **为空**,而 httpx 的 `trust_env=True` 只读环境变量、**不读 Windows 注册表**。
  ⇒ 即便代理修好,由 ZCode 启动的 MCP server 也拿不到代理地址(它不像 `real_check.py` 那样有端口自动探测)。

### C. 验证层 —— 错误信息不透传

引擎失败只记 `mark_unhealthy()` + 一行日志,返回值里只有引擎名。**403 配额耗尽、401 key 无效、网络超时在用户侧长得一模一样**,
导致"配好了却用不了"时完全无法自查。这是最该优先修的可用性缺陷。

---

## 4. 接管方案

### P0 — ✅ 已实施完成(2026-10-20 晚)

1. ✅ **失败原因透传**:新增 `orchestrator.classify_failure()`,把异常归类为
   `quota_exhausted` / `auth_failed` / `network_unreachable` / `timeout` / `rate_limited` / `server_error`,
   附加 HTTP 状态码、响应体摘要与可操作提示,通过新增的 `engine_errors` 字段返回;
   全部失败时额外给出 `error` 汇总。**实测生效**:
   `bocha → quota_exhausted(403)`、`tavily → network_unreachable`、`serper → timeout`。
2. ✅ **server 侧补代理探测**:新增 `orchestrator.detect_egress_proxy()`,优先级为
   `AGG_PROXY` → 环境变量 → 7897/7890/10809 端口探测 → **Windows 注册表系统代理**
   (httpx 的 `trust_env` 不读注册表,这是原先拿不到代理的根因)。国内源仍强制直连。
   实测正确输出 `[egress proxy] http://127.0.0.1:7897`。
3. ✅ **修 `real_check.py` 的 socket 泄漏**:探测逻辑已下沉到 orchestrator,real_check 改为复用并打印。
4. ✅ **README 增补**:新增「失败自查」与「验收清单」两节。

#### 附带修复(诊断中额外发现)

5. ✅ **`.env.example` 明文存放真实 API key**(博查/Tavily/Serper 三个真 key),
   而 `.gitignore` 只忽略 `.env` —— 模板文件一旦提交即密钥泄露。已全部替换为占位符。
6. ✅ **智谱补齐两个官方能力**:新增 `search_recency_filter` 时间筛选映射,
   `search_engine` 改由 `ZHIPU_SEARCH_ENGINE` 环境变量可选(默认 `search_std` 保持兼容)。
   已核实 `/api/paas/v4/web_search` 是官方**现行**端点,非废弃接口。
7. ✅ **新增 `tests/test_failure_classify.py`**(12 项),测试总数 22 → **34,全部通过**;E2E 仍为 `E2E OK`。

### P1 — 需要你提供材料

5. **博查充值或换新 key**(403 是硬阻断);或改填 **智谱 key** — 智谱国内直连、约 0.01 元/次,是本机最现实的国内主力源。
6. 智谱适配器当前按 `POST /api/paas/v4/web_search` + `search_engine=search_std` 实现,**拿到 key 后必须实测一次**确认官方接口未变更(README 已知限制里也承认了这一点)。

### P2 — 需要你决策方向

7. **海外源是否保留**:若你长期不换网络,Tavily / Serper 属于"永远进黑名单"的死重。可选:
   - (a) 保留并依赖黑名单自动跳过(现状,每次冷启动仍要等一次超时);
   - (b) 用 `AGG_ENABLE_TAVILY=0` / `AGG_ENABLE_SERPER=0` 显式禁用,彻底省掉等待;
   - (c) 排查 Clash 7897 的 TLS 拦截规则后再启用。
8. **SearXNG 自托管**:免 key、可内网部署,是绕开"key 配额 + 海外不可达"两条限制的最优解,但需要你确认是否愿意部署一个实例。
9. **二期 LLM 重排**:README 已列为 TODO,当前 RRF 融合已可用,非阻塞。

---

## 5. 收尾:已打通(2026-10-20 21:20)

用户提供博查新 key + 一个标注为「zcode」的 key,实测结果:

| 项 | 结果 |
|---|---|
| 博查新 key | 首次仍 403(同账户额度共享,换 key 无效)→ **用户领取免费资源包 1000 次后恢复可用** |
| 「zcode」key | 实测**确认为智谱 API Key**(两段式 `id.secret`),调用 `/api/paas/v4/web_search` 成功返回 3 条结果 → 已写入 `.env` |
| 最终 `real_check.py` | **REAL CHECK OK**,中文 query 命中 `bocha`+`zhipu`,英文 query 同样有结果 |

### 过程中发现的网络实况(重要)

`diag4` 对四个源各做 5 次 TLS 握手,**全部存在秒级抖动**:

```
api.bochaai.com    217ms  9226ms   199ms  3236ms  8233ms
open.bigmodel.cn   135ms  7109ms Timeout   113ms    94ms
api.tavily.com     952ms  4535ms  3888ms   500ms   529ms
google.serper.dev 1363ms   439ms  1164ms Timeout   765ms
```

原先 `connect=5.0s` 的硬上限会**误杀 9 秒级握手的国内源**,这是"国内引擎莫名超时"的真因。
已把 `AGG_TIMEOUT_S` 默认 10→20、新增 `AGG_CONNECT_TIMEOUT_S` 默认 10。
调整后博查双 IP 握手 214/187ms、连续两次真实调用均成功。

### 复测:海外源已恢复(2026-10-20 21:30)

用户提示"当前网络可以到达海外"后复测,**推翻了上午"海外源不可达"的结论**:

| 通道 | Tavily | Serper |
|---|---|---|
| **直连** | ✅ HTTP 200,返回 2 条 | 间歇(握手 3250/3180ms 成功过) |
| 经 7897 代理 | ❌ SSL UNEXPECTED_EOF ×4 | ❌ 同上(仅 1/4 侥幸成功) |

**真正的坑**:`_proxy_usable()` 对 7897 连测 5 次结果是 `False/True/True/False/True` ——
它是**间歇性坏代理**(接受 CONNECT 却在 TLS 阶段掐断),单次探测根本判不准,
而我上午加的"注册表回退"恰好把海外引擎导向了它,导致海外源必失败。

修复(两层):

1. 探测到代理后必须**通过 CONNECT + TLS 握手验证**才采用。
2. 海外引擎走代理失败时,**自动降级直连重试一次**,并在本进程内停用代理。

修复后 `real_check.py`:**四个引擎全部命中** ——
`used=['bocha','zhipu','tavily','serper'] failed=[]`,且出现 `[tavily,serper]` 跨源命中(融合生效)。
测试 35 passed(新增「坏代理必须被丢弃」用例)。

### 剩余可选项(不阻塞使用)

| 项 | 说明 |
|---|---|
| SearXNG | 免 key 自托管,可彻底摆脱配额与出网限制,属可选增强 |
| 海外源 | **已恢复,无需禁用** |

> 各平台申请网址与流程集中在 **[KEYS.md](KEYS.md)**。

## 6. 本次改动的文件清单

| 文件 | 改动 |
|---|---|
| `aggregate_search/orchestrator.py` | 新增 `classify_failure()` / `all_failed_hint()` / `detect_egress_proxy()` / `_windows_system_proxy()`;`_run_one` 返回失败详情;返回值新增 `engine_errors` 与全失败时的 `error` |
| `aggregate_search/engines/zhipu.py` | 补齐 `search_recency_filter` 时间筛选;`search_engine` 可由 `ZHIPU_SEARCH_ENGINE` 指定 |
| `server.py` | `engine_status` 增加 `egress_proxy` 输出 |
| `real_check.py` | 去掉重复的 socket 探测(含泄漏),改为复用 orchestrator 并打印失败分类 |
| `.env.example` | **清除其中明文存放的 3 个真实 API key**,改为占位符;补充新增环境变量说明 |
| `README.md` | 新增「失败自查」「验收清单」;更新配置说明与测试数量 |
| `tests/test_failure_classify.py` | 新增,12 项失败分类与代理探测测试 |
| `HANDOFF_接管诊断.md` / `KEYS.md` | 本文档 + key 配置指南 |

未改动:`.env`(你的真实 key 原样保留)、`~/.zcode/cli/config.json`(注册配置未动)、融合算法与各引擎解析逻辑。

---

## 附录:本次诊断产生的临时脚本(均在工作区 `.workbuddy` 下,不在工程目录内)

- `F:\软件仓库_新\开发软件\.workbuddy\diag_net.py` — TCP/TLS/代理三层连通性矩阵
- `F:\软件仓库_新\开发软件\.workbuddy\diag3_bocha.py` — 博查单次真实调用(输出脱敏,不打印 key)
- 运行日志:`_pytest_run.txt` / `_e2e_run.txt` / `_real_check_run.txt` / `_diag_net.txt` / `_diag2.txt` / `_diag3.txt`
