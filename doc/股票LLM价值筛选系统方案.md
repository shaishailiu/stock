# 股票 LLM 价值筛选系统方案

> 版本：2026-06-10  
> 适用场景：已有 Python 脚本可以根据价格回撤筛选出候选股票，希望进一步结合 Tushare 数据和大模型，对股票进行价值投资研究优先级排序。  
> 重要说明：本文档用于构建股票研究辅助系统，不构成任何投资建议或交易指令。

---

## 1. 背景与目标

当前系统已经可以通过 Python 根据价格条件筛出一批股票，例如：

- 从阶段高点大幅回撤；
- 接近 52 周低位；
- 股价处于历史低分位；
- 具备潜在“错杀”可能。

但仅靠价格筛选无法判断股票是真正低估，还是基本面恶化导致的“价值陷阱”。因此需要补充估值、财务、现金流、风险事件、资金行为、公告新闻等信息，并将结构化结果交给大模型进行综合判断。

最终目标是构建一个 **LLM Agent 驱动的每日研究系统**：

1. Agent 启动前，Python 先完成数据获取、清洗、指标计算、变化检测和入库；
2. Python 将港股、美股和 A 股数据按所属市场分别整理成可查询的结构化事实库；
3. Agent 启动后，由 LLM 负责驱动研究流程，而不是由 Python 硬编码完整决策流程；
4. Agent 通过 Python 工具查询候选池、快照、变化事件、历史结论，并触发评分映射、结论保存、报告生成等操作；
5. 最终输出“今日研究优先级榜单”和“变化日报”。

---

## 2. 核心原则

### 2.1 每日运行不等于每日重写结论

每日运行的目的不是每天重新生成一套类似的分析，而是发现：

- 哪些股票新进入候选池；
- 哪些股票退出候选池；
- 哪些老池股票发生关键变化；
- 哪些 LLM 研究优先级较高的股票虽然没有变化但仍值得关注；
- 哪些股票出现风险事件，需要下调或剔除。

正确逻辑是：

```text
首次完整分析
每日增量监控
重大变化触发重评
无重大变化沿用旧结论
周度或月度做完整复盘
```

### 2.2 新进股票不天然比老池股票重要

新进候选只说明它刚刚满足价格回撤条件，不代表它比池子中已经长期跟踪的高质量股票更重要。

因此每日输出不能只看“新增”或“变化”，而应维护一个统一的全池优先级排序。

推荐排序逻辑调整为：

```text
final_priority = llm_priority_score * 70%
               + bottom_signal_score * 20%
               + attention_score * 10%
```

其中：

- `llm_priority_score` 表示 Python 根据 LLM 结构化研究结论映射出的研究优先级分，不是 LLM 自由打出的分数；
- `bottom_signal_score` 表示 Python 根据技术指标计算出的“底部信号强度”，只说明技术面是否更像底部，不代表公司价值；
- `attention_score` 表示今日关注紧迫度，主要用于识别财报、公告、风险事件、价格异动等变化；
- `final_priority` 表示今日最终研究排序。

重要原则：**不再由 Python 通过财务指标硬算 `value_score`，财务指标只整理成结构化事实交给 LLM 判断。**

---

## 3. 数据来源建议

本系统统一采用 **Tushare** 作为第一数据源，覆盖港股、美股、A 股三类市场。三类市场在系统中地位相同，不区分“主市场”和“补充市场”。

核心原则是：**股票属于哪个市场，就使用该市场对应的数据接口和字段体系。**

原因是：

1. 当前 Tushare 可使用 **10000 积分档**，足以支撑更完整的数据接口调用；
2. Python 生态成熟，适合每日自动化任务；
3. 同一套接口体系便于做代码标准化；
4. 港股、美股、A 股都应进入统一候选池、统一 Agent 工具层和统一研究优先级排序；
5. 不同市场字段存在差异，系统应通过市场适配层处理，而不是把某个市场降级为补充。

注意：后续实现不能按“A 股主系统 + 港美补充”或“港美主系统 + A 股补充”的思路设计，而应按“多市场同等接入 + 按市场选择对应数据接口”的思路设计。若 Tushare 某个市场的字段缺失，补充数据源只作为该市场的兜底模块，不改变系统主架构。

详细的数据请求字段、主键、增量参数和清洗规则见 `Python代码结构设计.md` 的 `4.5 Tushare 数据请求清单` 与 `4.6 Python 数据清洗与加工清单`。主方案只保留数据层级和职责边界，具体字段契约以后以 Python 设计文档为准。

### 3.1 Tushare 适合获取的数据

| 市场 | 数据类型 | 典型接口 | 用途 |
|---|---|---|---|
| 港股 | 基础信息 | `hk_basic` | 港股代码、名称、上市状态等 |
| 港股 | 日线行情 | `hk_daily` / `hk_daily_adj` | 港股 OHLCV、涨跌幅等，优先用 `hk_daily_adj` 获取复权行情 |
| 港股 | 复权 | `hk_adjfactor` | 港股复权因子，备选方案 |
| 港股 | 交易日历 | `hk_tradecal` | 港股交易日校验 |
| 港股 | 三大财报 | `hk_income` / `hk_balancesheet` / `hk_cashflow` | 港股利润表、资产负债表、现金流量表 |
| 港股 | 财务指标 | `hk_fina_indicator` | 港股 ROE、利润率、成长性等财务指标 |
| 港股 | 持股数据 | `hk_hold` | 港股通持股等补充信息 |
| 美股 | 基础信息 | `us_basic` | 美股代码、名称、交易所、上市状态等 |
| 美股 | 日线行情 | `us_daily` / `us_daily_adj` | 美股 OHLCV、涨跌幅等，优先用 `us_daily_adj` 获取复权行情和估值指标 |
| 美股 | 复权 | `us_adjfactor` | 美股复权因子，备选方案 |
| 美股 | 交易日历 | `us_tradecal` | 美股交易日校验 |
| 美股 | 财务数据 | `us_income` / `us_balancesheet` / `us_cashflow` / `us_fina_indicator` | 美股利润表、资产负债表、现金流量表、财务指标，当前仅覆盖主要美股和中概股 |
| A 股 | 基础股票信息 | `stock_basic` | 股票代码、名称、上市状态、行业等 |
| A 股 | 日线行情 | `daily` | 价格、涨跌幅、成交量等 |
| A 股 | 每日指标 | `daily_basic` | PE、PB、PS、股息率、市值、换手率等 |
| A 股 | 复权因子 | `adj_factor` | 复权价格计算 |
| A 股 | 财务指标 | `fina_indicator` | ROE、毛利率、净利率、营收同比、利润同比等 |
| A 股 | 三大财报 | `income` / `balancesheet` / `cashflow` | 利润表、资产负债表、现金流量表 |
| A 股 | 事件与风险 | `forecast` / `express` / `dividend` / `suspend_d` / `namechange` / `stock_st` / `fina_audit` | 业绩预告、业绩快报、分红、停复牌、名称变更、ST 识别、审计意见 |
| A 股 | 资金与股东 | `moneyflow` / `stk_holdernumber` / `top10_holders` / `pledge_stat` / `pledge_detail` / `share_float` | 资金行为、股东结构、质押统计与明细、解禁 |

### 3.2 10000 积分档与接入策略

当前明确可以使用 Tushare **10000 积分档**，因此方案按较完整版本设计，不再按 5000/8000 积分档做降级假设。

接入时按数据层级分层，而不是按市场分主次：

1. **第一层：多市场行情与价格筛选**  
   对港股、美股、A 股都必须稳定拉取对应日线数据，用于复用旧版寻找底部逻辑。
2. **第二层：多市场估值与财务摘要**  
   按股票所属市场分别拉取 PE、PB、ROE、利润率、营收增速、净利润增速、现金流、负债率等字段，整理成 Agent 研究上下文。
3. **第三层：多市场事件和风险数据**  
   根据各市场可用字段分别补齐持股变化、财报发布、分红、停牌、重大风险等信息。
4. **第四层：市场差异适配**  
   对港股、美股、A 股分别维护字段映射和缺失字段标记，保证 Agent 看到的是统一结构、市场来源明确的事实数据。

---

## 4. 需要给 LLM 的核心数据

LLM 不应该直接处理大量原始 K 线、完整财报明细或全量历史数据，而应该接收 Python 清洗、计算、压缩后的结构化研究包。

核心原则：

1. **技术指标由 Python 计算**，LLM 不负责计算 RSI、MACD、回撤、均线、布林带等确定性指标；
2. **财务指标由 Python 提取和整理**，但不再由 Python 通过财务指标硬算价值评分；
3. **LLM 负责综合判断**，判断是否值得继续研究、是否可能是价值陷阱、主要风险是什么；
4. **原始数据只在必要时少量补充**，例如最近 20 日价格摘要、最近 8 个季度财务摘要、公告新闻摘要；
5. **Agent 看到的是 Python 清洗后的结构化结果**，包括 `StockSnapshot`、`SignalCard`、`ChangeEvent` 和 `data_quality`，不是 Tushare 原始字段。

### 4.1 基础信息

- 股票代码；
- 股票名称；
- 所属市场：港股、美股、A 股；
- 所属行业；
- 总市值；
- 流通市值；
- 上市年限；
- 是否 ST 或退市风险；
- 是否停牌；
- 当前池子状态：新进、老池、退出、风险警报等。

### 4.2 价格位置与技术底部信号

这部分来自旧版 `stock_monitor_v2.py` 的寻找底部逻辑，作为候选池入口和技术信号摘要。

需要提供给 LLM 的不是全量 K 线，而是这些计算结果：

- 当前价格；
- 阶段高点价格与日期；
- 从阶段高点回撤幅度；
- 52 周高点；
- 52 周低点；
- 距离低点反弹幅度；
- 当前价格在过去一年中的分位；
- 20 日、60 日、120 日均线位置；
- 日线 RSI；
- 周线 RSI；
- MACD DIF、DEA、柱状图；
- 是否出现 MACD 底背离；
- 布林带位置；
- 120 日 BIAS；
- 量比；
- 触发的底部筛选等级；
- `bottom_signal_score`。

### 4.3 估值指标

- PE TTM；
- PB；
- PS TTM；
- 股息率；
- 总市值、流通市值；
- PE 历史分位；
- PB 历史分位；
- 行业 PE/PB 中位数；
- 当前估值相对行业的位置。

这些指标只作为事实输入，不在 Python 中合成为 `value_score`。

### 4.4 财务质量

- ROE；
- ROIC；
- 毛利率；
- 净利率；
- 三年平均 ROE；
- 营收同比；
- 净利润同比；
- 三年营收复合增速；
- 三年净利润复合增速。

### 4.5 现金流质量

- 经营现金流；
- 自由现金流；
- 经营现金流 / 净利润；
- 最近五年自由现金流为正的年份数；
- 资本开支 / 营收。

### 4.6 资产负债与风险

- 资产负债率；
- 有息负债；
- 流动比率；
- 速动比率；
- 商誉 / 净资产；
- 应收账款增速；
- 存货增速；
- 股权质押比例；
- 未来 90 天解禁比例；
- 是否有重大诉讼；
- 是否有监管处罚；
- 审计意见是否异常。

任一市场如果缺少某些风险字段，应在 Agent 可查询的结构化上下文中明确标记 `data_missing`，不能让 Agent 自行推断。

### 4.7 资金与交易行为

- 换手率；
- 量比；
- 20 日平均成交额；
- 5 日主力净流入；
- 20 日主力净流入；
- 港股通持股变化；
- 北向资金变化；
- 成交额是否明显放大。

### 4.8 新闻、公告与事件

适合让 LLM 理解的内容包括：

- 业绩预告；
- 业绩快报；
- 定期报告摘要；
- 回购公告；
- 增持或减持公告；
- 股权质押公告；
- 解禁公告；
- 监管处罚；
- 诉讼仲裁；
- 并购重组；
- 重大合同；
- 行业政策变化；
- 机构调研摘要。

### 4.9 可少量补充给 LLM 的原始片段

原则上不传全量原始数据，但可以按需提供高信息密度片段：

| 原始片段 | 使用场景 |
|---|---|
| 最近 20 日价格摘要 | 技术形态变化明显时 |
| 最近 8 个季度营收、利润、现金流摘要 | 判断基本面是否持续恶化时 |
| 最近 5 年年度财务摘要 | 判断长期质量和周期位置时 |
| 公告、新闻、财报摘要文本 | 需要语义理解时 |
| 上一次 LLM 结论 | 做增量分析和结论复用时 |

---

## 5. Python 与 LLM Agent 的职责边界

### 5.1 总体原则

本系统不是传统的“Python 主程序决定流程，然后按条件调用 LLM”的架构，而是 **LLM Agent 驱动架构**。

核心分工是：

```text
Python = 数据准备层 + 指标计算层 + 存储层 + 工具层
LLM Agent = 研究流程驱动层 + 判断层 + 解释层
```

也就是说：

- Agent 启动前，Python 可以把数据全部清洗好、计算好、存储好；
- Agent 启动后，由 LLM 决定下一步要看什么、分析什么、复用什么、重评什么；
- Agent 不直接处理原始数据，而是调用 Python 暴露的工具查询结构化结果；
- Python 不替代 Agent 做研究判断，只保证数据、计算、存储和工具调用稳定可靠。

### 5.2 Python 负责什么

Python 负责所有确定性、可计算、可复现、需要预处理和入库的工作。

主要包括：

1. Tushare 数据获取；
2. 数据清洗；
3. 股票代码标准化；
4. 缺失值与异常值处理；
5. 阶段高点、回撤、价格分位等价格位置计算；
6. RSI、周线 RSI、MACD、布林带、BIAS、量比等技术指标计算；
7. 复用旧版寻找底部逻辑，完成候选池硬筛选；
8. 计算 `bottom_signal_score`，表示技术底部信号强弱；
9. 提取估值、财务、现金流、资产负债、风险字段，并整理成结构化摘要；
10. 生成并保存 StockSnapshot；
11. 生成并保存 ChangeEvent；
12. 计算 `attention_score`；
13. 保存观察池状态和历史 LLM 结论；
14. 提供 Agent 可调用的 Python 工具；
15. 校验 Agent/LLM 输出 JSON；
16. 根据 LLM 结构化判断映射 `llm_priority_score`；
17. 保存分析结论和生成最终报告文件。

Python **不再通过财务指标硬算 `value_score`**。财务指标只作为结构化事实输入，最终研究判断由 Agent 驱动完成。

### 5.3 Agent 负责什么

LLM Agent 负责驱动每日研究流程，以及完成需要语义理解、综合判断和表达的工作。

主要包括：

1. 决定今天先查看哪些候选池、变化事件和历史结论；
2. 调用 Python 工具获取候选列表、StockSnapshot、ChangeEvent、SignalCard、历史分析；
3. 理解公告和新闻摘要；
4. 识别价值陷阱；
5. 综合矛盾信号，例如技术面见底但基本面恶化；
6. 判断旧结论是否仍成立；
7. 判断哪些股票需要完整分析、增量分析、复用旧结论或剔除；
8. 输出研究优先级等级和结构化判断，供 Python 映射为 `llm_priority_score`；
9. 解释为什么某只股票排在前面；
10. 生成简洁、可读的日报文本；
11. 给出后续观察点。

### 5.4 Agent 可调用的 Python 工具

建议把 Python 能力封装成 Agent 工具，例如：

| 工具 | 用途 |
|---|---|
| `get_candidate_pool()` | 获取今日候选池和池子状态 |
| `get_stock_snapshot(code)` | 获取单只股票的结构化事实快照 |
| `get_signal_card(code)` | 获取技术底部信号卡 |
| `get_change_events(code)` | 获取近期变化事件 |
| `get_previous_analysis(code)` | 获取历史 LLM 研究结论 |
| `search_stocks(filters)` | 按市场、行业、回撤、技术信号、风险等条件筛选股票 |
| `map_llm_priority_score(llm_output)` | 将 LLM 结构化判断映射为稳定分数 |
| `save_analysis(code, analysis)` | 保存 Agent 的结构化研究结论 |
| `generate_report()` | 根据最终结果生成日报 |

### 5.5 不应该交给 Agent 的工作

不要让 Agent 做以下事情：

- 计算技术指标；
- 从原始 K 线中计算回撤；
- 自行推断缺失财务数据；
- 自行联网找数据；
- 直接处理全量原始行情和财报明细；
- 直接给出买入或卖出指令。

---

## 6. 中间决策层设计

中间层的职责是将 Python 获取、清洗、计算并存储好的数据，变成 Agent 可稳定调用的标准化数据结构和工具返回结果。

在 Agent 架构下，中间层不是一次性生成所有 LLM 任务，而是为 Agent 提供可查询、可组合、可追溯的研究上下文。

### 6.1 StockSnapshot：每日事实快照

示例：

```json
{
  "date": "2026-06-10",
  "code": "600519.SH",
  "name": "贵州茅台",
  "market": "A股",
  "industry": "白酒",
  "pool_status": "existing",
  "days_in_pool": 18,
  "price_signal": {
    "current_price": 1500.0,
    "cycle_high_price": 2220.0,
    "cycle_high_date": "2025-10-08",
    "drawdown_from_high_pct": 32.5,
    "distance_from_low_pct": 6.8,
    "price_percentile_1y": 12.3,
    "price_vs_ma60_pct": -5.1,
    "rsi_14": 34.2,
    "weekly_rsi": 39.8,
    "macd_hist": -0.18,
    "macd_divergence": false,
    "bollinger_position_pct": 14.6,
    "bias_120": -12.4,
    "volume_ratio": 128.0,
    "alert_level": "yellow",
    "bottom_signal_score": 62
  },
  "valuation": {
    "pe_ttm": 22.5,
    "pb": 6.8,
    "dividend_yield_ttm": 3.1,
    "pe_percentile_5y": 18.2,
    "pb_percentile_5y": 15.6,
    "industry_pe_median": 28.4
  },
  "fundamental": {
    "roe_ttm": 28.5,
    "revenue_yoy": 12.1,
    "net_profit_yoy": 15.3,
    "ocf_to_net_profit": 1.18,
    "debt_to_asset": 22.4
  },
  "risk": {
    "is_st": false,
    "pledge_ratio": 0,
    "unlock_ratio_next_90d": 0.5,
    "major_lawsuit": false,
    "audit_opinion": "标准无保留"
  },
  "data_missing": []
}
```

### 6.2 ChangeEvent：每日变化事件

示例：

```json
{
  "code": "600519.SH",
  "events": [
    {
      "type": "valuation",
      "level": "medium",
      "desc": "PE 历史分位下降到 18.2%，低于过去 5 年大部分交易日",
      "attention_impact": 10,
      "need_reanalysis": true
    }
  ]
}
```

### 6.3 SignalCard：技术底部信号卡

`SignalCard` 只描述技术底部信号强弱，不描述公司价值，也不替代 LLM 判断。

示例：

```json
{
  "code": "600519.SH",
  "passed_price_screen": true,
  "alert_level": "yellow",
  "bottom_signal_score": 62,
  "score_detail": {
    "drawdown": 20,
    "rsi_oversold": 16,
    "macd_divergence": 5,
    "bollinger_position": 8,
    "bias_position": 6,
    "volume_signal": 2,
    "weekly_confirmation": 5
  },
  "reason": "阶段高点回撤 32.5%，日线 RSI 34.2，价格接近一年低位"
}
```

### 6.4 AgentResearchContext：Agent 研究上下文

Agent 不需要 Python 预先生成固定任务包，而是在研究过程中通过工具按需拼装上下文。

示例：

```json
{
  "task_type": "incremental_analysis",
  "code": "600519.SH",
  "name": "贵州茅台",
  "reason": "估值分位明显下降，且价格接近 52 周低位",
  "previous_analysis": {
    "decision": "继续观察",
    "llm_priority_score": 78,
    "main_logic": "基本面稳定，估值逐步进入合理区间，但缺乏明显催化",
    "main_risks": ["消费需求恢复不确定", "行业估值中枢下降"]
  },
  "today_snapshot": {},
  "change_events": [],
  "signal_card": {},
  "raw_data_excerpt": {
    "recent_20d_price_summary": [],
    "recent_8q_financial_summary": []
  }
}
```

---

## 7. 评分体系

### 7.1 不再使用 Python 财务价值评分

本方案不再采用 Python 根据 PE、PB、ROE、利润率、现金流、负债率等财务指标硬算 `value_score` 的方式。

原因：

1. 财务指标之间存在行业差异，固定权重容易误判；
2. 不同市场字段覆盖不完全一致，硬评分会放大数据缺失问题；
3. 价值陷阱判断需要结合业务、周期、风险事件和历史结论，适合由 Agent 基于结构化事实综合判断；
4. Python 的确定性优势更适合做数据清洗、指标计算、候选筛选和变化检测。

因此，财务指标只进入 Agent 可查询的结构化事实库，不在 Python 层合成为价值评分。

### 7.2 bottom_signal_score：技术底部信号强度

`bottom_signal_score` 来自旧版 `stock_monitor_v2.py` 的思想，用于衡量“技术面是否更像底部”。

建议满分 100 分，参考维度如下：

| 维度 | 作用 |
|---|---|
| 回撤深度 | 判断是否从阶段高点出现足够回撤 |
| 日线 RSI | 判断短期是否超卖 |
| 周线 RSI | 判断中期是否超卖 |
| MACD 底背离 | 判断下跌动能是否减弱 |
| 布林带位置 | 判断价格是否处于极端低位 |
| 120 日 BIAS | 判断是否明显偏离中长期均线 |
| 量比 | 判断是否出现成交量异常 |

注意：

- `bottom_signal_score` 不是公司价值评分；
- 它只用于候选排序、Agent 分析优先级判断和技术面解释；
- 高分只说明“更像技术底部”，不说明“更值得投资”。

### 7.3 attention_score：今日关注紧迫度

建议满分 100 分，触发项如下：

| 触发项 | 加分 |
|---|---:|
| 重大风险事件 | +30 |
| 业绩预告或财报发布 | +25 |
| 价格关键变化 | +15 |
| 估值分位明显变化 | +10 |
| 资金行为明显变化 | +10 |
| 新进入候选池 | +5 |
| 距离上次完整分析超过 30 天 | +5 |

注意：新进入候选池只加少量分数，不能天然置顶。

### 7.4 llm_priority_score：LLM 研究判断映射分

`llm_priority_score` 不建议由 LLM 直接自由打分，而应由 **Python 根据 LLM 的结构化研究结论映射生成**。

原因：

1. LLM 直接输出 0-100 分容易出现尺度漂移；
2. 不同模型、不同日期对相同数据的打分可能不稳定；
3. Python 映射规则更可复现，方便回测、排序和调参；
4. LLM 更适合输出判断、理由、风险和置信度，而不是承担确定性计分器角色。

LLM 需要综合判断并输出结构化字段：

- `research_priority`：研究优先级等级；
- `opportunity_quality`：机会质量，1-5；
- `valuation_attractiveness`：估值吸引力，1-5；
- `fundamental_quality`：基本面质量，1-5；
- `risk_level`：风险等级，1-5，分数越高风险越大；
- `value_trap_probability`：价值陷阱概率；
- `confidence`：置信度；
- `data_missing`：缺失数据列表；
- `main_positive_points` 和 `main_risks`。

Python 再根据固定规则映射为 `llm_priority_score`。示例：

| LLM 输出 | 基础分 |
|---|---:|
| `very_high` | 90 |
| `high` | 75 |
| `medium` | 55 |
| `low` | 35 |
| `reject` | 15 |

再根据以下因素修正：

- `value_trap_probability = high`：扣分或封顶；
- `risk_level >= 4`：扣分或封顶；
- `confidence < 0.6`：扣分；
- `data_missing` 关键字段过多：扣分；
- `opportunity_quality`、`valuation_attractiveness`、`fundamental_quality` 较高：小幅加分。

最终原则：**LLM 负责判断，Python 负责把判断稳定转换成分数。**

### 7.5 final_priority：今日最终研究排序

推荐公式：

```text
final_priority = llm_priority_score * 0.7
               + bottom_signal_score * 0.2
               + attention_score * 0.1
```

如果某只股票当天没有被 Agent 重新分析，则沿用最近一次 `llm_priority_score`，再结合当日新的 `bottom_signal_score` 和 `attention_score` 更新排序。

MVP 阶段也可以先简化为：

```text
final_priority = llm_priority_score
```

但 `bottom_signal_score` 和 `attention_score` 仍应保留，便于解释排序和触发重评。

---

## 8. 每日运行流程

每日流程分成两个阶段：**Python 预处理阶段** 和 **Agent 研究阶段**。

### 8.1 Python 预处理阶段：Agent 启动前完成

```text
1. Python 根据股票所属市场，通过 Tushare 分别拉取港股、美股、A 股对应的行情、估值、财务、公告、风险等数据
2. Python 复用旧版寻找底部逻辑，计算阶段高点、回撤、RSI、MACD、布林带、BIAS、量比
3. 根据价格回撤和技术条件完成候选池硬筛选
4. 计算 bottom_signal_score
5. 提取估值、财务、现金流、资产负债、风险字段，生成 StockSnapshot
6. 对比昨日或上次快照，生成 ChangeEvent
7. 更新候选池状态：新进、维持、退出、风险警报
8. 计算 attention_score
9. 将所有快照、信号卡、变化事件、池状态、历史结论写入数据库
10. 启动 Agent 可调用的 Python 工具服务
```

### 8.2 Agent 研究阶段：LLM 驱动流程

```text
1. Agent 调用 get_candidate_pool() 获取今日候选池和池状态
2. Agent 根据候选池、变化事件和历史结论，决定哪些股票需要完整分析、增量分析、复用旧结论或剔除
3. Agent 对目标股票调用 get_stock_snapshot()、get_signal_card()、get_change_events()、get_previous_analysis()
4. Agent 基于工具返回的结构化数据完成研究判断
5. Agent 输出 research_priority、分项判断、风险、矛盾点、后续观察点等结构化结论
6. Python 工具校验 Agent 输出，并调用 map_llm_priority_score() 生成 llm_priority_score
7. Python 工具保存分析结论
8. Agent 调用 generate_report() 或使用报告工具生成每日研究优先级报告
```

---

## 9. Agent 调用策略

不是所有股票每天都需要 Agent 做完整分析。Agent 可以根据 Python 工具返回的数据自主决定任务类型。

| 场景 | Agent 处理方式 | 任务类型 |
|---|---|---|
| 新触发底部筛选，且通过基础风险过滤 | 调用快照、信号卡、财务摘要和历史数据，做完整研究 | full_analysis |
| 技术信号不足，或基础风险过滤不通过 | 记录拒绝原因，不做完整研究 | reject |
| 老池无重大变化 | 调用历史结论并复用 | reuse_previous |
| 老池价格小幅波动 | 通常复用旧结论，必要时补充一句变化说明 | reuse_previous |
| 老池估值分位明显变化 | 调用变化事件和快照，做增量分析 | incremental_analysis |
| 老池出现业绩预告 | 调用公告摘要和财务摘要，做增量或完整分析 | incremental_analysis 或 full_analysis |
| 老池发布财报 | 调用最新财报摘要，做完整重评 | full_reanalysis |
| 老池出现重大风险 | 调用风险事件，做完整重评或下调 | full_reanalysis |
| 距离上次完整分析超过 30 天 | 调用完整上下文做刷新分析 | refresh_analysis |
| 每日报告汇总 | 基于 Python 已计算的 final_priority 生成解释和摘要 | report_summary |

---

## 10. 最终展示结构

最终日报不应该只是“今日新增股票”，而应该是全池优先级报告。

建议包含：

1. 今日最值得关注 Top 10；
2. LLM 研究优先级较高但今日无重大变化的股票；
3. 今日新增候选；
4. 今日上调股票；
5. 今日下调股票；
6. 风险警报；
7. 退出候选池股票；
8. 需要完整重评的股票；
9. 数据缺失或需要人工核查的股票。

示例结构：

```json
{
  "date": "2026-06-10",
  "summary": {
    "total_pool_count": 42,
    "new_candidates": 5,
    "removed_candidates": 3,
    "reanalysis_count": 7,
    "major_risk_count": 1
  },
  "top_priority": [],
  "high_priority_unchanged": [],
  "new_candidates": [],
  "upgraded": [],
  "downgraded": [],
  "risk_alerts": [],
  "removed_candidates": []
}
```

---

## 11. Agent 设计

这一部分不再写单次提示词模板，而是定义 Agent 的角色、运行循环、工具调用方式和输出契约。具体提示词可以后续根据所选 Agent 框架再落地。

### 11.1 Agent 定位

Agent 是每日研究流程的驱动者，职责不是计算指标，也不是直接处理原始行情，而是基于 Python 工具提供的结构化事实完成研究判断。

Agent 的核心目标：

1. 从港股、美股、A 股统一候选池中找出最值得进一步研究的股票；
2. 区分“技术底部机会”和“基本面恶化导致的价值陷阱”；
3. 决定哪些股票需要完整分析、增量分析、复用旧结论或剔除；
4. 输出结构化研究结论，供 Python 映射 `llm_priority_score`；
5. 生成每日研究优先级报告。

Agent 的边界：

- 不计算 RSI、MACD、回撤、布林带、BIAS、量比；
- 不直接读取全量原始 K 线或完整财报明细；
- 不自行联网补数据；
- 不直接输出买入、卖出等交易指令；
- 不直接生成 `llm_priority_score`，只输出结构化判断。

### 11.2 Agent 运行循环

Agent 每日启动后的推荐流程：

```text
1. 调用 get_candidate_pool() 获取今日候选池、老池、退出池和风险池
2. 调用 search_stocks(filters) 初步查看各市场中最值得关注的分组
3. 对重点股票调用 get_stock_snapshot(code)、get_signal_card(code)、get_change_events(code)
4. 对老池股票调用 get_previous_analysis(code)
5. 判断任务类型：full_analysis / incremental_analysis / reuse_previous / reject / full_reanalysis
6. 对需要分析的股票输出结构化研究结论
7. 调用 map_llm_priority_score(llm_output) 生成稳定映射分
8. 调用 save_analysis(code, analysis) 保存结论
9. 调用 generate_report() 生成最终日报
```

### 11.3 Agent 决策策略

| 场景 | Agent 行为 | 任务类型 |
|---|---|---|
| 新进入候选池，且基础风险可接受 | 获取完整上下文，做完整分析 | `full_analysis` |
| 新进入候选池，但风险或数据缺失严重 | 记录拒绝或暂缓原因 | `reject` |
| 老池无重大变化 | 复用旧结论，必要时补充一句变化说明 | `reuse_previous` |
| 老池估值、财务、风险事件发生变化 | 对变化部分做增量分析 | `incremental_analysis` |
| 发布财报、重大公告、重大风险事件 | 重新获取完整上下文并重评 | `full_reanalysis` |
| 距离上次完整分析超过 30 天 | 刷新完整分析 | `refresh_analysis` |
| 生成日报 | 读取 `final_priority` 排序并解释原因 | `report_summary` |

### 11.4 Agent 工具调用原则

Agent 应遵循以下工具调用顺序：

1. 先看池子：`get_candidate_pool()`；
2. 再看变化：`get_change_events(code)`；
3. 再看事实：`get_stock_snapshot(code)`；
4. 再看技术底部信号：`get_signal_card(code)`；
5. 老池股票必须看历史结论：`get_previous_analysis(code)`；
6. 输出判断后交给 Python 映射分数：`map_llm_priority_score(llm_output)`；
7. 最后保存和生成报告：`save_analysis()`、`generate_report()`。

Agent 不应绕过工具直接推断缺失字段。如果工具返回 `data_missing`，必须在结论中保留该信息。

### 11.5 Agent 结构化输出契约

Agent 对单只股票的研究输出必须是结构化 JSON，字段建议如下：

```json
{
  "code": "00700.HK",
  "task_type": "full_analysis",
  "decision": "值得进一步研究",
  "research_priority": "high",
  "opportunity_quality": 4,
  "valuation_attractiveness": 4,
  "fundamental_quality": 4,
  "risk_level": 2,
  "value_trap_probability": "medium",
  "main_positive_points": [],
  "main_risks": [],
  "key_contradictions": [],
  "data_missing": [],
  "suggested_follow_up": [],
  "confidence": 0.75
}
```

字段约束：

- `research_priority` 只能是 `very_high`、`high`、`medium`、`low`、`reject`；
- `opportunity_quality`、`valuation_attractiveness`、`fundamental_quality`、`risk_level` 为 1-5；
- `risk_level` 越高表示风险越大；
- `value_trap_probability` 只能是 `low`、`medium`、`high`；
- `confidence` 范围为 0-1；
- 不输出 `llm_priority_score`。

### 11.6 Agent 日报输出契约

Agent 生成日报时，不重新打分，只解释 Python 已计算的 `final_priority` 排序。

日报结构建议：

```json
{
  "date": "2026-06-10",
  "summary": "今日多市场候选池整体变化摘要",
  "top_priority": [],
  "new_candidates": [],
  "upgraded": [],
  "downgraded": [],
  "risk_alerts": [],
  "reuse_previous": [],
  "data_missing_review": []
}
```

日报必须说明：

- 哪些股票是因为基本面改善或估值进入合理区间而上升；
- 哪些股票只是技术底部信号增强，但基本面仍需谨慎；
- 哪些股票存在价值陷阱风险；
- 哪些结论因为数据缺失需要人工复核。

---

## 12. 数据库设计建议

MVP 阶段 SQLite 足够，后续可升级到 PostgreSQL。

### 12.1 stock_daily_snapshot

用于保存每日快照。

字段建议：

```text
date
code
name
market
industry
price
pct_chg
cycle_high_price
cycle_high_date
drawdown_from_high_pct
distance_from_low_pct
price_percentile_1y
rsi_14
weekly_rsi
macd_hist
macd_divergence
bollinger_position_pct
bias_120
volume_ratio
alert_level
bottom_signal_score
pe_ttm
pb
dividend_yield
market_cap
turnover_rate
main_net_inflow_5d
financial_summary_json
risk_flags_json
data_missing_json
news_count
announcement_count
```

### 12.2 stock_pool_state

用于维护观察池状态。

字段建议：

```text
date
code
pool_status
first_seen_date
last_seen_date
days_in_pool
last_full_analysis_date
```

### 12.3 stock_change_event

用于保存变化事件。

字段建议：

```text
date
code
event_type
event_level
event_desc
attention_impact
need_reanalysis
```

### 12.4 stock_llm_analysis

用于保存 LLM 分析结论。

字段建议：

```text
code
analysis_date
task_type
decision
research_priority
opportunity_quality
valuation_attractiveness
fundamental_quality
risk_level
value_trap_probability
confidence
llm_priority_score
bottom_signal_score
attention_score
final_priority
main_logic
main_positive_points
main_risks
key_contradictions
raw_llm_output
```

---

## 13. MVP 落地路线

### 第一阶段：Python 数据准备层可用

目标：先跑通 Agent 启动前的数据准备能力。

需要实现：

1. 先冻结 `Tushare 数据请求清单` 和 `Python 数据清洗与加工清单`，明确各市场接口、字段、主键、增量参数、缺失标记；
2. 从配置中读取港股、美股、A 股观察列表；
3. 根据股票所属市场，通过 Tushare 拉取对应日线行情、估值、财务、风险和持仓资金数据；
4. 复用旧版 `find_cycle_high`、RSI、周线 RSI、MACD、布林带、BIAS、量比等逻辑；
5. 根据回撤和技术条件生成候选池；
6. 计算 `bottom_signal_score`；
7. 根据股票所属市场拉取并清洗对应估值、基础财务、现金流、负债等摘要字段；
8. 生成 StockSnapshot、SignalCard、ChangeEvent；
9. 保存快照、候选池、变化事件和历史状态。

### 第二阶段：Agent 工具层可用

目标：让 Agent 能通过 Python 工具访问所有预处理数据。

需要实现：

1. `get_candidate_pool()`；
2. `get_stock_snapshot(code)`；
3. `get_signal_card(code)`；
4. `get_change_events(code)`；
5. `get_previous_analysis(code)`；
6. `search_stocks(filters)`；
7. `map_llm_priority_score(llm_output)`；
8. `save_analysis(code, analysis)`；
9. `generate_report()`。

### 第三阶段：Agent 研究闭环

目标：由 LLM Agent 驱动完整研究流程，而不是由 Python 主程序硬编码调用顺序。

需要实现：

1. Agent 启动后读取候选池和变化事件；
2. Agent 自主决定完整分析、增量分析、复用旧结论或剔除；
3. Agent 调用 Python 工具获取所需上下文；
4. Agent 输出结构化研究判断；
5. Python 工具校验输出并映射 `llm_priority_score`；
6. Python 工具保存结论并计算 `final_priority`；
7. Agent 生成今日研究优先级报告。

### 第四阶段：专业增强

目标：构建更完整的股票研究系统。

可以增加：

1. 港股、美股、A 股三类市场的财务字段完整映射和缺失字段兜底；
2. 港股通持股、机构持仓、内部人交易等港股相关数据；
3. 美股 13F、内部人交易、回购、分红等美股相关数据；
4. A 股股东人数、质押、解禁、资金流向等 A 股相关数据；
5. 各市场行业估值对比；
6. 财报发布后强制重评；
7. 周报、月报；
8. 风险预警机制。

---

## 14. 最终系统定位

这个系统不应该定位为“自动推荐买入股票”，而应该定位为：

```text
深度回撤股票的自动研究优先级系统
```

它的核心价值是：

- 通过 Tushare 统一获取港股、美股、A 股数据，并按股票所属市场使用对应接口；
- Agent 启动前用 Python 完成数据清洗、技术指标、底部信号、变化事件和入库；
- Agent 启动后由 LLM 驱动研究流程，而不是由 Python 主程序硬编码完整决策；
- Agent 通过 Python 工具查询结构化事实，而不是直接处理原始 K 线和财报明细；
- 用财务、估值、现金流和风险数据帮助 Agent 排除价值陷阱；
- 不再用 Python 财务硬评分替代研究判断；
- 用变化检测减少重复分析；
- 用 Agent 提供综合判断、研究优先级和日报表达；
- 帮助使用者把有限精力集中在最值得进一步研究的股票上。

最终架构可以总结为：

```text
Tushare 以 10000 积分档提供港股、美股、A 股数据，按股票所属市场选择对应接口
Python 在 Agent 启动前完成数据清洗、指标计算、底部筛选、变化检测和入库
Python 暴露工具接口供 Agent 查询结构化事实和保存结论
LLM Agent 驱动研究流程，决定分析顺序、分析深度和复用策略
Agent 输出研究判断、价值陷阱识别和结构化结论
Python 工具映射 llm_priority_score 并汇总 final_priority
Agent/模板负责最终展示
```

---

## 15. 关键结论

1. 当前统一采用 Tushare 作为第一数据源，并明确可使用 10000 积分档；港股、美股、A 股在系统中同等处理，股票属于哪个市场就使用哪个市场的数据接口。
2. 系统采用 LLM Agent 驱动架构，不是 Python 主程序按固定流程调用 LLM 的架构。
3. Agent 启动前，Python 应完成数据获取、清洗、技术指标、底部筛选、变化检测、结构化摘要和入库。
4. 旧版寻找底部逻辑需要保留，作为候选池入口和技术底部信号来源。
5. Python 应提供稳定工具接口，供 Agent 查询 StockSnapshot、ChangeEvent、SignalCard、历史分析和候选池。
6. Python 不再通过财务指标硬算 `value_score`，财务数据只整理成结构化事实交给 Agent。
7. Agent 应负责流程驱动、语义理解、价值陷阱识别、矛盾信号判断、研究优先级等级和解释表达。
8. Python 负责将 Agent 的结构化判断映射为 `llm_priority_score`。
9. 每日运行应重点关注变化，而不是每天重复完整结论。
10. 新进股票不应天然置顶，应该进入统一优先级排序。
11. 核心排序公式建议为：`final_priority = llm_priority_score * 0.7 + bottom_signal_score * 0.2 + attention_score * 0.1`。
12. 中间层的关键是标准化数据结构和 Agent 工具：StockSnapshot、ChangeEvent、SignalCard、candidate/query/save/report tools。
13. 最终输出应是研究优先级报告，而不是直接投资建议。
