"""
生成 StockSnapshot
"""

import logging
from datetime import date
from typing import Any, Optional

import pandas as pd

from indicators.cycle_high import find_cycle_high
from indicators.technical import (
    calc_ma,
    calc_rsi,
    calc_weekly_rsi,
    calc_macd,
    calc_bollinger,
    calc_bias,
    calc_volume_ratio,
    calc_price_percentile,
)
from processing.calendar import to_date_str
from processing.code_mapper import normalize_code

logger = logging.getLogger("newstock.snapshot.snapshot_builder")


def build_stock_snapshot(
    code: str,
    daily_df: pd.DataFrame,
    valuation_df: Optional[pd.DataFrame] = None,
    financial: Optional[dict] = None,
    balance: Optional[dict] = None,
    cashflow: Optional[dict] = None,
    risk: Optional[dict] = None,
    capital_flow: Optional[dict] = None,
    data_quality: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> dict:
    """
    构建单只股票的 StockSnapshot。

    参数:
      code: 股票代码（系统标准格式）
      daily_df: 日线行情 DataFrame
      valuation_df: 每日估值 DataFrame
      financial/balance/cashflow/risk/capital_flow: 各模块摘要
      data_quality: 数据质量标记
      extra: 额外信息（如股票名称、行业等）

    返回:
      StockSnapshot dict
    """
    info = normalize_code(code)
    price_col = _detect_price_col(daily_df)

    snapshot = {
        "code": info["code"],
        "name": extra.get("name") if extra else None,
        "market": info["market"],
        "industry": extra.get("industry") if extra else None,
        "date": to_date_str(date.today()),
    }

    # ---- price_signal ----
    ps = {
        "current_price": float(daily_df[price_col].iloc[-1]) if not daily_df.empty and price_col in daily_df.columns else None,
        "pct_chg": _safe_val(daily_df, "pct_chg"),
    }

    cycle = find_cycle_high(daily_df, price_col)
    ps.update({k: v for k, v in cycle.items() if k != "current_price"})

    # 均线
    for period in [20, 60, 120]:
        if len(daily_df) >= period:
            ma = calc_ma(daily_df[price_col], period)
            ps[f"ma{period}"] = float(ma.iloc[-1]) if not pd.isna(ma.iloc[-1]) else None
        else:
            ps[f"ma{period}"] = None

    # RSI
    rsi_series = calc_rsi(daily_df, price_col=price_col)
    ps["rsi_14"] = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]) else None
    ps["weekly_rsi"] = calc_weekly_rsi(daily_df, price_col=price_col)

    # MACD
    macd = calc_macd(daily_df, price_col=price_col)
    if macd:
        ps.update(macd)
    else:
        ps.setdefault("macd_dif", None)
        ps.setdefault("macd_dea", None)
        ps.setdefault("macd_hist", None)
        ps.setdefault("macd_divergence", False)

    # 布林带
    ps["bollinger_position_pct"] = calc_bollinger(daily_df, price_col=price_col)

    # BIAS
    ps["bias_120"] = calc_bias(daily_df, period=120, price_col=price_col)

    # 量比
    ps["volume_ratio"] = calc_volume_ratio(daily_df)

    # 价格分位
    ps["price_percentile_1y"] = calc_price_percentile(daily_df, price_col=price_col)

    snapshot["price_signal"] = ps

    # ---- valuation ----
    if valuation_df is not None and not valuation_df.empty:
        latest = valuation_df.iloc[-1]
        snapshot["valuation"] = {
            "pe_ttm": _safe_val(latest, "pe_ttm"),
            "pb": _safe_val(latest, "pb"),
            "ps_ttm": _safe_val(latest, "ps_ttm"),
            "dividend_yield_ttm": _safe_val(latest, "dv_ttm"),
            "market_cap": _safe_val(latest, "total_mv"),
            "float_market_cap": _safe_val(latest, "circ_mv"),
            "turnover_rate": _safe_val(latest, "turnover_rate"),
        }
    else:
        snapshot["valuation"] = {}

    # ---- fundamental ----
    snapshot["fundamental"] = financial or {}

    # ---- balance_sheet ----
    snapshot["balance_sheet"] = balance or {}

    # ---- cashflow ----
    snapshot["cashflow"] = cashflow or {}

    # ---- risk ----
    snapshot["risk"] = risk or {}

    # ---- capital_flow ----
    snapshot["capital_flow"] = capital_flow or {}

    # ---- data_quality ----
    snapshot["data_quality"] = data_quality or {
        "data_missing": [],
        "data_stale": [],
        "data_estimated": [],
        "source_apis": [],
    }

    return snapshot


def _detect_price_col(df: pd.DataFrame) -> str:
    """检测价格列"""
    for col in ["adj_close", "close"]:
        if col in df.columns:
            return col
    return "close"


def _safe_val(row_or_df, col_name: str) -> Optional[float]:
    """安全获取数值"""
    try:
        if isinstance(row_or_df, pd.DataFrame):
            val = row_or_df[col_name].iloc[-1]
        else:
            val = row_or_df[col_name]
        if pd.isna(val):
            return None
        return float(val)
    except (KeyError, ValueError, IndexError):
        return None
