"""
增量更新逻辑
"""

import logging
from datetime import date, datetime
from typing import Callable, Optional

import pandas as pd

from cache.raw_cache import RawCache
from cache.cache_policy import get_incremental_start_date, get_primary_keys

logger = logging.getLogger("newstock.data_fetcher.incremental_fetcher")


class IncrementalFetcher:
    """增量数据拉取器"""

    def __init__(self, cache: RawCache):
        self.cache = cache

    def fetch_daily(
        self,
        market: str,
        api_name: str,
        ts_code: str,
        fetch_func: Callable[[str, str, str], pd.DataFrame],
        today: date,
        earliest_start_date: str = "2019-01-01",
        date_col: str = "trade_date",
    ) -> pd.DataFrame:
        """
        日线类数据的增量拉取。

        规则：
        - 本地无数据：从 earliest_start_date 开始
        - 本地有数据：从 last_date - coverage_window 开始
        - 合并去重后写入 Parquet
        """
        local_df = self.cache.load(market, api_name, ts_code)
        start = get_incremental_start_date(local_df, api_name, today, date_col, earliest_start_date)
        end = today

        logger.info(f"[{market}][{api_name}][{ts_code}] Fetching {start} -> {end}")

        start_str = start.strftime("%Y%m%d")
        end_str = end.strftime("%Y%m%d")

        new_df = fetch_func(ts_code, start_str, end_str)

        if new_df.empty:
            logger.info(f"[{market}][{api_name}][{ts_code}] No new data")
            return local_df

        pk = get_primary_keys(api_name)
        merged = self.cache.merge_and_save(market, api_name, ts_code, new_df, pk)
        return merged

    def fetch_financial(
        self,
        market: str,
        api_name: str,
        ts_code: str,
        fetch_func: Callable[[str, str, str], pd.DataFrame],
        period_col: str = "end_date",
    ) -> pd.DataFrame:
        """
        财务类数据的拉取。

        规则：
        - 本地无数据：从最早日期开始
        - 本地有数据：从 last_period 开始，覆盖最近 2-4 期
        """
        local_df = self.cache.load(market, api_name, ts_code)
        start_str = ""
        end_str = ""

        if not local_df.empty:
            last_period = str(local_df[period_col].max())
            # 回退 4 个季度确保覆盖
            try:
                last_date = datetime.strptime(last_period[:8], "%Y%m%d")
                start = last_date.replace(year=last_date.year - 1)
                start_str = start.strftime("%Y%m%d")
            except ValueError:
                pass

        logger.info(f"[{market}][{api_name}][{ts_code}] Fetching financial {start_str} -> {end_str}")

        new_df = fetch_func(ts_code, start_str, end_str)

        if new_df.empty:
            logger.info(f"[{market}][{api_name}][{ts_code}] No new financial data")
            return local_df

        pk = get_primary_keys(api_name)
        merged = self.cache.merge_and_save(market, api_name, ts_code, new_df, pk)
        return merged

    def fetch_basic(
        self,
        market: str,
        api_name: str,
        ts_code: str,
        fetch_func: Callable[[str], pd.DataFrame],
    ) -> pd.DataFrame:
        """基础信息拉取（全量覆盖）"""
        logger.info(f"[{market}][{api_name}][{ts_code}] Fetching basic info")
        new_df = fetch_func(ts_code) if ts_code else fetch_func()

        if new_df.empty:
            return self.cache.load(market, api_name, ts_code)

        self.cache.save(market, api_name, ts_code, new_df)
        return new_df
