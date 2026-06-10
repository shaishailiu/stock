# 股票 LLM 价值筛选系统

> 基于 Tushare 数据 + LLM Agent 驱动的多市场股票研究优先级系统  
> 重要说明：本文档用于构建股票研究辅助系统，不构成任何投资建议或交易指令。

## 功能概述

1. **多市场覆盖**：接入 Tushare 数据，同等支持港股、美股、A 股
2. **Python 预处理**：数据获取 → 清洗 → 技术指标（RSI/MACD/布林带/BIAS）→ 底部信号 → 候选池
3. **LLM Agent 驱动**：Agent 根据结构化事实（StockSnapshot / SignalCard / ChangeEvent）完成综合研究判断
4. **增量监控**：每日只追踪变化，不重复计算完整结论

## 项目结构

```
newstock/
├── main.py                     # CLI 入口
├── requirements.txt
├── config/
│   ├── config.yaml             # Tushare token、观察列表
│   └── logging.yaml
├── data_fetcher/               # Tushare API 封装
├── cache/                      # Parquet 缓存
├── processing/                 # 代码标准化、清洗、校验
├── indicators/                 # RSI/MACD/布林带/BIAS/底部信号分
├── snapshot/                   # StockSnapshot / SignalCard / ChangeEvent
├── storage/                    # SQLite + schema + CRUD
├── agent_tools/                # Agent 可调用的 Python 工具
├── pipelines/                  # 每日预处理 / 历史初始化
├── report/
├── tests/
└── doc/                        # 设计文档
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `config/config.yaml`：

- 填入 Tushare Token（需 10000 积分档）
- 配置港股/美股/A 股观察列表

### 3. Agent 研究入口（WorkBuddy）

Agent 提示词文件：`agent/research_prompts.py`

```bash
python3 main.py show-prompt
```

WorkBuddy 会读取系统提示词，并自动调用 Python 工具完成股票研究流程。

### 4. 首次初始化历史数据

```bash
python3 main.py init-history --markets hk,us,cn
```

### 5. 每日预处理

```bash
python3 main.py daily-prepare --date 2026-06-17
```

### 6. 生成日报

```bash
python3 main.py generate-report --date 2026-06-17
```

## 数据流

```text
配置读取 → Tushare 增量拉取（港股/美股/A股）
         → Parquet 缓存（data/raw/）
         → 技术指标计算 → 底部信号分 → 候选池
         → StockSnapshot / SignalCard / ChangeEvent
         → SQLite 入库（data/newstock.db）
         → Agent 工具查询 → LLM 研究判断 → 日报
```

## 核心概念

| 概念 | 说明 |
|---|---|
| StockSnapshot | 每日事实快照：价格、估值、财务、风险、数据质量 |
| SignalCard | 技术底部信号卡，满分 100（高分=更像底部，≠更值得投资） |
| ChangeEvent | 每日变化事件：价格、估值、风险、池子变化 |
| bottom_signal_score | Python 计算的技术底部信号分 |
| llm_priority_score | Python 将 Agent 判断映射为稳定分数 |
| final_priority | `llm × 0.7 + bottom × 0.2 + attention × 0.1` |

## 评分公式

```text
final_priority = llm_priority_score × 0.7
               + bottom_signal_score × 0.2
               + attention_score × 0.1
```

## 依赖

- tushare ≥ 1.4.0
- pandas ≥ 2.0.0
- numpy ≥ 1.24.0
- pyarrow ≥ 12.0.0
- pyyaml ≥ 6.0

## License

MIT
