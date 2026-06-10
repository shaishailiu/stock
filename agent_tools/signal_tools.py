"""
Agent 工具：信号卡查询
"""

import logging
import sqlite3
from typing import Optional

from storage.repositories import get_signal_card

logger = logging.getLogger("newstock.agent_tools.signal")


def get_signal_card_tool(
    conn: sqlite3.Connection, code: str, data_date: Optional[str] = None
) -> Optional[dict]:
    """获取技术底部信号卡"""
    return get_signal_card(conn, code, data_date)
