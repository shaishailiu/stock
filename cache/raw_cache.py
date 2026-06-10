"""
原始数据缓存读写
"""

import logging
from typing import Optional

import pandas as pd

from cache import parquet_store

logger = logging.getLogger("newstock.cache.raw_cache")


class RawCache:
    """原始 Tushare 数据缓存"""

    def __init__(self, root: str):
        self.root = root

    # ---- 读取 ----

    def load(self, market: str, api_name: str, ts_code: str) -> pd.DataFrame:
        return parquet_store.load(self.root, market, api_name, ts_code)

    # ---- 写入 ----

    def save(self, market: str, api_name: str, ts_code: str, df: pd.DataFrame) -> None:
        parquet_store.save(self.root, market, api_name, ts_code, df)

    # ---- 增量合并 ----

    def merge_and_save(
        self,
        market: str,
        api_name: str,
        ts_code: str,
        new_df: pd.DataFrame,
        primary_keys: list[str],
    ) -> pd.DataFrame:
        """增量合并新数据并写入缓存"""
        if new_df.empty:
            return self.load(market, api_name, ts_code)

        old_df = self.load(market, api_name, ts_code)
        merged = pd.concat([old_df, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=primary_keys, keep="last")
        merged = merged.sort_values(primary_keys[0])  # 按第一个主键排序
        merged = merged.reset_index(drop=True)

        self.save(market, api_name, ts_code, merged)
        return merged

    # ---- 日期查询 ----

    def get_last_trade_date(
        self,
        market: str,
        api_name: str,
        ts_code: str,
        date_col: str = "trade_date",
    ) -> Optional[str]:
        return parquet_store.get_max_trade_date(self.root, market, api_name, ts_code, date_col)

    def get_last_report_period(
        self,
        market: str,
        api_name: str,
        ts_code: str,
        period_col: str = "end_date",
    ) -> Optional[str]:
        return parquet_store.get_max_report_period(self.root, market, api_name, ts_code, period_col)
