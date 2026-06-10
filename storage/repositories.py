"""
SQLite CRUD 封装
"""

import sqlite3
import json
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger("newstock.storage.repositories")


def _json_dumps(obj: object) -> str:
    """将 Python 对象转为 JSON 字符串"""
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(s: Optional[str]) -> object:
    """将 JSON 字符串转为 Python 对象"""
    if s is None:
        return None
    return json.loads(s)


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------
def upsert_snapshot(conn: sqlite3.Connection, data_date: str, snapshot: dict) -> None:
    """插入或更新每日快照"""
    cols = [
        "date", "code", "name", "market", "industry",
        "current_price", "pct_chg", "cycle_high_price", "cycle_high_date",
        "drawdown_from_high_pct", "high_52w", "low_52w", "distance_from_low_pct",
        "price_percentile_1y", "ma20", "ma60", "ma120", "rsi_14", "weekly_rsi",
        "macd_dif", "macd_dea", "macd_hist", "macd_divergence",
        "bollinger_position_pct", "bias_120", "volume_ratio",
        "alert_level", "bottom_signal_score",
        "pe_ttm", "pb", "ps_ttm", "dividend_yield_ttm", "market_cap",
        "float_market_cap", "turnover_rate",
        "pe_percentile_5y", "pb_percentile_5y",
        "industry_pe_median", "industry_pb_median",
        "financial_summary_json", "balance_summary_json", "cashflow_summary_json",
        "risk_flags_json", "capital_flow_json",
        "data_missing_json", "data_stale_json", "data_estimated_json",
        "source_apis_json", "quality_score",
    ]

    ps = snapshot.get("price_signal", {})
    val = snapshot.get("valuation", {})
    fund = snapshot.get("fundamental", {})
    bal = snapshot.get("balance_sheet", {})
    cf = snapshot.get("cashflow", {})
    risk = snapshot.get("risk", {})
    cf_flow = snapshot.get("capital_flow", {})
    dq = snapshot.get("data_quality", {})

    values = {
        "date": data_date,
        "code": snapshot.get("code"),
        "name": snapshot.get("name"),
        "market": snapshot.get("market"),
        "industry": snapshot.get("industry"),
        "current_price": ps.get("current_price"),
        "pct_chg": ps.get("pct_chg"),
        "cycle_high_price": ps.get("cycle_high_price"),
        "cycle_high_date": ps.get("cycle_high_date"),
        "drawdown_from_high_pct": ps.get("drawdown_from_high_pct"),
        "high_52w": ps.get("high_52w"),
        "low_52w": ps.get("low_52w"),
        "distance_from_low_pct": ps.get("distance_from_low_pct"),
        "price_percentile_1y": ps.get("price_percentile_1y"),
        "ma20": ps.get("ma20"),
        "ma60": ps.get("ma60"),
        "ma120": ps.get("ma120"),
        "rsi_14": ps.get("rsi_14"),
        "weekly_rsi": ps.get("weekly_rsi"),
        "macd_dif": ps.get("macd_dif"),
        "macd_dea": ps.get("macd_dea"),
        "macd_hist": ps.get("macd_hist"),
        "macd_divergence": 1 if ps.get("macd_divergence") else 0,
        "bollinger_position_pct": ps.get("bollinger_position_pct"),
        "bias_120": ps.get("bias_120"),
        "volume_ratio": ps.get("volume_ratio"),
        "alert_level": ps.get("alert_level"),
        "bottom_signal_score": ps.get("bottom_signal_score"),
        "pe_ttm": val.get("pe_ttm"),
        "pb": val.get("pb"),
        "ps_ttm": val.get("ps_ttm"),
        "dividend_yield_ttm": val.get("dividend_yield_ttm"),
        "market_cap": val.get("market_cap"),
        "float_market_cap": val.get("float_market_cap"),
        "turnover_rate": val.get("turnover_rate"),
        "pe_percentile_5y": val.get("pe_percentile_5y"),
        "pb_percentile_5y": val.get("pb_percentile_5y"),
        "industry_pe_median": val.get("industry_pe_median"),
        "industry_pb_median": val.get("industry_pb_median"),
        "financial_summary_json": _json_dumps(fund) if fund else None,
        "balance_summary_json": _json_dumps(bal) if bal else None,
        "cashflow_summary_json": _json_dumps(cf) if cf else None,
        "risk_flags_json": _json_dumps(risk.get("risk_flags", [])) if risk else None,
        "capital_flow_json": _json_dumps(cf_flow) if cf_flow else None,
        "data_missing_json": _json_dumps(dq.get("data_missing")) if dq else None,
        "data_stale_json": _json_dumps(dq.get("data_stale")) if dq else None,
        "data_estimated_json": _json_dumps(dq.get("data_estimated")) if dq else None,
        "source_apis_json": _json_dumps(dq.get("source_apis")) if dq else None,
        "quality_score": dq.get("quality_score") if dq else None,
    }

    placeholders = ", ".join([f":{c}" for c in cols])
    sql = f"""
        INSERT INTO stock_daily_snapshot ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(date, code) DO UPDATE SET
            {", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("date", "code"))}
    """
    conn.execute(sql, values)
    conn.commit()


def get_snapshot(conn: sqlite3.Connection, code: str, data_date: Optional[str] = None) -> Optional[dict]:
    """获取单只股票快照"""
    if data_date:
        row = conn.execute(
            "SELECT * FROM stock_daily_snapshot WHERE code = ? AND date = ? ORDER BY date DESC LIMIT 1",
            (code, data_date),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM stock_daily_snapshot WHERE code = ? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# signal card
# ---------------------------------------------------------------------------
def upsert_signal_card(conn: sqlite3.Connection, data_date: str, card: dict) -> None:
    """保存信号卡片"""
    conn.execute(
        """INSERT INTO stock_signal_card (date, code, passed_price_screen, alert_level,
           bottom_signal_score, score_detail_json, reason)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(date, code) DO UPDATE SET
           passed_price_screen=excluded.passed_price_screen,
           alert_level=excluded.alert_level,
           bottom_signal_score=excluded.bottom_signal_score,
           score_detail_json=excluded.score_detail_json,
           reason=excluded.reason""",
        (
            data_date,
            card.get("code"),
            1 if card.get("passed_price_screen") else 0,
            card.get("alert_level"),
            card.get("bottom_signal_score", 0),
            _json_dumps(card.get("score_detail")),
            card.get("reason"),
        ),
    )
    conn.commit()


def get_signal_card(conn: sqlite3.Connection, code: str, data_date: Optional[str] = None) -> Optional[dict]:
    """获取信号卡片"""
    if data_date:
        row = conn.execute(
            "SELECT * FROM stock_signal_card WHERE code = ? AND date = ? ORDER BY date DESC LIMIT 1",
            (code, data_date),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM stock_signal_card WHERE code = ? ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# change event
# ---------------------------------------------------------------------------
def insert_change_events(conn: sqlite3.Connection, events: list[dict]) -> None:
    """批量写入变化事件"""
    for e in events:
        conn.execute(
            """INSERT INTO stock_change_event (date, code, event_type, event_level,
               event_desc, attention_impact, need_reanalysis)
               VALUES (?,?,?,?,?,?,?)""",
            (
                e.get("date"),
                e.get("code"),
                e.get("event_type"),
                e.get("event_level", "medium"),
                e.get("event_desc"),
                e.get("attention_impact", 0),
                1 if e.get("need_reanalysis") else 0,
            ),
        )
    conn.commit()


def get_change_events(conn: sqlite3.Connection, code: str, days: int = 30) -> list[dict]:
    """获取近期变化事件"""
    rows = conn.execute(
        """SELECT * FROM stock_change_event
           WHERE code = ? AND date >= date('now', ? || ' days')
           ORDER BY date DESC""",
        (code, f"-{days}"),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# pool state
# ---------------------------------------------------------------------------
def upsert_pool_state(conn: sqlite3.Connection, data_date: str, state: dict) -> None:
    """更新观察池状态"""
    conn.execute(
        """INSERT INTO stock_pool_state (date, code, pool_status, first_seen_date,
           last_seen_date, days_in_pool, last_full_analysis_date)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(date, code) DO UPDATE SET
           pool_status=excluded.pool_status,
           first_seen_date=excluded.first_seen_date,
           last_seen_date=excluded.last_seen_date,
           days_in_pool=excluded.days_in_pool,
           last_full_analysis_date=excluded.last_full_analysis_date""",
        (
            data_date,
            state.get("code"),
            state.get("pool_status"),
            state.get("first_seen_date"),
            state.get("last_seen_date"),
            state.get("days_in_pool", 0),
            state.get("last_full_analysis_date"),
        ),
    )
    conn.commit()


def get_candidate_pool(conn: sqlite3.Connection, data_date: Optional[str] = None) -> list[dict]:
    """获取候选池"""
    if data_date:
        rows = conn.execute(
            "SELECT * FROM stock_pool_state WHERE date = ? AND pool_status IN ('new', 'existing') ORDER BY code",
            (data_date,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM stock_pool_state
               WHERE date = (SELECT MAX(date) FROM stock_pool_state)
               AND pool_status IN ('new', 'existing')
               ORDER BY code""",
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# llm analysis
# ---------------------------------------------------------------------------
def save_analysis(conn: sqlite3.Connection, analysis: dict) -> None:
    """保存 Agent 分析结论"""
    conn.execute(
        """INSERT INTO stock_llm_analysis (
           code, analysis_date, task_type, decision, research_priority,
           opportunity_quality, valuation_attractiveness, fundamental_quality,
           risk_level, value_trap_probability, confidence,
           llm_priority_score, bottom_signal_score, attention_score, final_priority,
           main_logic, main_positive_points_json, main_risks_json,
           key_contradictions_json, data_missing_json, suggested_follow_up_json,
           raw_llm_output_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            analysis.get("code"),
            analysis.get("analysis_date"),
            analysis.get("task_type"),
            analysis.get("decision"),
            analysis.get("research_priority"),
            analysis.get("opportunity_quality"),
            analysis.get("valuation_attractiveness"),
            analysis.get("fundamental_quality"),
            analysis.get("risk_level"),
            analysis.get("value_trap_probability"),
            analysis.get("confidence"),
            analysis.get("llm_priority_score"),
            analysis.get("bottom_signal_score"),
            analysis.get("attention_score"),
            analysis.get("final_priority"),
            analysis.get("main_logic"),
            _json_dumps(analysis.get("main_positive_points")),
            _json_dumps(analysis.get("main_risks")),
            _json_dumps(analysis.get("key_contradictions")),
            _json_dumps(analysis.get("data_missing")),
            _json_dumps(analysis.get("suggested_follow_up")),
            _json_dumps(analysis.get("raw_llm_output")),
        ),
    )
    conn.commit()


def get_previous_analysis(conn: sqlite3.Connection, code: str) -> Optional[dict]:
    """获取最近一次 Agent 分析结论"""
    row = conn.execute(
        "SELECT * FROM stock_llm_analysis WHERE code = ? ORDER BY analysis_date DESC LIMIT 1",
        (code,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# fetch state
# ---------------------------------------------------------------------------
def get_fetch_state(conn: sqlite3.Connection, market: str, api_name: str, ts_code: str) -> Optional[dict]:
    """查询拉取状态"""
    row = conn.execute(
        "SELECT * FROM fetch_state WHERE market = ? AND api_name = ? AND ts_code = ?",
        (market, api_name, ts_code),
    ).fetchone()
    return dict(row) if row else None


def mark_fetch_success(
    conn: sqlite3.Connection,
    market: str,
    api_name: str,
    ts_code: str,
    last_success_date: str,
    last_trade_date: Optional[str] = None,
    last_report_period: Optional[str] = None,
) -> None:
    """标记拉取成功"""
    conn.execute(
        """INSERT INTO fetch_state (market, api_name, ts_code, last_success_date,
           last_trade_date, last_report_period, last_fetch_at, status)
           VALUES (?,?,?,?,?,?,datetime('now','localtime'),'success')
           ON CONFLICT(market, api_name, ts_code) DO UPDATE SET
           last_success_date=excluded.last_success_date,
           last_trade_date=excluded.last_trade_date,
           last_report_period=excluded.last_report_period,
           last_fetch_at=excluded.last_fetch_at,
           status='success',
           error_message=NULL""",
        (market, api_name, ts_code, last_success_date, last_trade_date, last_report_period),
    )
    conn.commit()


def mark_fetch_failed(
    conn: sqlite3.Connection,
    market: str,
    api_name: str,
    ts_code: str,
    error_message: str,
) -> None:
    """标记拉取失败"""
    conn.execute(
        """INSERT INTO fetch_state (market, api_name, ts_code, status, error_message,
           last_fetch_at)
           VALUES (?,?,?,'failed',?,datetime('now','localtime'))
           ON CONFLICT(market, api_name, ts_code) DO UPDATE SET
           status='failed',
           error_message=excluded.error_message,
           last_fetch_at=excluded.last_fetch_at""",
        (market, api_name, ts_code, error_message),
    )
    conn.commit()
