"""
SQLite 数据库连接与初始化
"""

import sqlite3
import os
import logging
from pathlib import Path

logger = logging.getLogger("newstock.storage.db")

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: str) -> sqlite3.Connection:
    """创建数据库连接"""
    # 确保目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str) -> None:
    """初始化数据库表结构"""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    logger.info(f"Initializing database: {db_path}")
    conn = get_connection(db_path)
    try:
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        conn.commit()
        logger.info("Database initialized successfully")
    except Exception:
        logger.exception("Failed to initialize database")
        raise
    finally:
        conn.close()
