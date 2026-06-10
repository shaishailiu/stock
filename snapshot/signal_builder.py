"""
生成 SignalCard
"""

import logging
from typing import Optional

import pandas as pd

from indicators.bottom_signal import compute_bottom_signal

logger = logging.getLogger("newstock.snapshot.signal_builder")


def build_signal_card(
    code: str,
    daily_df: pd.DataFrame,
    price_col: str = "close",
    date_col: str = "trade_date",
    drawdown_threshold: float = 20.0,
    rsi_oversold: float = 30.0,
    rsi_weekly_oversold: float = 35.0,
    bias_120_threshold: float = -15.0,
    min_bottom_signal: int = 15,
) -> dict:
    """
    构建技术底部信号卡。

    返回:
      {
        "code": str,
        "passed_price_screen": bool,
        "alert_level": "red" | "yellow" | "green" | "none",
        "bottom_signal_score": int,
        "score_detail": dict,
        "reason": str,
      }
    """
    bottom = compute_bottom_signal(
        df=daily_df,
        price_col=price_col,
        date_col=date_col,
        drawdown_threshold=drawdown_threshold,
        rsi_oversold=rsi_oversold,
        rsi_weekly_oversold=rsi_weekly_oversold,
        bias_120_threshold=bias_120_threshold,
    )

    passed = bottom["bottom_signal_score"] >= min_bottom_signal

    return {
        "code": code,
        "passed_price_screen": passed,
        "alert_level": bottom["alert_level"],
        "bottom_signal_score": bottom["bottom_signal_score"],
        "score_detail": bottom["score_detail"],
        "reason": bottom["reason"],
    }
