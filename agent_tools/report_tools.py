"""
Agent 工具：报告生成
"""

import json
import logging
import sqlite3
from datetime import date
from typing import Any, Optional

from storage.repositories import get_candidate_pool, get_snapshot, get_previous_analysis

logger = logging.getLogger("newstock.agent_tools.report")


def generate_report_tool(conn: sqlite3.Connection, report_date: Optional[str] = None) -> dict:
    """
    生成研究优先级报告。

    返回:
      {
        "date": str,
        "summary": dict,
        "top_priority": list,
        "new_candidates": list,
        "risk_alerts": list,
      }
    """
    if report_date is None:
        report_date = str(date.today())

    # 获取候选池
    all_states = conn.execute(
        "SELECT * FROM stock_pool_state WHERE date = ? ORDER BY code",
        (report_date,),
    ).fetchall()

    # 获取所有快照
    snapshots = conn.execute(
        "SELECT * FROM stock_daily_snapshot WHERE date = ?",
        (report_date,),
    ).fetchall()

    snapshot_map = {s["code"]: dict(s) for s in snapshots}

    # 构建报告
    new_candidates = []
    risk_alerts = []
    all_candidates = []

    for state in all_states:
        code = state["code"]
        status = state["pool_status"]
        snap = snapshot_map.get(code, {})

        entry = {
            "code": code,
            "name": snap.get("name"),
            "market": snap.get("market"),
            "pool_status": status,
            "bottom_signal_score": snap.get("bottom_signal_score", 0),
            "drawdown_from_high_pct": snap.get("drawdown_from_high_pct"),
            "pe_ttm": snap.get("pe_ttm"),
        }

        # 获取分析结论
        analysis = get_previous_analysis(conn, code)
        if analysis:
            entry["research_priority"] = analysis.get("research_priority")
            entry["final_priority"] = analysis.get("final_priority")

        if status == "risk_alert":
            risk_alerts.append(entry)
        elif status == "new":
            new_candidates.append(entry)
        elif status in ("new", "existing"):
            all_candidates.append(entry)

    # 按 final_priority 降序排序
    all_candidates.sort(
        key=lambda x: x.get("final_priority", 0) or 0,
        reverse=True,
    )

    top_priority = all_candidates[:10]

    return {
        "date": report_date,
        "summary": {
            "total_pool_count": len(all_candidates),
            "new_candidates": len(new_candidates),
            "risk_alerts": len(risk_alerts),
        },
        "top_priority": top_priority,
        "new_candidates": new_candidates,
        "risk_alerts": risk_alerts,
    }
