"""
Agent 工具：候选池查询
"""

import logging
import sqlite3
from datetime import date
from typing import Any, Optional

from storage.repositories import get_candidate_pool, get_snapshot

logger = logging.getLogger("newstock.agent_tools.candidate")


def get_candidate_pool_tool(conn: sqlite3.Connection, data_date: Optional[str] = None) -> dict:
    """
    返回候选池、老池、退出池、风险池。

    返回:
      {
        "date": str,
        "total_count": int,
        "new_candidates": list[str],
        "existing_candidates": list[str],
        "risk_alerts": list[str],
        "removed_candidates": list[str],
      }
    """
    rows = get_candidate_pool(conn, data_date)
    if not rows:
        return {"date": data_date or str(date.today()), "total_count": 0, "new_candidates": [], "existing_candidates": [], "risk_alerts": [], "removed_candidates": []}

    # 也查询退出和风险
    all_rows = conn.execute(
        "SELECT * FROM stock_pool_state WHERE date = (SELECT MAX(date) FROM stock_pool_state)"
    ).fetchall()

    result = {
        "date": rows[0].get("date", str(date.today())),
        "total_count": 0,
        "new_candidates": [],
        "existing_candidates": [],
        "risk_alerts": [],
        "removed_candidates": [],
    }

    for r in all_rows:
        status = r.get("pool_status", "")
        code = r.get("code", "")
        if status == "new":
            result["new_candidates"].append(code)
        elif status == "existing":
            result["existing_candidates"].append(code)
        elif status == "risk_alert":
            result["risk_alerts"].append(code)
        elif status == "removed":
            result["removed_candidates"].append(code)

    result["total_count"] = len(result["new_candidates"]) + len(result["existing_candidates"])
    return result


def search_stocks_tool(
    conn: sqlite3.Connection,
    filters: dict,
) -> list[dict]:
    """
    按条件筛选股票。

    支持的筛选条件:
      market: str (HK/US/CN)
      min_drawdown: float
      max_rsi: float
      min_bottom_signal: int
      max_pe_ttm: float
      industry: str
    """
    sql = "SELECT * FROM stock_daily_snapshot WHERE date = (SELECT MAX(date) FROM stock_daily_snapshot)"
    params = []

    if filters.get("market"):
        sql += " AND market = ?"
        params.append(filters["market"])

    if filters.get("industry"):
        sql += " AND industry LIKE ?"
        params.append(f"%{filters['industry']}%")

    if filters.get("min_drawdown"):
        sql += " AND drawdown_from_high_pct >= ?"
        params.append(filters["min_drawdown"])

    if filters.get("max_rsi"):
        sql += " AND rsi_14 <= ?"
        params.append(filters["max_rsi"])

    if filters.get("min_bottom_signal"):
        sql += " AND bottom_signal_score >= ?"
        params.append(filters["min_bottom_signal"])

    if filters.get("max_pe_ttm"):
        sql += " AND pe_ttm <= ? AND pe_ttm > 0"
        params.append(filters["max_pe_ttm"])

    sql += " ORDER BY bottom_signal_score DESC LIMIT 50"

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
