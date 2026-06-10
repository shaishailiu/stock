"""
bottom_signal_score 计算

技术底部信号分，满分 100 分。高分只表示"更像技术底部"，不表示"更值得投资"。
"""

import logging
from typing import Optional

import pandas as pd

from indicators.technical import (
    calc_rsi,
    calc_weekly_rsi,
    calc_macd,
    calc_bollinger,
    calc_bias,
    calc_volume_ratio,
    calc_price_percentile,
)
from indicators.cycle_high import find_cycle_high

logger = logging.getLogger("newstock.indicators.bottom_signal")

# 默认权重
DEFAULT_WEIGHTS = {
    "drawdown": 20,
    "rsi_oversold": 16,
    "weekly_rsi": 8,
    "macd_divergence": 5,
    "bollinger_position": 8,
    "bias_position": 6,
    "volume_signal": 2,
    "weekly_confirmation": 5,
    "price_percentile": 10,
    "ma_position": 5,
}


def compute_bottom_signal(
    df: pd.DataFrame,
    price_col: str = "close",
    date_col: str = "trade_date",
    weights: Optional[dict] = None,
    drawdown_threshold: float = 20.0,
    rsi_oversold: float = 30.0,
    rsi_weekly_oversold: float = 35.0,
    bias_120_threshold: float = -15.0,
) -> dict:
    """
    计算技术底部信号分。

    返回:
      {
        "bottom_signal_score": int,
        "alert_level": "red" | "yellow" | "green" | "none",
        "score_detail": { ... },
        "reason": str,
      }
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS.copy()

    score = 0
    detail = {}
    reasons = []

    current_price = float(df[price_col].iloc[-1]) if not df.empty else None

    # 1. 回撤信号
    cycle = find_cycle_high(df, price_col, date_col=date_col)
    drawdown = cycle.get("drawdown_from_high_pct", 0) or 0
    if drawdown >= drawdown_threshold:
        drawdown_score = min(int(drawdown / drawdown_threshold * weights["drawdown"]), weights["drawdown"])
        score += drawdown_score
        detail["drawdown"] = drawdown_score
        reasons.append(f"阶段高点回撤 {drawdown:.1f}%")
    else:
        detail["drawdown"] = 0

    # 2. RSI 超卖
    rsi_series = calc_rsi(df, price_col=price_col)
    rsi_14 = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]) else None
    if rsi_14 is not None and rsi_14 <= rsi_oversold:
        rsi_score = min(int((rsi_oversold - rsi_14) / rsi_oversold * weights["rsi_oversold"]), weights["rsi_oversold"])
        score += rsi_score
        detail["rsi_oversold"] = rsi_score
        reasons.append(f"日线 RSI {rsi_14:.1f}")
    else:
        detail["rsi_oversold"] = 0

    # 3. 周线 RSI
    weekly_rsi_val = calc_weekly_rsi(df, price_col=price_col)
    if weekly_rsi_val is not None and weekly_rsi_val <= rsi_weekly_oversold:
        weekly_score = min(int((rsi_weekly_oversold - weekly_rsi_val) / rsi_weekly_oversold * weights["weekly_rsi"]), weights["weekly_rsi"])
        score += weekly_score
        detail["weekly_rsi"] = weekly_score
        reasons.append(f"周线 RSI {weekly_rsi_val:.1f}")
    else:
        detail["weekly_rsi"] = 0

    # 4. MACD 背离
    macd_result = calc_macd(df, price_col=price_col)
    if macd_result and macd_result.get("macd_divergence"):
        score += weights["macd_divergence"]
        detail["macd_divergence"] = weights["macd_divergence"]
        reasons.append("MACD 底背离")
    else:
        detail["macd_divergence"] = 0

    # 5. 布林带位置
    bb_pos = calc_bollinger(df, price_col=price_col)
    if bb_pos is not None and bb_pos <= 20:
        bb_score = min(int((20 - bb_pos) / 20 * weights["bollinger_position"]), weights["bollinger_position"])
        score += bb_score
        detail["bollinger_position"] = bb_score
        reasons.append(f"布林带位置 {bb_pos:.1f}%")
    else:
        detail["bollinger_position"] = 0

    # 6. BIAS 乖离率
    bias_val = calc_bias(df, price_col=price_col, period=120)
    if bias_val is not None and bias_val <= bias_120_threshold:
        bias_score = min(int(abs(bias_val - bias_120_threshold) / abs(bias_120_threshold) * weights["bias_position"]), weights["bias_position"])
        score += bias_score
        detail["bias_position"] = bias_score
        reasons.append(f"120日BIAS {bias_val:.1f}%")
    else:
        detail["bias_position"] = 0

    # 7. 量比异常
    vol_ratio = calc_volume_ratio(df)
    if vol_ratio is not None and vol_ratio >= 1.5:
        vol_score = min(int((vol_ratio - 1) * weights["volume_signal"]), weights["volume_signal"])
        score += vol_score
        detail["volume_signal"] = vol_score
        reasons.append(f"量比 {vol_ratio:.1f}")
    else:
        detail["volume_signal"] = 0

    # 8. 周线确认（当周线和日线同时超卖时加分）
    if rsi_14 is not None and weekly_rsi_val is not None:
        if rsi_14 <= rsi_oversold and weekly_rsi_val <= rsi_weekly_oversold:
            score += weights["weekly_confirmation"]
            detail["weekly_confirmation"] = weights["weekly_confirmation"]
            reasons.append("日线和周线同时超卖")
        else:
            detail["weekly_confirmation"] = 0
    else:
        detail["weekly_confirmation"] = 0

    # 9. 价格分位
    price_pct = calc_price_percentile(df, price_col=price_col)
    if price_pct is not None and price_pct <= 20:
        pct_score = min(int((20 - price_pct) / 20 * weights["price_percentile"]), weights["price_percentile"])
        score += pct_score
        detail["price_percentile"] = pct_score
        reasons.append(f"价格处于1年分位 {price_pct:.1f}%")
    else:
        detail["price_percentile"] = 0

    # 10. 均线位置（价格明显低于中长期均线）
    ma_score = 0
    ma_reasons = []
    for period in [60, 120]:
        if len(df) >= period:
            ma = df[price_col].rolling(window=period, min_periods=period).mean()
            if not pd.isna(ma.iloc[-1]) and ma.iloc[-1] > 0:
                pct = (current_price - ma.iloc[-1]) / ma.iloc[-1] * 100
                if pct < -10:
                    ma_score += weights["ma_position"] // 2
                    ma_reasons.append(f"价格低于{period}日均线 {pct:.1f}%")
    if ma_score > 0:
        score += min(ma_score, weights["ma_position"])
        detail["ma_position"] = min(ma_score, weights["ma_position"])
        reasons.extend(ma_reasons)
    else:
        detail["ma_position"] = 0

    # ---- 判定 ----
    score = min(score, 100)

    if score >= 70:
        alert_level = "red"
    elif score >= 40:
        alert_level = "yellow"
    elif score >= 15:
        alert_level = "green"
    else:
        alert_level = "none"

    rsi_val = rsi_14 if rsi_14 is not None else 0

    return {
        "bottom_signal_score": score,
        "alert_level": alert_level,
        "score_detail": detail,
        "reason": "，".join(reasons) if reasons else "无明显的技术底部信号",
        "rsi_14": rsi_val,
        "weekly_rsi": weekly_rsi_val,
    }
