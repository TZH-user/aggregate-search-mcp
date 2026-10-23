# 保留题(hold-out)A/B 对照报告 —— 过拟合检验

- 日期:2026-10-24
- 方法:8 道全新查询(**不参与** relevance 过滤器的任何调参),同进程内 `AGG_RELEVANCE=0/1` 各跑一次,对比 top-5
- 原始数据:`raw/holdout_ab.json`;脚本:`holdout_ab.py`
- 判分口径与主评测一致(0/1/2,跑题或错实体=0)

## 结论速览

| 指标 | 过滤器关 | 过滤器开 |
|---|---|---|
| 垃圾条数(40 条中) | **5** | **0** |
| 好结果被挤出 top-5(明确误杀) | — | **0** |

## 逐题判分

| ID | 类型 | 查询 | 关(判分) | 开(判分) | 判定 |
|---|---|---|---|---|---|
| H1 | 陷阱 | Transformers movie 2026 box office | 5条全切题,0垃圾 | 5条全切题,0垃圾;facebook贴→the-numbers票房库(2↔2) | 持平 |
| H2 | 陷阱 | Go board game rules beginners | **5条全垃圾**(引擎把query误解析成电影《Beginners》:维基/烂番茄/Instagram/Netflix) | **5条全切题**(YouTube围棋教程/reddit baduk/gomagic/维基Rules of Go) | **修复** |
| H3 | 陷阱 | mermaid diagram syntax tutorial | 5条全切题(mermaid.js.org等) | 完全相同 | 持平 |
| H4 | 陷阱 | 苹果公司 最新股价 | 5条全AAPL行情页,0垃圾 | 0垃圾;但TradingView被换成东方财富同站**参数变体页(近重复)** | 持平,暴露近重复问题 |
| H5 | 普通 | Rust 安装 教程 windows | rust-lang.org 第1,5条全好 | **top-5 完全相同**,rust-lang.org 第1(drop=4 发生在可见区外) | 无误杀 |
| H6 | 普通 | HTTP 418 status code meaning | MDN/HTTP Cats/维基HTCPP 全切题 | 同样全切题;维基HTCPP→sitechecker(2↔2) | 持平 |
| H7 | 普通 | swiftui vs flutter 2026 比较 | 5条全切题 | 同 5 条,仅顺序微调 | 持平 |
| H8 | 普通 | Django 官方文档 | MDN/GitHub/3条CSDN旧PDF(均分一般) | **top-5 完全相同**(boost=8 对可见区无影响);docs.djangoproject.com 两边都**不在池中**——召回缺口,与 C3 同病 | 持平 |

## 关键发现

1. **泛化成立**:垃圾 5/40→0/40,且 H2 修复的是调参集中**不存在的新失败模式**(整句误解析,而非 D1 的撞词污染)——过滤器规则(词覆盖率)起作用的机制与具体查询无关,这是抗过拟合的最有力证据。
2. **无误杀**:4 道普通查询的 top-5 完好(H5/H7/H8 逐条相同,H6 等价换条);`drop=N` 大多发生在 top-5 之外的深名次候选。
3. 暴露三个真问题:
   - 被剔除/降权的结果**无留痕**,无法事后复核 → 已修:`relevance` 字段新增 `dropped_items` / `downweighted_items` 审计列表;
   - H4 近重复 URL:`canonical_url` 保留查询参数,同站不同参数的两页算不同结果,一页被过滤挤出后**另一页参数变体顶进来** → 记入已知限制(可做标题相似度二次去重);
   - H8 权威域助推"空转":官方站(djangoproject)根本不在引擎返回池时,重排层无解 → 召回缺口,需 per_engine 加深或引擎侧解决。

## 方法论约定(防过拟合)

- `goldset.json`(14 题)冻结,只作回归基线,不再用于调参;
- `holdout`(8 题)只用于验证泛化,若据其调阈值,需再留第三批新题做终验;
- 阈值调整必须同时跑主集+保留题并双报告落盘。


## rerank 轮(2026-10-24 追加,v4)

8 题保留集在 `AGG_RERANK=embedding` 下重跑(原始数据 raw/holdout_rerank.json):

| ID | 判定 |
|---|---|
| H1-H4 陷阱题 | 全部无垃圾;H1 好莱坞报道新进,H2 official-go 官方学习页新进 |
| H5-H7 普通题 | top-5 全切题,**无任何误杀** |
| H8 | **标志性场景**:官方 docs.djangoproject.com(中/英)+ djangoproject.com 进池后,rerank 全部抬顶(第1/3/4)——上一轮(仅词法过滤)官方站根本排不进 |

结论:语义重排在未见查询上泛化成立,陷阱题全防住、普通题零误杀,且具备词法层没有的
"官方内容抬顶"能力。遗留:H4 东方财富同站参数变体仍占 2 席(限条 max_per_host=2 允许),
近重复合并仍是待办。
