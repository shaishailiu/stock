"""
缓存策略：覆盖窗口、去重策略、更新规则
"""

from datetime import date, timedelta
from typing import Optional

import pandas as pd

# 不同数据类型的覆盖窗口
COVERAGE_WINDOWS: dict[str, Optional[int]] = {
    "daily": 0,                # 日线行情：从最后一天开始覆盖
    "hk_daily": 0,
    "us_daily": 0,
    "hk_daily_adj": 0,
    "us_daily_adj": 0,
    "adj_factor": 30,          # 复权因子：回看 30 天
    "hk_adjfactor": 30,
    "us_adjfactor": 30,
    "daily_basic": 0,          # 每日估值：从最后一天覆盖
    "basic": None,             # 基础信息：全量
    "financial": 2,            # 财务报表：最近 2-4 期可重复拉取
    "fina_indicator": 2,
    "hk_fina_indicator": 2,
    "us_fina_indicator": 2,
    "event": 30,               # 事件数据：回看 30 天
}

# 数据主键
PRIMARY_KEYS: dict[str, list[str]] = {
    "daily": ["ts_code", "trade_date"],
    "hk_daily": ["ts_code", "trade_date"],
    "us_daily": ["ts_code", "trade_date"],
    "hk_daily_adj": ["ts_code", "trade_date"],
    "us_daily_adj": ["ts_code", "trade_date"],
    "adj_factor": ["ts_code", "trade_date"],
    "hk_adjfactor": ["ts_code", "trade_date"],
    "us_adjfactor": ["ts_code", "trade_date"],
    "daily_basic": ["ts_code", "trade_date"],
    "basic": ["ts_code"],
    "hk_basic": ["ts_code"],
    "us_basic": ["ts_code"],
    "stock_basic": ["ts_code"],
    "income": ["ts_code", "end_date", "report_type"],
    "balancesheet": ["ts_code", "end_date", "report_type"],
    "cashflow": ["ts_code", "end_date", "report_type"],
    "hk_income": ["ts_code", "end_date", "report_type"],
    "hk_balancesheet": ["ts_code", "end_date", "report_type"],
    "hk_cashflow": ["ts_code", "end_date", "report_type"],
    "us_income": ["ts_code", "end_date", "report_type"],
    "us_balancesheet": ["ts_code", "end_date", "report_type"],
    "us_cashflow": ["ts_code", "end_date", "report_type"],
    "fina_indicator": ["ts_code", "end_date"],
    "hk_fina_indicator": ["ts_code", "end_date"],
    "us_fina_indicator": ["ts_code", "end_date"],
    "forecast": ["ts_code", "end_date"],
    "dividend": ["ts_code", "end_date"],
    "moneyflow": ["ts_code", "trade_date"],
    "hk_hold": ["ts_code", "trade_date"],
}


def get_primary_keys(api_name: str) -> list[str]:
    """获取接口数据的主键"""
    return PRIMARY_KEYS.get(api_name, ["ts_code"])


def get_coverage_window(api_name: str) -> Optional[int]:
    """获取覆盖窗口（天数），None 表示全量"""
    return COVERAGE_WINDOWS.get(api_name, 0)


def get_incremental_start_date(
    local_df: pd.DataFrame,
    api_name: str,
    today: date,
    date_col: str = "trade_date",
    earliest_start_date: str = "2019-01-01",
) -> date:
    """
    计算增量拉取的起始日期。

    规则：
    - 本地无数据：从 earliest_start_date 开始
    - 本地有数据：从 last_date - coverage_window 开始
    """
    window = get_coverage_window(api_name)

    if local_df.empty:
        return date.fromisoformat(earliest_start_date)

    last_date_str = str(local_df[date_col].max())
    last_date = date.fromisoformat(last_date_str[:10])

    if window is not None and window > 0:
        return last_date - timedelta(days=window)
    return last_date
