"""
阶段高点与回撤计算
"""

import logging
from datetime import date, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger("newstock.indicators.cycle_high")

from processing.calendar import to_date_str


def find_cycle_high(
    df: pd.DataFrame,
    price_col: str = "close",
    lookback_days: int = 180,
    date_col: str = "trade_date",
) -> dict:
    """
    查找阶段高点并计算回撤。

    返回:
      {
        "cycle_high_price": float,
        "cycle_high_date": "YYYY-MM-DD",
        "drawdown_from_high_pct": float,      # 正数表示回撤幅度
        "high_52w": float,
        "high_52w_date": "YYYY-MM-DD",
        "low_52w": float,
        "low_52w_date": "YYYY-MM-DD",
        "distance_from_low_pct": float,        # 正数表示从低点反弹幅度
      }
    """
    if df.empty or price_col not in df.columns:
        return {}

    # 确保按日期排序
    if date_col in df.columns:
        df = df.sort_values(date_col).reset_index(drop=True)

    current_price = float(df[price_col].iloc[-1])
    result = {"current_price": current_price}

    # ---- 阶段高点（回看 lookback_days 天） ----
    window = df.iloc[-min(lookback_days, len(df)):]
    if len(window) > 0:
        high_idx = window[price_col].idxmax()
        high_row = window.loc[high_idx]
        result["cycle_high_price"] = float(high_row[price_col])
        if date_col in window.columns:
            result["cycle_high_date"] = to_date_str(high_row[date_col])

        # 回撤 = (高点 - 当前) / 高点 * 100
        if result["cycle_high_price"] > 0:
            result["drawdown_from_high_pct"] = round(
                (result["cycle_high_price"] - current_price) / result["cycle_high_price"] * 100, 2
            )

    # ---- 52 周高低点 ----
    lookback_52w = min(252, len(df))
    window_52w = df.iloc[-lookback_52w:]

    high_52w_idx = window_52w[price_col].idxmax()
    low_52w_idx = window_52w[price_col].idxmin()

    result["high_52w"] = float(window_52w.loc[high_52w_idx, price_col])
    result["low_52w"] = float(window_52w.loc[low_52w_idx, price_col])

    if date_col in window_52w.columns:
        result["high_52w_date"] = to_date_str(window_52w.loc[high_52w_idx, date_col])
        result["low_52w_date"] = to_date_str(window_52w.loc[low_52w_idx, date_col])

    # 距低点反弹
    if result["low_52w"] > 0:
        result["distance_from_low_pct"] = round(
            (current_price - result["low_52w"]) / result["low_52w"] * 100, 2
        )

    return result
