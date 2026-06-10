# Python 代码结构设计

> 版本：2026-06-10  
> 对应方案：`股票LLM价值筛选系统方案.md`  
> 系统定位：Python 负责数据准备、指标计算、缓存入库和 Agent 工具层；LLM Agent 负责研究流程驱动和综合判断。

---

## 1. 设计目标

本项目的 Python 代码不负责替代 Agent 做研究判断，而是负责提供稳定、可复现、可追溯的数据基础。

核心目标：

1. 从 Tushare 拉取港股、美股、A 股数据，并按股票所属市场使用对应接口；
2. 对原始数据做缓存，避免 Agent 运行时频繁请求 Tushare；
3. 对行情、财务、估值、风险数据做清洗和标准化；
4. 复用旧版寻找底部逻辑，计算回撤、RSI、MACD、布林带、BIAS、量比等技术指标；
5. 生成 `StockSnapshot`、`SignalCard`、`ChangeEvent` 等结构化事实；
6. 提供 Agent 可调用的 Python 工具；
7. 保存 Agent 分析结论，并映射 `llm_priority_score`、计算 `final_priority`。

---

## 2. 总体架构

```text
Tushare API
  ↓
Data Fetcher：增量拉取数据
  ↓
Raw Cache：原始接口结果缓存
  ↓
Data Cleaner：字段清洗、代码标准化、缺失处理
  ↓
Indicator Engine：技术指标与底部信号计算
  ↓
Snapshot Builder：生成结构化事实快照
  ↓
SQLite / Parquet Storage
  ↓
Agent Tools：供 LLM Agent 查询和写入
  ↓
LLM Agent
```

Python 分成两类能力：

| 类型 | 说明 |
|---|---|
| 离线预处理能力 | Agent 启动前执行，负责拉取、清洗、计算、缓存、入库 |
| Agent 工具能力 | Agent 运行时调用，负责查询结构化事实、保存结论、生成报告 |

---

## 3. 推荐目录结构

```text
newstock/
├── config/
│   ├── config.yaml                    # 主配置：Tushare token、市场、观察列表、路径
│   └── logging.yaml                   # 日志配置
│
├── data_fetcher/
│   ├── tushare_client.py              # Tushare API 封装
│   ├── market_fetcher.py              # 港股/美股/A股统一拉取入口
│   ├── incremental_fetcher.py         # 增量更新逻辑
│   └── fetch_state.py                 # 记录每个接口、每只股票的最后更新时间
│
├── cache/
│   ├── raw_cache.py                   # 原始数据缓存读写
│   ├── parquet_store.py               # Parquet 文件存储
│   └── cache_policy.py                # 缓存策略、覆盖窗口、去重策略
│
├── processing/
│   ├── cleaner.py                     # 字段清洗、类型转换、缺失值处理
│   ├── code_mapper.py                 # 股票代码标准化：港股/美股/A股
│   ├── calendar.py                    # 交易日、日期窗口处理
│   └── validators.py                  # 数据质量校验
│
├── indicators/
│   ├── technical.py                   # RSI、MACD、布林带、BIAS、量比
│   ├── cycle_high.py                  # 阶段高点与回撤计算
│   ├── bottom_signal.py               # bottom_signal_score 计算
│   └── price_screen.py                # 候选池硬筛选
│
├── snapshot/
│   ├── snapshot_builder.py            # 生成 StockSnapshot
│   ├── signal_builder.py              # 生成 SignalCard
│   └── change_detector.py             # 生成 ChangeEvent
│
├── storage/
│   ├── db.py                          # SQLite 连接与初始化
│   ├── schema.sql                     # 表结构
│   ├── repositories.py                # CRUD 封装
│   └── migrations/                    # 后续数据库迁移
│
├── agent_tools/
│   ├── candidate_tools.py             # get_candidate_pool / search_stocks
│   ├── snapshot_tools.py              # get_stock_snapshot
│   ├── signal_tools.py                # get_signal_card
│   ├── change_tools.py                # get_change_events
│   ├── analysis_tools.py              # get_previous_analysis / save_analysis
│   ├── scoring_tools.py               # map_llm_priority_score / final_priority
│   └── report_tools.py                # generate_report
│
├── pipelines/
│   ├── daily_prepare.py               # 每日预处理主流程
│   ├── init_history.py                # 首次历史数据初始化
│   └── rebuild_snapshot.py            # 用缓存重建快照
│
├── report/
│   └── report_generator.py            # 日报文件生成
│
├── tests/
│   ├── test_incremental_fetcher.py
│   ├── test_indicators.py
│   ├── test_snapshot_builder.py
│   └── test_agent_tools.py
│
└── main.py                            # CLI 入口
```

---

## 4. 数据更新与缓存策略

### 4.1 核心原则

Python 必须从 Tushare 拉取并缓存数据。Agent 不直接访问 Tushare。

原因：

1. 保证同一次 Agent 研究使用同一批数据；
2. 避免 Agent 多轮调用时重复请求 Tushare；
3. 避免频率限制和接口不稳定；
4. 支持变化检测、历史复盘和问题排查；
5. 支持用原始缓存重新计算指标或重建快照。

### 4.2 增量更新规则

数据更新必须支持增量拉取。

规则：

```text
如果本地已有历史数据：
    从本地最后一个交易日开始拉取，到今天为止
    拉取后按 code + trade_date 去重，保留最新记录

如果本地没有历史数据：
    从配置的 earliest_start_date 开始全量初始化
```

示例：

```text
本地已有数据到 2026-06-08
今天是 2026-06-10
则只需要拉取 2026-06-08 ~ 2026-06-10 的数据
再和本地数据合并去重
```

这里故意从 `last_date` 当天开始，而不是从 `last_date + 1` 开始，原因是：

1. 避免上次最后一天数据不完整；
2. 兼容 Tushare 对最近交易日数据的修正；
3. 简化缺口修复逻辑。

### 4.3 覆盖窗口策略

不同数据类型可以使用不同覆盖窗口。

| 数据类型 | 更新策略 |
|---|---|
| 日线行情 | 从 `last_trade_date` 开始增量拉取，合并去重 |
| 复权因子 | 从 `last_trade_date` 开始增量拉取，必要时回看 30 天 |
| 财务报表 | 按报告期增量更新，最近 2-4 个报告期可重复拉取 |
| 财务指标 | 按报告期增量更新，最近 2-4 个报告期可重复拉取 |
| 基础信息 | 每日或每周全量刷新 |
| 风险事件 | 从最近一次事件日期开始增量拉取 |
| 港股/美股字段映射 | 每次启动时校验，变更时更新 |

### 4.4 首次初始化策略

如果本地没有数据，则按市场初始化。

建议默认窗口：

| 市场 | 行情历史 | 财务历史 |
|---|---:|---:|
| 港股 | 5 年 | 5 年 |
| 美股 | 5 年 | 5 年 |
| A 股 | 5 年 | 5 年 |

配置示例：

```yaml
data:
  earliest_start_date: "2019-01-01"
  markets:
    hk:
      enabled: true
      priority: 1
    us:
      enabled: true
      priority: 1
    cn:
      enabled: true
      priority: 1
```

---

### 4.5 Tushare 数据请求清单

本节定义 Python 在 Agent 启动前必须向 Tushare 请求的数据契约。原则是：**按股票所属市场调用对应接口，原始字段可不同，清洗后的结构必须统一，缺失字段必须显式标记。**

统一处理链路：

```text
raw_tushare_data
  -> market_adapter：港股 / 美股 / A 股字段适配
  -> cleaner：类型、日期、代码、异常值清洗
  -> indicator_engine：技术指标、TTM、估值分位、风险标签计算
  -> unified_stock_snapshot：Agent 可查询结构化事实
```

#### 4.5.1 请求分层与更新频率

| 数据层 | 目的 | 更新频率 | 是否直接给 Agent |
|---|---|---|---|
| 基础信息 | 股票身份、上市状态、行业、交易所、币种 | 每日或每周全量 | 清洗后进入 `basic` |
| 日线行情 | 价格、成交量、回撤、技术指标 | 每日增量 | 只给计算结果，不给全量 K 线 |
| 复权因子 | 复权价格，避免除权影响技术指标 | 每日增量，回看 30 天 | 不直接给，只给复权后指标 |
| 每日估值 | PE、PB、PS、股息率、市值、换手率 | 每日增量 | 清洗后进入 `valuation` |
| 财务指标 | ROE、利润率、成长性、偿债能力 | 财报期增量，最近 4 期覆盖 | 清洗后进入 `fundamental` |
| 三大报表 | 收入、利润、资产、负债、现金流 | 财报期增量，最近 4 期覆盖 | 只给摘要和派生指标 |
| 事件风险 | ST、停牌、质押、解禁、预告、分红等 | 每日增量 | 清洗后进入 `risk` / `change_event` |
| 资金持仓 | 主力资金、港股通持股、股东人数等 | 每日或按披露增量 | 清洗后进入 `capital_flow` |

#### 4.5.2 港股请求清单

##### 港股基础信息：`hk_basic`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code` 可选；全量初始化时不传；也可按观察列表逐只过滤 |
| 主键 | `ts_code` |
| 更新频率 | 每周全量，或每日轻量校验 |
| 用途 | 识别港股代码、公司名称、上市状态、交易币种、每手股数 |

建议字段：

| Tushare 字段 | 清洗后字段 | 用途 |
|---|---|---|
| `ts_code` | `code` / `raw_ts_code` | 代码标准化与回溯 |
| `name` | `name` | 股票简称 |
| `fullname` | `full_name` | 公司全称 |
| `enname` | `english_name` | 英文名 |
| `market` | `exchange_board` | 市场或板块 |
| `list_status` | `list_status` | 上市状态 |
| `list_date` | `list_date` | 上市日期 |
| `delist_date` | `delist_date` | 退市日期 |
| `trade_unit` | `trade_unit` | 每手股数 |
| `curr_type` | `currency` | 交易币种 |
| `isin` | `isin` | ISIN 编码 |

##### 港股日线行情：`hk_daily`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`start_date`、`end_date` |
| 主键 | `ts_code + trade_date` |
| 更新频率 | 每日增量，从本地 `last_trade_date` 当天开始覆盖 |
| 用途 | 价格位置、回撤、均线、RSI、MACD、布林带、BIAS、量比 |

建议字段：

| Tushare 字段 | 清洗后字段 | 用途 |
|---|---|---|
| `trade_date` | `date` | 交易日 |
| `open` / `high` / `low` / `close` | `open` / `high` / `low` / `close` | OHLC |
| `pre_close` | `pre_close` | 前收盘价 |
| `change` | `price_change` | 涨跌额 |
| `pct_chg` | `pct_chg` | 涨跌幅 |
| `vol` | `volume` | 成交量 |
| `amount` | `amount` | 成交额 |

Python 必须派生：`current_price`、`cycle_high_price`、`cycle_high_date`、`drawdown_from_high_pct`、`high_52w`、`low_52w`、`distance_from_low_pct`、`price_percentile_1y`、`ma20`、`ma60`、`ma120`、`rsi_14`、`weekly_rsi`、`macd_dif`、`macd_dea`、`macd_hist`、`bollinger_position_pct`、`bias_120`、`volume_ratio`、`bottom_signal_score`。

##### 港股复权和数据增强

官方提供以下我们应优先使用的接口：

| 接口 | 官方描述 | 用途 |
|---|---|---|
| `hk_daily_adj` | 港股复权行情，提供股本、市值、换手率等 | **推荐作为港股价格主数据源**，避免拆股/分红导致技术指标失真 |
| `hk_adjfactor` | 港股每日复权因子 | 备选方案，用于从 `hk_daily` 自行计算复权价格 |
| `hk_tradecal` | 港股交易日历 | 交易日校验，避免用 A 股节假日误判港股停盘 |

技术指标优先使用 `hk_daily_adj` 的复权价格；若使用 `hk_daily + hk_adjfactor` 组合，则按 `price * adj_factor / latest_adj_factor` 计算复权 OHLC。

##### 港股财务数据

| 数据类型 | 接口 | 请求参数 | 主键 | 用途 |
|---|---|---|---|---|
| 利润表 | `hk_income` | `ts_code`、`period` 或日期区间 | `ts_code + end_date + report_type` | 收入、利润、利润率 |
| 资产负债表 | `hk_balancesheet` | `ts_code`、`period` 或日期区间 | `ts_code + end_date + report_type` | 资产、负债、权益、商誉 |
| 现金流量表 | `hk_cashflow` | `ts_code`、`period` 或日期区间 | `ts_code + end_date + report_type` | 经营现金流、资本开支、自由现金流 |
| 财务指标 | `hk_fina_indicator` | `ts_code`、`period` 或日期区间 | `ts_code + end_date` | ROE、ROA、毛利率、净利率、成长性 |

清洗后重点字段：

| 目标字段 | 来源 | 处理规则 |
|---|---|---|
| `revenue_ttm` | `hk_income` | 最近 4 个季度合计；缺季度则标记 `ttm_missing` |
| `net_profit_ttm` | `hk_income` | 最近 4 个季度合计 |
| `gross_margin` | `hk_fina_indicator` 或利润表计算 | 优先使用指标接口，缺失则计算 |
| `net_margin` | `hk_fina_indicator` 或利润表计算 | 净利润 / 收入 |
| `roe` / `roa` | `hk_fina_indicator` | 缺失时尝试由净利润和权益/资产计算 |
| `revenue_yoy` / `net_profit_yoy` | 指标接口或 Python 计算 | 同报告期同比 |
| `ocf_ttm` | `hk_cashflow` | 最近 4 个季度经营现金流 |
| `free_cash_flow_ttm` | `hk_cashflow` | `ocf_ttm - capex_ttm`，若 capex 缺失标记估算失败 |
| `ocf_to_net_profit` | Python 计算 | 净利润为 0 或负数时标记异常 |
| `debt_to_asset` | `hk_balancesheet` 或指标接口 | 总负债 / 总资产 |
| `current_ratio` / `quick_ratio` | `hk_balancesheet` | 字段不足则标记缺失 |
| `goodwill_to_equity` | `hk_balancesheet` | 商誉 / 归母权益 |

##### 港股持股与资金：`hk_hold`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`start_date`、`end_date` |
| 主键 | `ts_code + trade_date` |
| 用途 | 港股通持股比例和变化趋势 |

清洗后字段：`southbound_hold_ratio`、`southbound_hold_change_5d`、`southbound_hold_change_20d`。若港股质押、审计意见、重大诉讼等字段无法从 Tushare 稳定获得，必须写入 `data_missing`，例如 `hk_pledge_ratio_missing`、`audit_opinion_missing`、`major_lawsuit_missing`。

#### 4.5.3 美股请求清单

##### 美股基础信息：`us_basic`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code` 或全量 |
| 主键 | `ts_code` |
| 更新频率 | 每周全量，或每日轻量校验 |
| 用途 | 美股代码、公司名称、交易所、上市状态、行业、币种 |

建议字段：`ts_code`、`symbol`、`name`、`enname`、`exchange`、`list_date`、`delist_date`、`list_status`、`industry`、`currency`。清洗后进入 `code`、`name`、`market = US`、`exchange`、`industry`、`listed_years`、`currency = USD`。

##### 美股日线行情与估值：`us_daily`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`start_date`、`end_date` |
| 主键 | `ts_code + trade_date` |
| 更新频率 | 每日增量 |
| 用途 | 价格位置、技术指标、部分估值字段 |

建议字段：`ts_code`、`trade_date`、`open`、`high`、`low`、`close`、`pre_close`、`change`、`pct_chg`、`vol`、`amount`、`vwap`、`pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`、`total_mv`。

Python 必须派生：`current_price`、`drawdown_from_high_pct`、`price_percentile_1y`、`rsi_14`、`weekly_rsi`、`macd_hist`、`bollinger_position_pct`、`bias_120`、`volume_ratio`、`pe_ttm`、`pb`、`ps_ttm`、`market_cap`。

##### 美股复权和数据增强

官方提供以下接口：

| 接口 | 官方描述 | 用途 |
|---|---|---|
| `us_daily_adj` | 美股复权行情，提供股本、市值、复权因子和成交信息 | **推荐作为美股价格主数据源**，技术指标用复权价 |
| `us_adjfactor` | 美股每日复权因子 | 备选方案 |
| `us_tradecal` | 美股交易日历 | 交易日校验 |
| `us_fina_indicator` | 美股财务指标数据，覆盖主要美股和中概股 | ROE、ROA、毛利率等关键指标的直接来源 |

技术指标优先使用 `us_daily_adj` 的复权价格；财务指标优先使用 `us_fina_indicator` 接口，缺失时从三大报表派生。当前美股财务数据仅覆盖主要美股和中概股，非覆盖范围内的股票必须写入 `data_missing`。

##### 美股财务数据

| 数据类型 | 接口 | 主键 | 用途 |
|---|---|---|---|
| 利润表 | `us_income` | `ts_code + end_date + report_type` | 收入、利润、EPS、利润率 |
| 资产负债表 | `us_balancesheet` | `ts_code + end_date + report_type` | 资产、负债、权益、商誉 |
| 现金流量表 | `us_cashflow` | `ts_code + end_date + report_type` | OCF、Capex、FCF |

清洗后重点字段：`revenue_ttm`、`net_profit_ttm`、`eps_ttm`、`gross_margin`、`operating_margin`、`net_margin`、`roe`、`roa`、`revenue_yoy`、`net_profit_yoy`、`ocf_ttm`、`capex_ttm`、`free_cash_flow_ttm`、`ocf_to_net_profit`、`debt_to_asset`、`current_ratio`、`goodwill_to_equity`。

美股特殊规则：

1. 财报口径和 A 股不同，不强行套 A 股字段名，先进入 `market_adapter`；
2. Tushare 未直接提供的指标，由 Python 从三大报表派生；
3. 行业分类缺失时写入 `industry_missing`；
4. 拆股或复权字段缺失时，价格相关信号写入 `low_confidence_price_signal`。

#### 4.5.4 A 股请求清单

##### A 股基础信息：`stock_basic`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code` 可选；全量初始化时不传；可指定 `list_status` |
| 主键 | `ts_code` |
| 更新频率 | 每日或每周全量 |
| 用途 | 股票身份、行业、上市状态、交易所、是否沪深港通 |

建议字段：`ts_code`、`symbol`、`name`、`area`、`industry`、`market`、`exchange`、`list_status`、`list_date`、`delist_date`、`is_hs`。清洗后进入 `code`、`name`、`market = CN`、`exchange`、`industry`、`is_st`、`listed_years`。

##### A 股日线行情：`daily`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`start_date`、`end_date` |
| 主键 | `ts_code + trade_date` |
| 更新频率 | 每日增量 |
| 用途 | OHLCV、回撤、技术指标、候选池筛选 |

建议字段：`ts_code`、`trade_date`、`open`、`high`、`low`、`close`、`pre_close`、`change`、`pct_chg`、`vol`、`amount`。

##### A 股复权因子：`adj_factor`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`start_date`、`end_date` |
| 主键 | `ts_code + trade_date` |
| 更新频率 | 每日增量，回看 30 天 |
| 用途 | 计算 `adj_open`、`adj_high`、`adj_low`、`adj_close` |

技术指标优先使用复权价格；如果某日缺少复权因子，则该日技术指标标记 `adj_factor_missing`。

##### A 股每日估值：`daily_basic`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`trade_date` 或日期区间 |
| 主键 | `ts_code + trade_date` |
| 更新频率 | 每日增量 |
| 用途 | 估值、市值、换手率、股息率 |

建议字段：`close`、`turnover_rate`、`turnover_rate_f`、`volume_ratio`、`pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`、`dv_ratio`、`dv_ttm`、`total_share`、`float_share`、`free_share`、`total_mv`、`circ_mv`。

清洗后进入：`pe_ttm`、`pb`、`ps_ttm`、`dividend_yield_ttm`、`market_cap`、`float_market_cap`、`turnover_rate`、`volume_ratio`、`pe_percentile_5y`、`pb_percentile_5y`、`industry_pe_median`、`industry_pb_median`。

##### A 股财务指标：`fina_indicator`

| 项目 | 说明 |
|---|---|
| 请求参数 | `ts_code`、`period` 或日期区间 |
| 主键 | `ts_code + end_date` |
| 更新频率 | 财报期增量，最近 4 期覆盖刷新 |
| 用途 | 盈利能力、成长性、现金流质量、偿债能力 |

建议字段：`ann_date`、`end_date`、`roe`、`roe_waa`、`roa`、`grossprofit_margin`、`netprofit_margin`、`debt_to_assets`、`current_ratio`、`quick_ratio`、`ocf_to_profit`、`basic_eps_yoy`、`op_yoy`、`netprofit_yoy`、`or_yoy`、`assets_yoy`、`bps`、`ocfps`。

##### A 股三大报表

| 数据类型 | 接口 | 主键 | 重点清洗字段 |
|---|---|---|---|
| 利润表 | `income` | `ts_code + end_date + report_type` | `revenue_ttm`、`net_profit_ttm`、`gross_profit_ttm`、费用率 |
| 资产负债表 | `balancesheet` | `ts_code + end_date + report_type` | `total_assets`、`total_liabilities`、`shareholder_equity`、应收、存货、商誉、有息负债 |
| 现金流量表 | `cashflow` | `ts_code + end_date + report_type` | `ocf_ttm`、`capex_ttm`、`free_cash_flow_ttm`、`ocf_to_net_profit` |

##### A 股事件、风险、资金和股东

| 接口 | 请求参数 | 用途 | 清洗后字段 |
|---|---|---|---|
| `stock_st` | `ts_code` 或 `trade_date` | ST 股票列表，可按日期获取历史 ST 状态 | `is_st`、`st_date`（优先用此接口，比 `namechange` 更准确） |
| `suspend_d` | `ts_code`、日期区间 | 停复牌 | `is_suspended` |
| `namechange` | `ts_code`、日期区间 | 名称变更（ST 之外的原因、退市等） | `is_delisting_risk` |
| `forecast` | `ts_code`、日期区间 | 业绩预告 | `performance_forecast_type`、`performance_forecast_change_pct` |
| `express` | `ts_code`、日期区间 | 业绩快报 | 财报变化事件 |
| `fina_audit` | `ts_code`、报告期 | 财务审计意见 | `audit_opinion_abnormal` |
| `dividend` | `ts_code`、日期区间 | 分红 | `dividend_yield_ttm`、分红稳定性 |
| `moneyflow` | `ts_code`、日期区间 | 主力资金 | `main_net_inflow_5d`、`main_net_inflow_20d` |
| `stk_holdernumber` | `ts_code`、日期区间 | 股东人数 | `holder_number_change_pct` |
| `top10_holders` | `ts_code`、报告期 | 股东结构 | 股东集中度 |
| `pledge_stat` | `ts_code`、日期区间 | 股权质押统计 | `pledge_ratio` |
| `pledge_detail` | `ts_code`、日期区间 | 股权质押明细（可选，补充细节） | 质押明细事件 |
| `share_float` | `ts_code`、日期区间 | 限售解禁 | `unlock_ratio_next_90d` |

#### 4.5.5 增量请求参数规则

| 数据类型 | 初次初始化 | 日常增量 | 覆盖窗口 |
|---|---|---|---|
| 基础信息 | 全量请求 | 全量或按观察列表请求 | 不需要历史覆盖 |
| 日线行情 | `earliest_start_date -> today` | `last_trade_date -> today` | 从最后一天覆盖 |
| 复权因子 | 同行情 | `last_trade_date - 30d -> today` | 回看 30 天 |
| 每日估值 | 同行情 | `last_trade_date -> today` | 从最后一天覆盖 |
| 财务指标 | 最近 5 年报告期 | 从 `last_report_period` 开始 | 最近 4 期覆盖 |
| 三大报表 | 最近 5 年报告期 | 从 `last_report_period` 开始 | 最近 4 期覆盖 |
| 事件风险 | 最近 5 年或配置窗口 | `last_event_date -> today` | 最近 30-90 天可覆盖 |

请求失败时不能覆盖旧缓存；必须更新 `fetch_state.status = failed`，并把接口名写入当日 `data_missing` 或 `data_stale`。

### 4.6 Python 数据清洗与加工清单

本节定义 Python 从 Tushare 原始数据到 Agent 可查询结构化结果之间必须完成的清洗和加工。核心原则：**确定性计算都在 Python 完成，Agent 只看结构化事实、缺失标记和少量摘要。**

#### 4.6.1 股票代码标准化

| 清洗项 | 规则 |
|---|---|
| 去空格 | 所有 `ts_code`、`symbol`、用户配置代码先 `strip()` |
| 统一大小写 | `aapl.us` 转成 `AAPL.US` |
| 后缀校验 | 允许 `.HK`、`.US`、`.SH`、`.SZ`、`.BJ` |
| 市场推断 | `.HK -> HK`，`.US -> US`，`.SH/.SZ/.BJ -> CN` |
| 保留原始代码 | 保存 `raw_ts_code`，方便回溯接口返回 |
| 建立映射 | `raw_ts_code -> code -> market` |

#### 4.6.2 日期和交易日标准化

| 原始字段 | 统一字段 | 说明 |
|---|---|---|
| `trade_date` | `date` | 交易日 |
| `ann_date` | `announcement_date` | 公告日期 |
| `f_ann_date` | `final_announcement_date` | 实际披露日期 |
| `end_date` | `report_period` | 财报报告期 |
| `list_date` | `list_date` | 上市日期 |

规则：

1. 统一输出为 `YYYY-MM-DD`；
2. raw cache 保留 Tushare 原始 `YYYYMMDD`；
3. 不同市场不强行补齐交易日，因为港股、美股、A 股假期不同；
4. 财报必须按披露日可见，不能让 Agent 在历史日期看到未来披露的数据；
5. 每个 `StockSnapshot` 必须带 `data_date`、`last_fetch_at` 和 `source_apis`。

#### 4.6.3 行情数据清洗

| 清洗项 | 处理方式 |
|---|---|
| 重复数据 | 按 `code + trade_date` 去重，保留最后一次拉取结果 |
| 日期排序 | 按交易日升序保存 |
| OHLC 异常 | 检查 `high >= open/close/low`，异常写入 `data_quality_flags` |
| 价格为空 | 不参与技术指标，写入 `price_missing` |
| 成交量为 0 | 判断是否停牌或无成交，写入 `zero_volume` |
| 涨跌幅异常 | 检查是否复权、拆股、除权导致；无法解释则降权 |
| 缺失交易日 | 自动补拉缺口区间，仍失败则写入 `trade_date_gap` |
| 最近数据不完整 | 从 `last_trade_date` 当天重新拉取覆盖 |

#### 4.6.4 价格复权处理

A 股优先使用 `adj_factor` 生成复权价格：

```text
adj_close = close * adj_factor / latest_adj_factor
adj_open  = open  * adj_factor / latest_adj_factor
adj_high  = high  * adj_factor / latest_adj_factor
adj_low   = low   * adj_factor / latest_adj_factor
```

技术指标优先使用复权价格。各市场复权策略：

| 市场 | 主数据源 | 备选方案 | 处理 |
|---|---|---|---|
| A 股 | `daily` + `adj_factor` 自行计算复权 OHLC | 直接使用 `daily` 接口（该接口本身包含前后复权数据） | 优先计算；缺复权因子则降级 |
| 港股 | **`hk_daily_adj`**（官方复权行情接口） | `hk_daily` + `hk_adjfactor` | 优先用复权行情接口 |
| 美股 | **`us_daily_adj`**（官方复权行情接口） | `us_daily` + `us_adjfactor` | 优先用复权行情接口 |

拆股或异常跳变时降低 `bottom_signal_score` 置信度；完整复权数据缺失才写入 `price_adjustment_missing`。

#### 4.6.5 财务报表去重和口径处理

| 问题 | 处理规则 |
|---|---|
| 同一报告期多次披露 | 保留最新 `ann_date` 或 `f_ann_date` |
| 快报和正式财报同时存在 | 正式财报优先，快报作为变化事件保留 |
| 修正公告 | 保留最新版本，写入 `is_revised = true` |
| 报表类型不同 | 保留 `report_type`、`comp_type`，避免混用 |
| 财报可见性 | 以披露日为准，避免未来数据污染回测 |

主键建议：`code + report_period + report_type`。

#### 4.6.6 TTM、同比和质量指标计算

| 指标 | 计算方式 | 异常处理 |
|---|---|---|
| `revenue_ttm` | 最近 4 个季度收入合计 | 少于 4 季度标记 `ttm_missing` |
| `net_profit_ttm` | 最近 4 个季度净利润合计 | 净利润为负时比率类指标谨慎处理 |
| `ocf_ttm` | 最近 4 个季度经营现金流合计 | 字段缺失标记 `cashflow_missing` |
| `capex_ttm` | 最近 4 个季度资本开支合计 | 字段缺失标记 `capex_missing` |
| `free_cash_flow_ttm` | `ocf_ttm - capex_ttm` | capex 缺失则标记估算失败 |
| `eps_ttm` | 最近 4 季度 EPS 合计，或净利润 / 股本估算 | 估算时写入 `data_estimated` |
| `ocf_to_net_profit` | `ocf_ttm / net_profit_ttm` | 分母为 0 或负数时标记异常 |
| `revenue_yoy` | 当前报告期收入 / 去年同期 - 1 | 找不到同期则缺失 |
| `net_profit_yoy` | 当前报告期净利润 / 去年同期 - 1 | 找不到同期则缺失 |

#### 4.6.7 估值分位和行业对比

| 原始字段 | 统一字段 |
|---|---|
| `pe_ttm` | `pe_ttm` |
| `pb` | `pb` |
| `ps_ttm` | `ps_ttm` |
| `dv_ttm` | `dividend_yield_ttm` |
| `total_mv` | `market_cap` |
| `circ_mv` | `float_market_cap` |

Python 需要计算：`pe_percentile_5y`、`pb_percentile_5y`、`ps_percentile_5y`、`industry_pe_median`、`industry_pb_median`、`valuation_vs_industry`。

规则：

1. `pe_ttm <= 0` 不进入 PE 历史分位；
2. `pb <= 0` 不进入 PB 历史分位；
3. 行业对比必须限定同一市场，不能把港股、美股、A 股直接混算；
4. 行业分类缺失时写入 `industry_missing`；
5. 港股/美股估值字段缺失时，可尝试用市值、净利润、净资产派生，不能计算则标记缺失。

#### 4.6.8 技术指标和底部信号计算

Python 必须提前计算并缓存：

| 指标 | 说明 |
|---|---|
| `cycle_high_price` / `cycle_high_date` | 阶段高点和日期 |
| `drawdown_from_high_pct` | 从阶段高点回撤 |
| `high_52w` / `low_52w` | 52 周高低点 |
| `distance_from_low_pct` | 距离低点反弹幅度 |
| `price_percentile_1y` | 一年价格分位 |
| `ma20` / `ma60` / `ma120` | 均线 |
| `rsi_14` / `weekly_rsi` | 日线和周线 RSI |
| `macd_dif` / `macd_dea` / `macd_hist` | MACD |
| `macd_divergence` | 是否底背离 |
| `bollinger_position_pct` | 布林带位置 |
| `bias_120` | 120 日 BIAS |
| `volume_ratio` | 量比 |
| `bottom_signal_score` | 技术底部信号分 |

这些指标不交给 Agent 现算，Agent 只读取结果和 `score_detail`。

#### 4.6.9 风险标签生成

Python 需要生成结构化风险字段和 `risk_flags`。

| 风险字段 | 来源 | 典型标签 |
|---|---|---|
| `is_st` | **`stock_st`（优先）**，辅助 `namechange` 识别 | `st_stock` |
| `is_suspended` | `suspend_d` | `suspended` |
| `is_delisting_risk` | `namechange`、上市状态、公告摘要 | `delisting_risk` |
| `pledge_ratio` | `pledge_stat`（统计），可选 `pledge_detail`（明细） | `pledge_ratio_high` |
| `unlock_ratio_next_90d` | `share_float` | `large_unlock_pressure` |
| `holder_number_change_pct` | `stk_holdernumber` | `holder_number_rising` |
| `goodwill_to_equity` | 资产负债表 | `goodwill_high` |
| `receivable_growth_vs_revenue` | 资产负债表 + 利润表 | `receivable_growth_faster_than_revenue` |
| `inventory_growth_vs_revenue` | 资产负债表 + 利润表 | `inventory_growth_faster_than_revenue` |
| `debt_to_asset` | 资产负债表或指标接口 | `high_debt_to_asset` |
| `ocf_to_net_profit` | 现金流 + 利润表 | `negative_operating_cashflow` |
| `audit_opinion_abnormal` | **`fina_audit`（A 股专项接口）** | `audit_opinion_abnormal` |

示例：

```json
{
  "risk_flags": [
    "high_debt_to_asset",
    "negative_operating_cashflow",
    "receivable_growth_faster_than_revenue"
  ]
}
```

#### 4.6.10 缺失、过期、估算和数据质量标记

Python 不允许用假数据补齐字段。每个快照必须暴露：

| 字段 | 说明 |
|---|---|
| `data_missing` | 无法获取或无法计算的字段 |
| `data_stale` | 数据过期或本次拉取失败但沿用旧值的字段 |
| `data_estimated` | 由 Python 估算的字段 |
| `source_apis` | 本快照使用过的 Tushare 接口 |
| `last_fetch_at` | 最近拉取时间 |
| `quality_score` | 可选，数据质量分 |

示例：

```json
{
  "data_missing": ["industry_pe_median", "audit_opinion"],
  "data_stale": ["hk_fina_indicator"],
  "data_estimated": ["free_cash_flow_ttm"],
  "source_apis": ["hk_basic", "hk_daily", "hk_income", "hk_cashflow"]
}
```

#### 4.6.11 最终结构化结果

Tushare 原始数据不直接给 Agent。Python 最终生成三类核心结构。

##### `StockSnapshot`

| 模块 | 内容 |
|---|---|
| `basic` | 代码、名称、市场、行业、上市状态、币种 |
| `price_signal` | 回撤、52 周高低点、RSI、MACD、布林带、BIAS、底部信号 |
| `valuation` | PE、PB、PS、股息率、估值分位、行业估值 |
| `fundamental` | ROE、利润率、收入增速、利润增速 |
| `cashflow` | OCF、FCF、现金流利润比 |
| `balance_sheet` | 负债率、流动比率、商誉、应收、存货 |
| `risk` | ST、停牌、质押、解禁、审计意见、风险事件 |
| `capital_flow` | 换手率、量比、资金流、港股通持股 |
| `data_quality` | 缺失、过期、估算、来源 |

##### `SignalCard`

| 字段 | 说明 |
|---|---|
| `passed_price_screen` | 是否通过价格筛选 |
| `alert_level` | 底部信号等级 |
| `bottom_signal_score` | 技术底部信号分 |
| `score_detail` | 分项得分 |
| `reason` | 触发原因 |

##### `ChangeEvent`

| 类型 | 示例 |
|---|---|
| `price` | 回撤扩大、接近 52 周低点 |
| `valuation` | PE 分位下降到 20% 以下 |
| `fundamental` | 最新财报收入增速下滑 |
| `cashflow` | 经营现金流转负 |
| `risk` | 出现停牌、ST、质押升高 |
| `capital_flow` | 成交额明显放大 |
| `pool` | 新进入候选池、退出候选池 |
| `data_quality` | 关键字段缺失或恢复 |

---

## 5. 原始缓存设计

### 5.1 缓存目录

原始数据建议用 Parquet 保存。

```text
data/
├── raw/
│   └── tushare/
│       ├── hk/
│       │   ├── hk_basic/
│       │   ├── hk_daily/
│       │   │   ├── 00700.HK.parquet
│       │   │   └── 09988.HK.parquet
│       │   ├── hk_daily_adj/          ← 复权行情（推荐主数据源）
│       │   ├── hk_adjfactor/
│       │   ├── hk_income/
│       │   ├── hk_balancesheet/
│       │   ├── hk_cashflow/
│       │   ├── hk_fina_indicator/
│       │   ├── hk_hold/
│       │   └── hk_tradecal/
│       ├── us/
│       │   ├── us_basic/
│       │   ├── us_daily/
│       │   │   ├── AAPL.US.parquet
│       │   │   └── MSFT.US.parquet
│       │   ├── us_daily_adj/          ← 复权行情（推荐主数据源）
│       │   ├── us_adjfactor/
│       │   ├── us_fina_indicator/
│       │   ├── us_income/
│       │   ├── us_balancesheet/
│       │   ├── us_cashflow/
│       │   └── us_tradecal/
│       └── cn/
│           ├── stock_basic/
│           ├── daily/
│           ├── adj_factor/
│           ├── daily_basic/
│           ├── fina_indicator/
│           ├── fina_audit/
│           ├── income/
│           ├── balancesheet/
│           ├── cashflow/
│           ├── stock_st/
│           ├── forecast/
│           ├── express/
│           ├── dividend/
│           ├── suspend_d/
│           ├── namechange/
│           ├── moneyflow/
│           ├── stk_holdernumber/
│           ├── top10_holders/
│           ├── pledge_stat/
│           ├── pledge_detail/
│           └── share_float/
└── processed/
    ├── stock_snapshot.parquet
    ├── signal_card.parquet
    ├── change_event.parquet
    └── technical_indicator.parquet
```

### 5.2 原始缓存写入规则

每次拉取后：

1. 读取本地旧缓存；
2. 合并新数据；
3. 按主键去重；
4. 按日期排序；
5. 覆盖写回 Parquet。

主键建议：

| 数据 | 主键 |
|---|---|
| 日线行情 | `ts_code + trade_date` |
| 财务报表 | `ts_code + end_date + report_type` |
| 财务指标 | `ts_code + end_date` |
| 基础信息 | `ts_code` |
| 事件数据 | `ts_code + event_date + event_type` |

---

## 6. SQLite 结构化存储

Agent 工具不直接读原始 Tushare 缓存，而是查询 SQLite 中的结构化结果。

建议表：

```text
stock_daily_snapshot
stock_signal_card
stock_change_event
stock_pool_state
stock_llm_analysis
fetch_state
```

### 6.1 fetch_state

用于记录每个接口、每个市场、每只股票的最后成功更新时间。

字段建议：

```text
market
api_name
ts_code
last_success_date
last_trade_date
last_report_period
last_fetch_at
status
error_message
```

`incremental_fetcher` 优先读取 `fetch_state`，如果没有记录，则回退到本地缓存文件中最大日期；如果本地缓存也没有，则执行首次初始化。

---

## 7. 增量更新伪代码

```python
def update_daily_data(ts_code: str, market: str, today: date) -> None:
    api_name = resolve_daily_api(market)
    local_df = raw_cache.load(api_name=api_name, ts_code=ts_code)

    if local_df.empty:
        start_date = config.data.earliest_start_date
    else:
        last_date = local_df["trade_date"].max()
        start_date = last_date

    new_df = tushare_client.fetch(
        api_name=api_name,
        ts_code=ts_code,
        start_date=start_date,
        end_date=today,
    )

    merged_df = concat([local_df, new_df])
    merged_df = merged_df.drop_duplicates(
        subset=["ts_code", "trade_date"],
        keep="last",
    ).sort_values("trade_date")

    raw_cache.save(api_name=api_name, ts_code=ts_code, df=merged_df)
    fetch_state.mark_success(
        api_name=api_name,
        ts_code=ts_code,
        last_success_date=today,
        last_trade_date=merged_df["trade_date"].max(),
    )
```

财务数据类似，但以 `end_date` 或报告期作为增量锚点。

---

## 8. 每日预处理流水线

`pipelines/daily_prepare.py` 负责 Agent 启动前的数据准备。

流程：

```text
1. 读取配置和观察列表
2. 按股票所属市场分别执行港股、美股、A 股增量数据更新
3. 清洗并标准化字段
4. 计算技术指标
5. 运行价格底部筛选
6. 生成 SignalCard
7. 生成 StockSnapshot
8. 对比历史快照生成 ChangeEvent
9. 更新 stock_pool_state
10. 写入 SQLite
11. 输出 prepare_summary，供 Agent 启动时读取
```

`prepare_summary` 示例：

```json
{
  "data_date": "2026-06-10",
  "markets": ["hk", "us", "cn"],
  "updated_symbols": 125,
  "new_candidates": 8,
  "existing_candidates": 34,
  "risk_alerts": 2,
  "data_missing_count": 6
}
```

---

## 9. Agent 工具接口设计

### 9.1 查询类工具

```python
def get_candidate_pool(date: str | None = None) -> dict:
    """返回候选池、老池、退出池、风险池。"""


def search_stocks(filters: dict) -> list[dict]:
    """按市场、回撤、bottom_signal_score、风险等级等条件筛选股票。"""


def get_stock_snapshot(code: str, date: str | None = None) -> dict:
    """返回单只股票结构化快照。"""


def get_signal_card(code: str, date: str | None = None) -> dict:
    """返回技术底部信号卡。"""


def get_change_events(code: str, days: int = 30) -> list[dict]:
    """返回近期变化事件。"""


def get_previous_analysis(code: str) -> dict | None:
    """返回最近一次 Agent 研究结论。"""
```

### 9.2 写入类工具

```python
def map_llm_priority_score(llm_output: dict) -> dict:
    """根据 Agent 结构化判断映射 llm_priority_score。"""


def save_analysis(code: str, analysis: dict) -> dict:
    """保存 Agent 分析结论。"""


def generate_report(date: str | None = None) -> dict:
    """根据 final_priority 和分析结论生成日报。"""
```

---

## 10. Agent 运行时的数据访问原则

Agent 只能通过 Python 工具访问数据。

Agent 不应该：

- 直接请求 Tushare；
- 直接读取 raw Parquet；
- 直接访问全量原始 K 线；
- 自行计算 RSI、MACD、回撤；
- 自行补全缺失字段。

Agent 应该：

- 先读取候选池；
- 再按需读取快照、信号卡、变化事件和历史结论；
- 对数据缺失保持谨慎；
- 输出结构化判断；
- 交给 Python 工具做分数映射、保存和报告生成。

---

## 11. 异常处理与数据质量

### 11.1 Tushare 拉取失败

处理方式：

1. 记录失败接口、股票代码、时间和错误信息；
2. 不覆盖旧缓存；
3. 标记 `fetch_state.status = failed`；
4. 在 `data_missing` 中暴露给 Agent；
5. 下次运行继续从上次成功日期重试。

### 11.2 数据缺口

如果发现本地日期不连续：

1. 自动识别缺口区间；
2. 对缺口区间重新拉取；
3. 合并去重；
4. 仍无法补齐时写入 `data_missing_json`。

### 11.3 字段缺失

不同市场字段体系不完全一致。处理原则：

1. 字段缺失不伪造；
2. 可计算则计算替代指标；
3. 不可计算则标记 `data_missing`；
4. Agent 研究结论必须显式看到缺失字段。

---

## 12. CLI 设计

建议提供以下命令：

```bash
python main.py init-history --market hk,us,cn
python main.py daily-prepare --date 2026-06-10
python main.py rebuild-snapshot --date 2026-06-10
python main.py run-agent --date 2026-06-10
python main.py generate-report --date 2026-06-10
```

其中：

- `init-history`：首次全量历史初始化；
- `daily-prepare`：每日增量更新和结构化快照生成；
- `rebuild-snapshot`：用已有 raw cache 重建结构化结果；
- `run-agent`：启动 Agent 研究流程；
- `generate-report`：重新生成日报。

---

## 13. 关键结论

1. Python 必须从 Tushare 拉取并缓存数据；
2. Tushare 请求必须先按港股、美股、A 股分别建立接口、字段、主键、更新频率和增量参数契约；
3. 如果本地已有历史数据，只从最后日期或最近覆盖窗口开始增量拉取到今天，并合并去重；
4. 如果本地没有历史数据，则从 `earliest_start_date` 开始初始化；
5. 原始 Tushare 数据建议用 Parquet 缓存，并按市场和接口分目录保存；
6. Python 必须完成代码、日期、行情、复权、财务、TTM、估值、风险和数据质量清洗；
7. Agent 查询的数据应来自 SQLite 中的结构化事实表；
8. Agent 不直接访问 Tushare，也不直接处理原始行情或完整财报；
9. Python 是数据准备层、缓存层、计算层、存储层和工具层；
10. Agent 是研究流程驱动层和判断层。