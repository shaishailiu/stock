-- 股票 LLM 价值筛选系统 - SQLite Schema
-- 版本：2026-06-10

-- 每日股票快照
CREATE TABLE IF NOT EXISTS stock_daily_snapshot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,          -- 交易日 YYYY-MM-DD
    code        TEXT    NOT NULL,          -- 系统标准代码 如 00700.HK
    name        TEXT,                      -- 股票名称
    market      TEXT    NOT NULL,          -- HK / US / CN
    industry    TEXT,                      -- 行业

    -- 价格与技术信号
    current_price           REAL,
    pct_chg                 REAL,
    cycle_high_price        REAL,
    cycle_high_date         TEXT,
    drawdown_from_high_pct  REAL,
    high_52w                REAL,
    low_52w                 REAL,
    distance_from_low_pct   REAL,
    price_percentile_1y     REAL,
    ma20                    REAL,
    ma60                    REAL,
    ma120                   REAL,
    rsi_14                  REAL,
    weekly_rsi              REAL,
    macd_dif                REAL,
    macd_dea                REAL,
    macd_hist               REAL,
    macd_divergence         INTEGER DEFAULT 0,
    bollinger_position_pct  REAL,
    bias_120                REAL,
    volume_ratio            REAL,
    alert_level             TEXT,
    bottom_signal_score     INTEGER DEFAULT 0,

    -- 估值
    pe_ttm                  REAL,
    pb                      REAL,
    ps_ttm                  REAL,
    dividend_yield_ttm      REAL,
    market_cap              REAL,
    float_market_cap        REAL,
    turnover_rate           REAL,
    pe_percentile_5y        REAL,
    pb_percentile_5y        REAL,
    industry_pe_median      REAL,
    industry_pb_median      REAL,

    -- 财务摘要（JSON）
    financial_summary_json  TEXT,
    balance_summary_json    TEXT,
    cashflow_summary_json   TEXT,

    -- 风险（JSON 数组）
    risk_flags_json         TEXT,

    -- 资金持仓（JSON）
    capital_flow_json       TEXT,

    -- 数据质量（JSON）
    data_missing_json       TEXT,
    data_stale_json         TEXT,
    data_estimated_json     TEXT,
    source_apis_json        TEXT,
    quality_score           REAL,

    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(date, code)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_date ON stock_daily_snapshot(date);
CREATE INDEX IF NOT EXISTS idx_snapshot_code ON stock_daily_snapshot(code);
CREATE INDEX IF NOT EXISTS idx_snapshot_market ON stock_daily_snapshot(market);

-- 信号卡片
CREATE TABLE IF NOT EXISTS stock_signal_card (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL,
    code                TEXT NOT NULL,
    passed_price_screen INTEGER DEFAULT 0,
    alert_level         TEXT,
    bottom_signal_score INTEGER DEFAULT 0,
    score_detail_json   TEXT,
    reason              TEXT,
    created_at          TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(date, code)
);

CREATE INDEX IF NOT EXISTS idx_signal_date ON stock_signal_card(date);
CREATE INDEX IF NOT EXISTS idx_signal_code ON stock_signal_card(code);

-- 变化事件
CREATE TABLE IF NOT EXISTS stock_change_event (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    code            TEXT NOT NULL,
    event_type      TEXT NOT NULL,          -- price / valuation / fundamental / cashflow / risk / capital_flow / pool / data_quality
    event_level     TEXT DEFAULT 'medium',  -- low / medium / high / critical
    event_desc      TEXT,
    attention_impact INTEGER DEFAULT 0,
    need_reanalysis INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_cevent_date ON stock_change_event(date);
CREATE INDEX IF NOT EXISTS idx_cevent_code ON stock_change_event(code);

-- 观察池状态
CREATE TABLE IF NOT EXISTS stock_pool_state (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    date                    TEXT NOT NULL,
    code                    TEXT NOT NULL,
    pool_status             TEXT NOT NULL,   -- new / existing / removed / risk_alert
    first_seen_date         TEXT,
    last_seen_date          TEXT,
    days_in_pool            INTEGER DEFAULT 0,
    last_full_analysis_date TEXT,
    created_at              TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(date, code)
);

CREATE INDEX IF NOT EXISTS idx_pool_date ON stock_pool_state(date);
CREATE INDEX IF NOT EXISTS idx_pool_code ON stock_pool_state(code);

-- Agent 分析结论
CREATE TABLE IF NOT EXISTS stock_llm_analysis (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    code                    TEXT NOT NULL,
    analysis_date           TEXT NOT NULL,
    task_type               TEXT,             -- full_analysis / incremental_analysis / reuse_previous / reject / full_reanalysis / refresh_analysis
    decision                TEXT,
    research_priority       TEXT,             -- very_high / high / medium / low / reject
    opportunity_quality     INTEGER CHECK(opportunity_quality >= 1 AND opportunity_quality <= 5),
    valuation_attractiveness INTEGER CHECK(valuation_attractiveness >= 1 AND valuation_attractiveness <= 5),
    fundamental_quality     INTEGER CHECK(fundamental_quality >= 1 AND fundamental_quality <= 5),
    risk_level              INTEGER CHECK(risk_level >= 1 AND risk_level <= 5),
    value_trap_probability  TEXT,             -- low / medium / high
    confidence              REAL CHECK(confidence >= 0 AND confidence <= 1),
    llm_priority_score      INTEGER,
    bottom_signal_score     INTEGER,
    attention_score         INTEGER,
    final_priority          REAL,
    main_logic              TEXT,
    main_positive_points_json  TEXT,
    main_risks_json            TEXT,
    key_contradictions_json    TEXT,
    data_missing_json          TEXT,
    suggested_follow_up_json   TEXT,
    raw_llm_output_json        TEXT,
    created_at              TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_analysis_code ON stock_llm_analysis(code);
CREATE INDEX IF NOT EXISTS idx_analysis_date ON stock_llm_analysis(analysis_date);

-- 拉取状态
CREATE TABLE IF NOT EXISTS fetch_state (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market              TEXT NOT NULL,
    api_name            TEXT NOT NULL,
    ts_code             TEXT NOT NULL,
    last_success_date   TEXT,
    last_trade_date     TEXT,
    last_report_period  TEXT,
    last_fetch_at       TEXT,
    status              TEXT DEFAULT 'success',  -- success / failed
    error_message       TEXT,
    updated_at          TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE(market, api_name, ts_code)
);
