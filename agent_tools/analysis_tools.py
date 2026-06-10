"""
Agent 工具：分析结论读写
"""

import logging
import sqlite3
from typing import Optional

from storage.repositories import get_previous_analysis, save_analysis
from processing.validators import validate_llm_output

logger = logging.getLogger("newstock.agent_tools.analysis")


def get_previous_analysis_tool(conn: sqlite3.Connection, code: str) -> Optional[dict]:
    """获取最近一次 Agent 分析结论"""
    return get_previous_analysis(conn, code)


def save_analysis_tool(conn: sqlite3.Connection, analysis: dict) -> dict:
    """
    保存 Agent 分析结论（含校验）。

    返回:
      {"success": bool, "errors": list[str], "saved": dict | None}
    """
    errors = validate_llm_output(analysis)
    if errors:
        logger.warning(f"Validation errors for {analysis.get('code')}: {errors}")
        return {"success": False, "errors": errors, "saved": None}

    save_analysis(conn, analysis)
    logger.info(f"Saved analysis for {analysis.get('code')}")
    return {"success": True, "errors": [], "saved": analysis}
