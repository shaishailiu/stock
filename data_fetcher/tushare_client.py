"""
Tushare API 封装
"""

import time
import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd
import tushare as ts

logger = logging.getLogger("newstock.data_fetcher.tushare_client")


class TushareClient:
    """Tushare API 客户端"""

    def __init__(
        self,
        token: str,
        timeout: int = 30,
        max_retries: int = 3,
        rate_limit_per_minute: int = 200,
    ):
        ts.set_token(token)
        self.pro = ts.pro_api(timeout=timeout)
        self.max_retries = max_retries
        self._request_count = 0
        self._window_start = time.time()
        self._rate_limit = rate_limit_per_minute

    def _rate_limit_check(self) -> None:
        """简单频率控制"""
        self._request_count += 1
        elapsed = time.time() - self._window_start
        if elapsed >= 60:
            self._request_count = 1
            self._window_start = time.time()
        elif self._request_count > self._rate_limit:
            wait = 60 - elapsed + 1
            logger.info(f"Rate limit approaching, sleeping {wait:.0f}s")
            time.sleep(wait)
            self._request_count = 1
            self._window_start = time.time()

    def _call(self, api_name: str, **kwargs) -> pd.DataFrame:
        """带重试的 API 调用"""
        for attempt in range(1, self.max_retries + 1):
            try:
                self._rate_limit_check()
                func = getattr(self.pro, api_name)
                result = func(**kwargs)
                return result if isinstance(result, pd.DataFrame) else pd.DataFrame()
            except Exception as e:
                logger.warning(f"{api_name} attempt {attempt}/{self.max_retries} failed: {e}")
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt)
        return pd.DataFrame()

    # ---- 港股 ----
    def hk_basic(self, ts_code: str = "") -> pd.DataFrame:
        if ts_code:
            return self._call("hk_basic", ts_code=ts_code)
        return self._call("hk_basic")

    def hk_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return self._call("hk_daily", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_daily_adj(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return self._call("hk_daily_adj", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_adjfactor(
        self, ts_code: str, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame:
        return self._call("hk_adjfactor", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_income(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("hk_income", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_balancesheet(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("hk_balancesheet", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_cashflow(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("hk_cashflow", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_fina_indicator(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("hk_fina_indicator", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_hold(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("hk_hold", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def hk_tradecal(self, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("hk_tradecal", start_date=start_date, end_date=end_date)

    # ---- 美股 ----
    def us_basic(self, ts_code: str = "") -> pd.DataFrame:
        if ts_code:
            return self._call("us_basic", ts_code=ts_code)
        return self._call("us_basic")

    def us_daily(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return self._call("us_daily", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_daily_adj(
        self, ts_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return self._call("us_daily_adj", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_adjfactor(
        self, ts_code: str, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame:
        return self._call("us_adjfactor", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_income(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("us_income", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_balancesheet(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("us_balancesheet", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_cashflow(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("us_cashflow", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_fina_indicator(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("us_fina_indicator", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def us_tradecal(self, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("us_tradecal", start_date=start_date, end_date=end_date)

    # ---- A 股 ----
    def stock_basic(self, ts_code: str = "", list_status: str = "L") -> pd.DataFrame:
        kwargs = {"list_status": list_status}
        if ts_code:
            kwargs["ts_code"] = ts_code
        return self._call("stock_basic", **kwargs)

    def daily(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return self._call("daily", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def adj_factor(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("adj_factor", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def daily_basic(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("daily_basic", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def income(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("income", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def balancesheet(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("balancesheet", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def cashflow(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("cashflow", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def fina_indicator(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("fina_indicator", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def fina_audit(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("fina_audit", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def stock_st(self, ts_code: str = "", trade_date: str = "") -> pd.DataFrame:
        kwargs = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if trade_date:
            kwargs["trade_date"] = trade_date
        return self._call("stock_st", **kwargs)

    def forecast(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("forecast", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def express(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("express", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def dividend(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("dividend", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def suspend_d(self, ts_code: str = "", start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("suspend_d", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def namechange(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("namechange", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def moneyflow(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("moneyflow", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def stk_holdernumber(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("stk_holdernumber", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def top10_holders(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("top10_holders", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def pledge_stat(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("pledge_stat", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def pledge_detail(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("pledge_detail", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def share_float(self, ts_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("share_float", ts_code=ts_code, start_date=start_date, end_date=end_date)

    def trade_cal(self, exchange: str = "SSE", start_date: str = "", end_date: str = "") -> pd.DataFrame:
        return self._call("trade_cal", exchange=exchange, start_date=start_date, end_date=end_date)
