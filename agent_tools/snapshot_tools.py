"""
Agent 工具：快照查询
"""

import logging
import sqlite3
from typing import Optional

from storage.repositories import get_snapshot

logger = logging.getLogger("newstock.agent_tools.snapshot")


def get_stock_snapshot_tool(
    conn: sqlite3.Connection, code: str, data_date: Optional[str] = None
) -> Optional[dict]:
    """获取单只股票结构化快照"""
    return get_snapshot(conn, code, data_date)
