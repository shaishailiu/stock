"""
Parquet 文件存储
"""

import os
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("newstock.cache.parquet_store")


def _resolve_path(root: str, market: str, api_name: str, ts_code: str) -> Path:
    """解析 Parquet 文件路径"""
    return Path(root) / market / api_name / f"{ts_code}.parquet"


def load(root: str, market: str, api_name: str, ts_code: str) -> pd.DataFrame:
    """读取 Parquet 缓存"""
    path = _resolve_path(root, market, api_name, ts_code)
    if not path.exists():
        logger.debug(f"Cache miss: {path}")
        return pd.DataFrame()
    df = pd.read_parquet(path)
    logger.debug(f"Cache hit: {path} ({len(df)} rows)")
    return df


def save(
    root: str,
    market: str,
    api_name: str,
    ts_code: str,
    df: pd.DataFrame,
) -> None:
    """写入 Parquet 缓存（覆盖）"""
    if df.empty:
        logger.warning(f"Skip saving empty df for {market}/{api_name}/{ts_code}")
        return
    path = _resolve_path(root, market, api_name, ts_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.debug(f"Saved: {path} ({len(df)} rows)")


def get_max_trade_date(
    root: str,
    market: str,
    api_name: str,
    ts_code: str,
    date_col: str = "trade_date",
) -> Optional[str]:
    """获取缓存中最大交易日"""
    df = load(root, market, api_name, ts_code)
    if df.empty:
        return None
    return str(df[date_col].max())


def get_max_report_period(
    root: str,
    market: str,
    api_name: str,
    ts_code: str,
    period_col: str = "end_date",
) -> Optional[str]:
    """获取缓存中最大报告期"""
    df = load(root, market, api_name, ts_code)
    if df.empty:
        return None
    return str(df[period_col].max())
