"""
缓存策略：覆盖窗口、去重策略、更新规则（Longbridge 版）
"""

from datetime import date, timedelta
from typing import Optional

import pandas as pd

# 不同数据类型的覆盖窗口
COVERAGE_WINDOWS: dict[str, Optional[int]] = {
    "kline_daily": 0,          # 日线行情：从最后一天开始覆盖
    "daily_basic": 0,          # 每日估值：从最后一天覆盖
    "financial": 2,            # 财务报表：最近 2-4 期可重复拉取
    "fina_indicator": 2,
}

# 数据主键
PRIMARY_KEYS: dict[str, list[str]] = {
    # K 线
    "kline_daily": ["ts_code", "trade_date"],
    # 估值
    "daily_basic": ["ts_code", "trade_date"],
    # 财务
    "income": ["ts_code", "end_date"],
    "balancesheet": ["ts_code", "end_date"],
    "cashflow": ["ts_code", "end_date"],
    "fina_indicator": ["ts_code", "end_date"],
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
    try:
        # YYYYMMDD 格式
        last_date = date.fromisoformat(
            f"{last_date_str[:4]}-{last_date_str[4:6]}-{last_date_str[6:8]}"
        )
    except (IndexError, ValueError):
        return date.fromisoformat(earliest_start_date)

    if window is not None and window > 0:
        return last_date - timedelta(days=window)
    return last_date
