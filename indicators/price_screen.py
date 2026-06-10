"""
候选池硬筛选
"""

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("newstock.indicators.price_screen")


def run_price_screen(
    stock_results: list[dict],
    min_drawdown_pct: float = 20.0,
    min_bottom_signal: int = 15,
) -> list[dict]:
    """
    根据回撤和技术底部信号筛选候选股票。

    参数:
      stock_results: list of {code, name, market, price_signal: {...}, bottom_signal: {...}}
      min_drawdown_pct: 最低回撤幅度
      min_bottom_signal: 最低底部信号分

    返回:
      通过筛选的股票列表，附带 passed 标记
    """
    passed = []
    for stock in stock_results:
        price_signal = stock.get("price_signal", {})
        bottom = stock.get("bottom_signal", {})

        drawdown = price_signal.get("drawdown_from_high_pct", 0) or 0
        score = bottom.get("bottom_signal_score", 0) or 0

        stock["passed_price_screen"] = drawdown >= min_drawdown_pct and score >= min_bottom_signal

        if stock["passed_price_screen"]:
            stock["screen_reason"] = (
                f"回撤 {drawdown:.1f}%, 底部信号分 {score}"
            )
            passed.append(stock)
        else:
            stock["screen_reason"] = (
                f"不满足: 回撤 {drawdown:.1f}%(需>={min_drawdown_pct}), "
                f"底部信号分 {score}(需>={min_bottom_signal})"
            )

    logger.info(f"Price screen: {len(passed)}/{len(stock_results)} passed")
    return stock_results
