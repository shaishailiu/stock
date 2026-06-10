"""
Agent 工具：变化事件查询
"""

import logging
import sqlite3
from typing import Optional

from storage.repositories import get_change_events

logger = logging.getLogger("newstock.agent_tools.change")


def get_change_events_tool(
    conn: sqlite3.Connection, code: str, days: int = 30
) -> list[dict]:
    """获取近期变化事件"""
    return get_change_events(conn, code, days)
