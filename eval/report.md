# aggregate-search vs 内置 WebSearch 对比评测报告

- 日期:2026-10-24(相关性过滤启用后);方法与金标准见 [goldset.md](goldset.md),逐条判分留证见 [scores.json](scores.json),原始数据见 raw/
- 半盲声明:判分者见过两侧原始输出,属半盲;每条判分附 evidence 可复核
- 过滤前基线见 [report_before.md](report_before.md) / [scores_before.json](scores_before.json)

| ID | 方向 | 查询 | agg均分 | web均分 | agg垃圾 | web垃圾 | 金标命中 | agg条数 | web条数 |
|---|---|---|---|---|---|---|---|---|---|
| A1 | A 中文时效 | 智谱 GLM 最新模型 发布 | 1.80 | 2.00 | 0 | 0 | agg@1 web@1 | 5 | 5 |
| A2 | A 中文时效 | 2026年 诺贝尔文学奖 得主 | 2.00 | 2.00 | 0 | 0 | agg@1 web@1 | 5 | 1 |
| A3 | A 中文时效 | iPhone 18 发布 | 2.00 | 2.00 | 0 | 0 | agg@1 web@1 | 5 | 5 |
| B1 | B 英文定位 | Model Context Protocol off | 1.40 | 1.80 | 0 | 0 | agg@2 web@1 | 5 | 5 |
| B2 | B 英文定位 | FastAPI official documenta | 1.20 | 1.40 | 0 | 0 | agg@2 web@1 | 5 | 5 |
| B3 | B 英文定位 | Python asyncio official do | 1.40 | 1.40 | 0 | 0 | agg@1 web@1 | 5 | 5 |
| C1 | C 中文定位 | 智谱开放平台 API 文档 | 1.60 | 1.80 | 0 | 0 | agg@1 web@1 | 5 | 5 |
| C2 | C 中文定位 | 博查搜索 Bocha AI API 官网 | 1.60 | 1.60 | 0 | 0 | agg@1 web@1 | 5 | 5 |
| C3 | C 中文定位 | Rust 编程语言 官网 | 1.20 | 1.40 | 0 | 0 | agg@3 web@1 | 5 | 5 |
| D1 | D 跑题抵抗 | Model Context Protocol new | 2.00 | 2.00 | 0 | 0 | (D向不适用) | 5 | 5 |
| D2 | D 跑题抵抗 | Java island travel guide | 2.00 | 2.00 | 0 | 0 | (D向不适用) | 5 | 5 |
| D3 | D 跑题抵抗 | Apple fruit nutrition fact | 2.00 | 2.00 | 0 | 0 | (D向不适用) | 5 | 3 |
| E1 | E 精确事实 | HTTP 451 status code meani | 1.80 | 2.00 | 0 | 0 | agg@1 web@1 | 5 | 3 |
| E2 | E 精确事实 | reciprocal rank fusion RRF | 2.00 | 2.00 | 0 | 0 | agg@1 web@1 | 5 | 5 |

## 总指标

| 指标 | aggregate-search | 内置 WebSearch |
|---|---|---|
| 金标 Hit@5(11 题适用) | **11/11 (100%)** | **11/11 (100%)** |
| MRR | 0.848 | 1.000 |
| 相关性均分(0-2,全部结果) | 1.714 | 1.790 |
| 垃圾条数(score=0) | 0/70 | 0/62 |
| 结果条数覆盖 | 70/70(恒为5) | 62/70(3题不足5) |
| 延迟 | 均值 2.7s / 中位 2.6s / 最大 4.8s | 未测 |

分方向相关性均分: A 中文时效: agg 1.93 / web 2.00; B 英文定位: agg 1.33 / web 1.53; C 中文定位: agg 1.47 / web 1.60; D 跑题抵抗: agg 2.00 / web 2.00; E 精确事实: agg 1.90 / web 2.00

## 版本趋势(aggregate 侧,同一冻结评测集)

| 指标 | v1 无过滤 | v2 +覆盖率过滤 | v3 +限条+去重修复 | v4 +语义重排 | v6 +近重复合并(当前) |
|---|---|---|---|---|---|
| 金标 Hit@5 | 10/11 | 10/11 | 11/11 | 11/11 | 11/11 |
| MRR | 0.791 | 0.791 | 0.864 | 0.818 | 0.848 |
| 相关性均分 | 1.586 | 1.714 | 1.686 | 1.800 | 1.714 |
| 垃圾条数 | 5/70 | 0/70 | 3/70 | 0/70 | 0/70 |

注:
- 各轮引擎返回池存在自然波动(单轮指标噪声约 ±0.1),趋势看方向不看个位数;
- **v6 轮智谱配额耗尽(1113),仅 3 引擎参与**(v4 为 4 引擎),对比含引擎池构成的混淆变量;
  近重复合并的效果以定点验证为准:H4 场景(苹果股价)同站参数变体从 2 席降回 1 席(eval/raw/holdout_rerank.json 对照);
- v4(智谱 embedding-3 语义重排)相关性均分 1.800 曾首超内置搜索(1.790),D1 主题撞词垃圾全灭;
- v6 新增 read_url 全文读取与 LLM 重排档(AGG_RERANK=llm,glm-4-flash),不影响本轮排序配置(=v4 的 embedding 档);
- v1/v2/v3/v4 基线判分见 scores_before / scores_filter_only / scores_v3 / scores_v4.json。
