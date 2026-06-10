"""
字段清洗、类型转换、缺失值处理
"""

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("newstock.processing.cleaner")


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转换为 float"""
    if val is None or pd.isna(val):
        return default
    try:
        result = float(val)
        if np.isinf(result) or np.isnan(result):
            return default
        return result
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """安全转换为 int"""
    result = safe_float(val)
    if result is None:
        return default
    return int(result)


def clean_trade_date(val: Any) -> Optional[str]:
    """清洗交易日期 -> YYYY-MM-DD"""
    if val is None or pd.isna(val):
        return None
    s = str(int(float(val))) if isinstance(val, (float,)) else str(val).strip()
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def clean_ohlc(df: pd.DataFrame, code: str) -> list[str]:
    """
    检查 OHLC 数据异常。

    返回: 异常标签列表
    """
    flags = []
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            continue
        null_count = df[col].isna().sum()
        if null_count > 0:
            flags.append(f"{col}_null:{null_count}")

    # high >= max(open, close, low)
    if all(c in df.columns for c in ["high", "open", "close", "low"]):
        invalid = (df["high"] < df[["open", "close", "low"]].max(axis=1)).sum()
        if invalid > 0:
            flags.append(f"high_anomaly:{invalid}")
        invalid_low = (df["low"] > df[["open", "close"]].min(axis=1)).sum()
        if invalid_low > 0:
            flags.append(f"low_anomaly:{invalid_low}")

    # 成交量为零
    if "vol" in df.columns:
        zero_vol = (df["vol"] == 0).sum()
        if zero_vol > 0:
            flags.append(f"zero_volume:{zero_vol}")

    return flags


def detect_gaps(df: pd.DataFrame, date_col: str = "trade_date") -> list[str]:
    """检测日期缺口"""
    if df.empty or date_col not in df.columns:
        return []
    dates = pd.to_datetime(df[date_col]).dropna().sort_values().unique()
    if len(dates) < 2:
        return []
    gaps = []
    for i in range(1, len(dates)):
        delta = (dates[i] - dates[i - 1]).days
        if delta > 5:  # 超过 5 天视为缺口
            gaps.append(f"{dates[i-1].date()} -> {dates[i].date()} ({delta}d)")
    return gaps


def detect_pct_chg_anomaly(df: pd.DataFrame, threshold: float = 50.0) -> list[str]:
    """检测涨跌幅异常"""
    flags = []
    if "pct_chg" in df.columns:
        extreme = (df["pct_chg"].abs() > threshold).sum()
        if extreme > 0:
            flags.append(f"extreme_pct_chg:{extreme}")
    return flags
