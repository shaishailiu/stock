"""
技术指标计算：RSI、MACD、布林带、BIAS、量比
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("newstock.indicators.technical")

# 列名映射（Tushare 列 -> 计算用列）
COL_MAP = {
    "close": "close",
    "adj_close": "close",  # 如果使用复权行情，列名可能是 close
    "vol": "vol",
    "volume": "vol",
}


def _get_col(df: pd.DataFrame, col_name: str) -> str:
    """根据数据框实际列名获取标准列"""
    for key in COL_MAP:
        if key in df.columns:
            return df.columns[df.columns == key][0] if isinstance(df.columns, pd.Index) else key
    return df.columns[0] if col_name in df.columns else col_name


def calc_ma(series: pd.Series, period: int) -> pd.Series:
    """计算移动均线"""
    return series.rolling(window=period, min_periods=period).mean()


def calc_rsi(df: pd.DataFrame, period: int = 14, price_col: str = "close") -> Optional[pd.Series]:
    """计算 RSI"""
    if price_col not in df.columns or len(df) < period + 1:
        return None

    delta = df[price_col].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_weekly_rsi(df: pd.DataFrame, period: int = 14, price_col: str = "close") -> Optional[float]:
    """计算周线 RSI"""
    if price_col not in df.columns or "trade_date" not in df.columns:
        return None

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date")

    weekly = df[price_col].resample("W").last().dropna()
    if len(weekly) < period + 1:
        return None

    rsi_series = calc_rsi(weekly.to_frame(name=price_col), period, price_col)
    if rsi_series is None or rsi_series.empty:
        return None
    return float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None


def calc_macd(
    df: pd.DataFrame,
    price_col: str = "close",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[dict]:
    """计算 MACD"""
    if price_col not in df.columns or len(df) < slow + signal:
        return None

    ema_fast = df[price_col].ewm(span=fast, min_periods=fast, adjust=False).mean()
    ema_slow = df[price_col].ewm(span=slow, min_periods=slow, adjust=False).mean()

    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, min_periods=signal, adjust=False).mean()
    hist = 2 * (dif - dea)

    latest = {
        "macd_dif": float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else None,
        "macd_dea": float(dea.iloc[-1]) if not pd.isna(dea.iloc[-1]) else None,
        "macd_hist": float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else None,
        "macd_divergence": _detect_macd_divergence(df, dif, price_col),
    }
    return latest


def _detect_macd_divergence(
    df: pd.DataFrame, dif: pd.Series, price_col: str
) -> bool:
    """检测 MACD 底背离"""
    # 取最近 60 天数据，检测价格新低但 DIF 未新低
    window = min(60, len(df))
    recent_df = df.iloc[-window:]
    recent_dif = dif.iloc[-window:]

    price_low_idx = recent_df[price_col].idxmin()
    # 找到价格最低点之前的 DIF 最低点
    before_low = recent_dif.loc[:price_low_idx]
    if before_low.empty:
        return False

    dif_min_before = before_low.min()
    dif_current = recent_dif.iloc[-1]

    # 价格新低，但 DIF 高于前期低点
    current_price = recent_df[price_col].iloc[-1]
    min_price = recent_df[price_col].min()

    if current_price <= min_price * 1.02 and not pd.isna(dif_current) and not pd.isna(dif_min_before):
        if dif_current > dif_min_before:
            return True
    return False


def calc_bollinger(
    df: pd.DataFrame,
    price_col: str = "close",
    period: int = 20,
    std: int = 2,
) -> Optional[float]:
    """计算布林带位置 (0-100)"""
    if price_col not in df.columns or len(df) < period:
        return None

    ma = df[price_col].rolling(window=period, min_periods=period).mean()
    std_val = df[price_col].rolling(window=period, min_periods=period).std()

    upper = ma + std * std_val
    lower = ma - std * std_val

    current = df[price_col].iloc[-1]
    band_range = upper.iloc[-1] - lower.iloc[-1]

    if pd.isna(band_range) or band_range == 0:
        return None

    position = (current - lower.iloc[-1]) / band_range * 100
    return round(float(position), 2)


def calc_bias(df: pd.DataFrame, period: int = 120, price_col: str = "close") -> Optional[float]:
    """计算 BIAS 乖离率"""
    if price_col not in df.columns or len(df) < period:
        return None
    ma = df[price_col].rolling(window=period, min_periods=period).mean()
    if pd.isna(ma.iloc[-1]) or ma.iloc[-1] == 0:
        return None
    bias = (df[price_col].iloc[-1] - ma.iloc[-1]) / ma.iloc[-1] * 100
    return round(float(bias), 2)


def calc_volume_ratio(df: pd.DataFrame, base_days: int = 5) -> Optional[float]:
    """计算量比"""
    vol_col = None
    for c in ["vol", "volume"]:
        if c in df.columns:
            vol_col = c
            break
    if vol_col is None or len(df) < base_days + 1:
        return None

    recent_vol = df[vol_col].iloc[-1]
    base_vol = df[vol_col].iloc[-(base_days + 1):-1].mean()

    if pd.isna(base_vol) or base_vol == 0:
        return None

    ratio = recent_vol / base_vol
    return round(float(ratio), 2)


def calc_price_percentile(df: pd.DataFrame, price_col: str = "close", days: int = 252) -> Optional[float]:
    """计算价格分位"""
    if price_col not in df.columns or len(df) < days:
        return None
    window = df[price_col].iloc[-days:]
    current = window.iloc[-1]
    rank = (window <= current).sum()
    percentile = rank / len(window) * 100
    return round(float(percentile), 2)
