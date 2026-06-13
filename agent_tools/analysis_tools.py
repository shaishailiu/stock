"""
Agent 工具：分析结论读写

在 save_analysis_tool 内完成完整的评分链路：
  LLM 输出 → map_llm_priority_score → attention_score → final_priority → 入库
"""

import logging
import sqlite3
from datetime import date, datetime
from typing import Optional

from storage.repositories import (
    get_change_events,
    get_previous_analysis,
    get_snapshot,
    save_analysis,
)
from processing.validators import validate_llm_output
from agent_tools.scoring_tools import (
    calc_attention_score_tool,
    calc_final_priority_tool,
    map_llm_priority_score_tool,
)

logger = logging.getLogger("newstock.agent_tools.analysis")


def get_previous_analysis_tool(conn: sqlite3.Connection, code: str) -> Optional[dict]:
    """获取最近一次 Agent 分析结论"""
    return get_previous_analysis(conn, code)


def _is_new_candidate(conn: sqlite3.Connection, code: str, data_date: str) -> bool:
    """判断是否为新进候选池的股票"""
    row = conn.execute(
        "SELECT pool_status FROM stock_pool_state WHERE code = ? AND date = ?",
        (code, data_date),
    ).fetchone()
    return row is not None and row["pool_status"] == "new"


def _compute_days_since_last_analysis(conn: sqlite3.Connection, code: str) -> int:
    """计算距上次完整分析的天数"""
    prev = get_previous_analysis(conn, code)
    if not prev:
        return 999  # 从未分析过
    last_date_str = prev.get("analysis_date", "")
    if not last_date_str:
        return 999
    try:
        last_date = datetime.strptime(str(last_date_str)[:10], "%Y-%m-%d").date()
        return (date.today() - last_date).days
    except (ValueError, TypeError):
        return 999


def _deduce_attention_params(
    conn: sqlite3.Connection,
    code: str,
    analysis_date: str,
) -> dict:
    """
    从数据库推导 calc_attention_score_tool 需要的参数。
    """
    # 是否新进候选池
    is_new = _is_new_candidate(conn, code, analysis_date)

    # 距上次分析天数
    days_since_last = _compute_days_since_last_analysis(conn, code)

    # 从变化事件中提取关注信号
    events = get_change_events(conn, code, days=30)
    major_risk_event = any(
        e.get("event_type") == "risk" and e.get("event_level") in ("high", "critical")
        for e in events
    )
    earnings_report = any(
        e.get("event_type") == "fundamental"
        and "财报" in str(e.get("event_desc", ""))
        for e in events
    )
    price_change_major = any(
        e.get("event_type") == "price" and e.get("event_level") in ("medium", "high", "critical")
        for e in events
    )
    valuation_change_major = any(
        e.get("event_type") == "valuation" for e in events
    )
    capital_flow_change = any(
        e.get("event_type") == "capital_flow" for e in events
    )

    return {
        "is_new_candidate": is_new,
        "major_risk_event": major_risk_event,
        "earnings_report": earnings_report,
        "price_change_major": price_change_major,
        "valuation_change_major": valuation_change_major,
        "capital_flow_change": capital_flow_change,
        "days_since_last_analysis": days_since_last,
    }


def _get_bottom_signal_score(
    conn: sqlite3.Connection, code: str, analysis_date: str
) -> int:
    """从 stock_daily_snapshot 或 stock_signal_card 获取底部信号分"""
    # 优先从快照取
    snap = get_snapshot(conn, code, data_date=analysis_date)
    if snap and snap.get("bottom_signal_score") is not None:
        return int(snap["bottom_signal_score"])

    # 回退到信号卡
    row = conn.execute(
        "SELECT bottom_signal_score FROM stock_signal_card WHERE code = ? AND date = ?",
        (code, analysis_date),
    ).fetchone()
    if row and row["bottom_signal_score"] is not None:
        return int(row["bottom_signal_score"])

    return 0


def save_analysis_tool(conn: sqlite3.Connection, analysis: dict) -> dict:
    """
    保存 Agent 分析结论（含校验 + 自动评分）。

    内部完成完整评分链路：
      1. 校验 LLM 输出结构
      2. 从 LLM 输出映射 llm_priority_score（map_llm_priority_score_tool）
      3. 从数据库推导 attention_score（calc_attention_score_tool）
      4. 加权合成 final_priority（calc_final_priority_tool）
      5. 将所有评分字段注入 analysis 并入库

    返回:
      {
        "success": bool,
        "errors": list[str],
        "saved": dict | None,
        "scoring": {  # 仅 success=true 时返回
          "llm_priority_score": int,
          "base_score": int,
          "adjustments": list[dict],
          "bottom_signal_score": int,
          "attention_score": int,
          "final_priority": float,
          "attention_params": dict,
        }
      }
    """
    code = analysis.get("code")
    analysis_date = analysis.get("analysis_date") or str(date.today())
    analysis["analysis_date"] = analysis_date

    # ── 第 1 步：校验 ──
    errors = validate_llm_output(analysis)
    if errors:
        logger.warning(f"Validation errors for {code}: {errors}")
        return {"success": False, "errors": errors, "saved": None}

    # ── 第 2 步：映射 llm_priority_score ──
    llm_score_result = map_llm_priority_score_tool(analysis)
    analysis["llm_priority_score"] = llm_score_result["llm_priority_score"]

    # ── 第 3 步：获取底部信号分 ──
    bottom_signal_score = _get_bottom_signal_score(conn, code, analysis_date)
    analysis["bottom_signal_score"] = bottom_signal_score

    # ── 第 4 步：计算关注紧迫度 ──
    attention_params = _deduce_attention_params(conn, code, analysis_date)
    attention_score = calc_attention_score_tool(**attention_params)
    analysis["attention_score"] = attention_score

    # ── 第 5 步：合成最终优先级 ──
    final_priority = calc_final_priority_tool(
        llm_priority_score=llm_score_result["llm_priority_score"],
        bottom_signal_score=bottom_signal_score,
        attention_score=attention_score,
    )
    analysis["final_priority"] = final_priority

    # ── 第 6 步：入库 ──
    save_analysis(conn, analysis)
    logger.info(
        f"Saved analysis for {code} | "
        f"llm_score={llm_score_result['llm_priority_score']} "
        f"bottom={bottom_signal_score} "
        f"attention={attention_score} "
        f"final={final_priority}"
    )

    return {
        "success": True,
        "errors": [],
        "saved": analysis,
        "scoring": {
            "llm_priority_score": llm_score_result["llm_priority_score"],
            "base_score": llm_score_result["base_score"],
            "adjustments": llm_score_result["adjustments"],
            "bottom_signal_score": bottom_signal_score,
            "attention_score": attention_score,
            "final_priority": final_priority,
            "attention_params": attention_params,
        },
    }
