"""
港股/美股/A 股统一拉取入口
"""

import logging
from datetime import date
from typing import Callable

import pandas as pd

from data_fetcher.tushare_client import TushareClient
from data_fetcher.incremental_fetcher import IncrementalFetcher
from cache.raw_cache import RawCache

logger = logging.getLogger("newstock.data_fetcher.market_fetcher")

# 拉取函数类型
FetchFunc = Callable[[str, str, str], pd.DataFrame]
FetchBasicFunc = Callable[[str], pd.DataFrame]


class MarketFetcher:
    """按市场执行股票数据拉取"""

    def __init__(self, client: TushareClient, cache: RawCache):
        self.client = client
        self.inc = IncrementalFetcher(cache)

    def fetch_hk_daily(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """港股日线行情（含复权）"""
        # 优先使用复权行情
        return self.inc.fetch_daily(
            "hk", "hk_daily_adj", ts_code,
            self.client.hk_daily_adj,
            today, earliest_start_date,
        )

    def fetch_us_daily(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """美股日线行情（含复权）"""
        return self.inc.fetch_daily(
            "us", "us_daily_adj", ts_code,
            self.client.us_daily_adj,
            today, earliest_start_date,
        )

    def fetch_cn_daily(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股日线行情"""
        return self.inc.fetch_daily(
            "cn", "daily", ts_code,
            self.client.daily,
            today, earliest_start_date,
        )

    def fetch_cn_adj_factor(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股复权因子"""
        return self.inc.fetch_daily(
            "cn", "adj_factor", ts_code,
            self.client.adj_factor,
            today, earliest_start_date,
        )

    def fetch_cn_daily_basic(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股每日估值指标"""
        return self.inc.fetch_daily(
            "cn", "daily_basic", ts_code,
            self.client.daily_basic,
            today, earliest_start_date,
        )

    # ---- 财务数据 ----

    def fetch_hk_income(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("hk", "hk_income", ts_code, self.client.hk_income)

    def fetch_hk_balancesheet(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("hk", "hk_balancesheet", ts_code, self.client.hk_balancesheet)

    def fetch_hk_cashflow(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("hk", "hk_cashflow", ts_code, self.client.hk_cashflow)

    def fetch_hk_fina_indicator(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("hk", "hk_fina_indicator", ts_code, self.client.hk_fina_indicator)

    def fetch_us_income(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("us", "us_income", ts_code, self.client.us_income)

    def fetch_us_balancesheet(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("us", "us_balancesheet", ts_code, self.client.us_balancesheet)

    def fetch_us_cashflow(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("us", "us_cashflow", ts_code, self.client.us_cashflow)

    def fetch_us_fina_indicator(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("us", "us_fina_indicator", ts_code, self.client.us_fina_indicator)

    def fetch_cn_income(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "income", ts_code, self.client.income)

    def fetch_cn_balancesheet(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "balancesheet", ts_code, self.client.balancesheet)

    def fetch_cn_cashflow(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "cashflow", ts_code, self.client.cashflow)

    def fetch_cn_fina_indicator(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "fina_indicator", ts_code, self.client.fina_indicator)

    # ---- 事件与风险 ----

    def fetch_cn_forecast(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "forecast", ts_code,
                                         self.client.forecast, period_col="end_date")

    def fetch_cn_dividend(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "dividend", ts_code,
                                         self.client.dividend, period_col="end_date")

    def fetch_hk_hold(self, ts_code: str, today: date, earliest_start_date: str) -> pd.DataFrame:
        return self.inc.fetch_daily("hk", "hk_hold", ts_code, self.client.hk_hold,
                                      today, earliest_start_date)

    def fetch_cn_moneyflow(self, ts_code: str, today: date, earliest_start_date: str) -> pd.DataFrame:
        return self.inc.fetch_daily("cn", "moneyflow", ts_code, self.client.moneyflow,
                                      today, earliest_start_date)

    def fetch_cn_stk_holdernumber(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "stk_holdernumber", ts_code,
                                         self.client.stk_holdernumber, period_col="end_date")

    def fetch_cn_top10_holders(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "top10_holders", ts_code,
                                         self.client.top10_holders, period_col="end_date")

    def fetch_cn_pledge_stat(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "pledge_stat", ts_code,
                                         self.client.pledge_stat, period_col="end_date")

    def fetch_cn_share_float(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "share_float", ts_code,
                                         self.client.share_float, period_col="ann_date")

    def fetch_cn_suspend_d(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        return self.inc.fetch_daily("cn", "suspend_d", ts_code, self.client.suspend_d,
                                      today, earliest_start_date, date_col="suspend_date")

    def fetch_cn_namechange(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "namechange", ts_code,
                                         self.client.namechange, period_col="ann_date")

    def fetch_cn_stock_st(
        self, ts_code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股 ST 列表"""
        # stock_st 可以按 trade_date 查询
        return self.inc.fetch_daily("cn", "stock_st", ts_code,
                                      self.client.stock_st,
                                      today, earliest_start_date)

    def fetch_cn_fina_audit(self, ts_code: str) -> pd.DataFrame:
        return self.inc.fetch_financial("cn", "fina_audit", ts_code,
                                         self.client.fina_audit, period_col="end_date")
