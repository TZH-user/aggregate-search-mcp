# aggregate-search — 聚合搜索 MCP

把多个搜索引擎(国内:博查/智谱;海外:Tavily/Serper;免 key 兜底 DuckDuckGo;可选自托管 SearXNG)
聚合成一个 MCP 服务,工具:`web_search` / `read_url`(网页正文抽取) / `engine_status`。
链路:并行查询 → 加权 RRF 融合 → 相关性过滤(去跑题)→ 近重复合并 → 可选语义/LLM 重排 → top-K。

- 海外引擎不可达时自动退回国内源:失败引擎进入 300s 健康黑名单,期间不再等待其超时;
  尾延迟抖动时按软截止(`AGG_SOFT_DEADLINE_S`)提前返回已完成引擎的结果。
- 中文 query 自动提升国内源权重(博查/智谱 1.3),英文反之(Tavily/Serper 1.3)。
- 每个结果的 `engines` 字段标注命中来源,跨源命中的结果更可信;过滤/降权全程留痕可复核。
- 同参数查询 300s 内直接回缓存(全失败不缓存);`read_url` 直连优先、失败经代理重试,实测 2s 返回 7.6k 字正文。
- 迭代全程有评测线把着([eval/](eval/)):14 题冻结金标准 + 8 题保留集,金标 Hit@5 11/11、垃圾 0/70,
  保留集泛化零误杀,逐版本趋势见 [CHANGELOG.md](CHANGELOG.md)。

> 🇬🇧 English version: [README.md](README.md)

## 快速开始(5 分钟跑通)

> 本包**不带任何 API key**,需要你自己申请——各平台网址见下方「配置」第 1 条,模板见 `.env.example`。

1. **建环境**(路径无关,任意目录解压均可):

   ```bash
   bash setup.sh
   ```

   没有 uv 时手动装:`uv venv --python 3.12 .venv` 然后
   `uv pip install --python .venv/Scripts/python.exe -r requirements.txt -r requirements-dev.txt`
   (Linux/macOS 把 `Scripts` 换成 `bin`)。

2. **配 key**:`cp .env.example .env`,至少填入一个引擎的 key(推荐先配博查,免费 1000 次)。
   全部留空也能启动,但 `web_search` 会提示"没有可用引擎"。

3. **验证**(按顺序,前两步不出网不花钱):

   ```bash
   .venv/Scripts/python.exe -m pytest -q     # 90 项离线测试
   .venv/Scripts/python.exe verify_e2e.py    # stdio 端到端
   .venv/Scripts/python.exe real_check.py    # 真实调用,消耗额度
   ```

4. **注册进 MCP 客户端**(ZCode / Claude Code 等,`mcp.servers` 里加一段):

   ```json
   "aggregate-search": {
     "type": "stdio",
     "command": "<项目绝对路径>/.venv/Scripts/python.exe",
     "args": ["<项目绝对路径>/server.py"],
     "cwd": "<项目绝对路径>"
   }
   ```

   Linux/macOS 的 `command` 用 `.venv/bin/python`。

5. **重启客户端**,对话里说"搜一下 XXX"即可触发 `web_search`;
   排查问题先调 `engine_status` 看 key 配置、健康状态与出网代理。

## 评测

冻结的 14 题金标准,覆盖 5 个方向(中/英时效、权威源定位、跑题抵抗、精确事实),逐条 0/1/2 判分,与内置 WebSearch 对比;另设 8 题保留集(不参与任何调参)检验过滤器过拟合。方法、逐题判分 evidence 与原始引擎输出全部落盘在 [eval/](eval/)。

| 指标(v6) | aggregate-search | 内置 WebSearch |
|---|---|---|
| 金标 Hit@5(11 题适用) | 11/11 | 11/11 |
| MRR | 0.848 | 1.000 |
| 垃圾条数(得分 0) | 0/70 | 0/62 |
| 结果条数覆盖 | 70/70 | 62/70 |
| 延迟 | 均值 2.7s / 中位 2.6s | 未测 |

- v1→v6 演进(覆盖率过滤→限条+去重修复→语义重排→近重复合并):垃圾 5/70→0/70,Hit@5 10/11→11/11,见 [eval/report.md](eval/report.md)
- 保留集 A/B:过滤器开启后垃圾 5/40→0/40、普通查询零误杀,见 [eval/holdout_report.md](eval/holdout_report.md)

## 目录结构

```
server.py                 MCP 入口(stdio),工具:web_search / read_url / engine_status
aggregate_search/
  config.py               引擎注册表 + zh/en 权重 + key 检测
  models.py               SearchResult 统一模型
  fusion.py               URL 规范化、去重、加权 RRF(score = Σ w/(60+rank))、跨源加分、单站限条、近重复合并
  relevance.py            词覆盖率相关性过滤 + 权威域助推 + dropped/downweighted 审计留痕
  rerank.py               embedding / LLM 重排(智谱 embedding-3 / glm-4-flash),失败自动降级保序
  reader.py               read_url 正文抽取(trafilatura)+ 代理重试
  orchestrator.py         并行调度、健康黑名单、失败分类、出网代理探测、查询缓存、软截止
  engines/                各引擎适配器(新增引擎:加一个文件 + 在 config.SPECS 注册)
tests/                    90 个 mock 测试(fusion / engines / orchestrator / relevance / rerank / 缓存)
eval/                     冻结金标准、逐版本判分、保留集报告、原始引擎输出
verify_e2e.py             用 MCP stdio 客户端真实连接 server 的端到端验证
real_check.py             真实 key 全链路验证(消耗额度,输出失败分类)
CHANGELOG.md              v1→v6 迭代日志
docker-compose.yml        可选:自托管 SearXNG(配套 searxng-settings/)
HANDOFF_接管诊断.md        工程接管诊断报告
借鉴调研.md                同类开源搜索 MCP 调研
.env.example              API key 模板
```

## 配置

1. `cp .env.example .env`,填入已申请的 key(全部可选,填了哪个用哪个):
   - 博查 https://open.bochaai.com (中文主力,免费 1000 次 + 兑换码再 1000 次)
   - 智谱 https://open.bigmodel.cn (GLM 生态,端点 `/api/paas/v4/web_search`)
   - Tavily https://app.tavily.com (每月 1000 次免费)
   - Serper https://serper.dev (Google SERP,$1/千次)
   - SearXNG_BASE_URL(自托管实例,免 key)
2. 可选环境变量:
   - `AGG_RELEVANCE`(默认 1)— 词覆盖率相关性过滤,`AGG_REL_MIN_COV`(0.4)/`AGG_REL_PENALTY`(0.3)/
     `AGG_AUTH_BOOST`(1.3)调参;设 0 关闭。
   - `AGG_RERANK`(默认关)— `embedding`(智谱 embedding-3)/`llm`(glm-4-flash,RankGPT 式整体排序,
     解析失败自动降级保序)或叠加 `"embedding,llm"`;`AGG_RERANK_ALPHA`(0.4)融合系数、
     `AGG_RERANK_POOL`(20)候选池。
   - `AGG_CACHE_TTL_S`(300,0 关)— 查询缓存(LRU 128,全失败不缓存);
     `AGG_SOFT_DEADLINE_S`(10,0 关)— 软截止,尾延迟抖动时提前返回已完成引擎结果。
   - `AGG_MAX_PER_HOST`(2)— 单站限条,防止单站霸屏;`AGG_NEAR_DUP`(1)/`AGG_NEAR_DUP_THRESHOLD`(0.9)—
     同站参数变体近重复合并;`AGG_READ_MAX_CHARS`(8000)— read_url 输出上限。
   - `AGG_DOMAIN_BOOST` / `AGG_DOMAIN_BLOCK` — 域名黑白名单(`AGG_DOMAIN_BOOST_FACTOR` 1.5);
     `AGG_ENABLE_DUCKDUCKGO=1` 显式开启免 key 兜底引擎。
   - `AGG_PROXY` — 海外引擎出网代理(如 Clash 混合端口 `http://127.0.0.1:7897`);
     国内引擎(博查/智谱)始终直连,不受代理影响。
     留空时按 环境变量 → 常见端口(7897/7890/10809)探测 → **Windows 注册表系统代理** 依次回退。
     ⚠️ **探测到 ≠ 能用**:代理需通过一次 CONNECT + TLS 握手验证才会被采用;
     海外引擎走代理失败时会自动**降级直连重试一次**,失败后本进程内不再用代理。
     (实测案例:本机 7897 即属"接受 CONNECT 却在 TLS 阶段掐断"的间歇性坏代理,而海外源直连可用)
   - `AGG_ENABLE_<NAME>=0` 强制禁用某引擎(bocha/zhipu/tavily/serper/searxng)
   - `AGG_TIMEOUT_S`(默认 20)/ `AGG_CONNECT_TIMEOUT_S`(默认 10)/ `AGG_UNHEALTHY_TTL_S`(默认 300)
   - `ZHIPU_SEARCH_ENGINE` — 智谱搜索引擎编码,默认 `search_std`;
     可选 `search_pro`(高阶)/ `search_pro_sogou` / `search_pro_quark`

## 开发

```bash
bash setup.sh                                  # 建 venv + 装依赖(uv, Python 3.12)
.venv/Scripts/python.exe -m pytest -q          # 90 个 mock 测试,不出网、不消耗额度
.venv/Scripts/python.exe verify_e2e.py         # stdio 端到端验证(mock,不出网)
.venv/Scripts/python.exe real_check.py         # 真实 key 全链路验证(消耗配额,输出失败分类)
```

## 失败自查

`web_search` 返回值里有 `engine_errors` 字段,每个失败的引擎都会给出可操作的死因:

```json
"engine_errors": {
  "bocha": {"kind": "quota_exhausted", "status": 403,
            "detail": "You do not have enough money or package quota",
            "hint": "配额/余额耗尽,去控制台购买或领取资源包"}
}
```

`kind` 取值与处置:`quota_exhausted` 额度耗尽 / `auth_failed` key 无效 / `network_unreachable`
网络不通 / `timeout` 超时 / `rate_limited` 限流 / `server_error` 服务端故障。
全部引擎失败时还会多一个 `error` 字段,直接汇总所有引擎的死因。

也可以用 `engine_status` 查看 key 配置、健康状态与当前生效的出网代理(`egress_proxy`)。

## 验收清单(改动后按此回归)

1. `.venv\Scripts\python.exe -m pytest -q` → 90 passed(离线,不出网)
2. `.venv\Scripts\python.exe verify_e2e.py` → `E2E OK`,且 `TOOLS` 含 `web_search` / `read_url` / `engine_status`
3. `.venv\Scripts\python.exe real_check.py` → `used` 非空且 `results > 0`(消耗额度)
4. 若第 3 步失败,按输出的 `kind` 对照上方「失败自查」处置,**不要靠猜**

## 已知限制 / 待办

- 各引擎响应格式按官方文档实现,真实 key 首次使用时建议跑一次 `web_search` 核对
  `engines_used` 是否齐全(如有接口变更,改动只在对应适配器文件内)。
- 重排只能重排引擎已返回的结果——官方站根本不在返回池时重排无解(召回缺口,
  见 [eval/holdout_report.md](eval/holdout_report.md) H8),需 per_engine 加深或引擎侧解决。
- 智谱 `count` 参数按文档最大值处理,若实际返回数不同不影响融合。
- 海外引擎(Tavily / Serper)依赖出网代理;在无可用代理的机器上会持续进入健康黑名单,
  建议用 `AGG_ENABLE_<NAME>=0` 显式禁用以省掉每轮冷启动的超时等待。

## 许可证

**MIT** © 2026 TZH-user,见 [LICENSE](LICENSE)。
