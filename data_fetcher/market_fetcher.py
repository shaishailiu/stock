"""
港股/美股/A 股统一拉取入口（Longbridge 版）

基于 Longbridge CLI，通过适配层转为 Tushare 兼容 DataFrame。
"""
import logging
from datetime import date
from typing import Optional

import pandas as pd

from data_fetcher.longbridge_client import LongbridgeClient
from data_fetcher.longbridge_adapter import (
    kline_to_dataframe,
    calc_index_to_valuation_df,
    income_to_dataframe,
    balance_to_dataframe,
    cashflow_to_dataframe,
    extract_valuation_history_values,
    extract_industry_median_from_valuation,
    financial_report_to_fina_indicator,
    to_longbridge_symbol,
)
from data_fetcher.incremental_fetcher import IncrementalFetcher
from cache.raw_cache import RawCache

logger = logging.getLogger("newstock.data_fetcher.market_fetcher")


class MarketFetcher:
    """按市场执行股票数据拉取（Longbridge 数据源）"""

    def __init__(self, client: LongbridgeClient, cache: RawCache):
        self.client = client
        self.cache = cache
        self.inc = IncrementalFetcher(cache)
        # 同一次 daily_prepare 中 calc-index 结果缓存，消除重复 API 调用
        self._calc_cache: dict[str, list[dict]] = {}

    # ── 请求级 calc-index 缓存（同一次 daily_prepare 中复用）──

    def _get_calc_index(self, lb_symbol: str):
        """获取 calc-index 结果，同一只股票同一次流程只调一次 API"""
        if lb_symbol not in self._calc_cache:
            self._calc_cache[lb_symbol] = self.client.calc_index(lb_symbol)
        return self._calc_cache[lb_symbol]

    # ── 财务数据新鲜度检测（P1：避免每天重复拉取季报）──

    def is_financial_stale(
        self, market: str, api_name: str, code: str, max_age_days: int = 100
    ) -> bool:
        """
        检查财务缓存是否过期。
        通过 parquet 缓存中的最大 end_date 判断距离上次报告是否超过 max_age_days。
        """
        max_period = self.cache.get_last_report_period(market, api_name, code, "end_date")
        if not max_period:
            return True  # 无缓存，需要拉取
        try:
            period_str = str(max_period)[:8]  # "20251231"
            last_date = date.fromisoformat(
                f"{period_str[:4]}-{period_str[4:6]}-{period_str[6:8]}"
            )
            return (date.today() - last_date).days >= max_age_days
        except (ValueError, IndexError):
            return True  # 解析失败，安全起见拉取

    # ── 日线 K 线 ──

    def _fetch_kline(
        self,
        market: str,
        api_name: str,
        code: str,
        today: date,
        earliest_start_date: str,
    ) -> pd.DataFrame:
        """通用日线拉取（港股/美股/A股共用）"""
        lb_symbol = to_longbridge_symbol(code, market)

        local_df = self.cache.load(market, api_name, code)

        # 确定起始日期
        if local_df.empty:
            start = earliest_start_date
        else:
            last_date_str = str(local_df["trade_date"].max())
            try:
                # YYYYMMDD → YYYY-MM-DD
                start = f"{last_date_str[:4]}-{last_date_str[4:6]}-{last_date_str[6:8]}"
            except (IndexError, ValueError):
                start = earliest_start_date

        end_str = today.strftime("%Y-%m-%d")

        # 缓存已覆盖目标日期则跳过 API 调用（调试/盘中保护）
        if not local_df.empty and start >= end_str:
            logger.info(f"[{market}][{api_name}][{code}] Cache up-to-date ({start}), skip kline API")
            return local_df

        logger.info(f"[{market}][{api_name}][{code}] Fetching {start} -> {end_str}")

        kline_data = self.client.kline_history(
            lb_symbol, start, end_str, period="day", adjust="forward"
        )

        if not kline_data:
            logger.info(f"[{market}][{api_name}][{code}] No new data")
            return local_df

        new_df = kline_to_dataframe(kline_data, ts_code=code)

        if new_df.empty:
            return local_df

        # 增量合并
        merged = self.cache.merge_and_save(
            market, api_name, code, new_df,
            primary_keys=["ts_code", "trade_date"],
        )
        return merged

    def fetch_hk_daily(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """港股日线行情（前复权）"""
        return self._fetch_kline("hk", "kline_daily", code, today, earliest_start_date)

    def fetch_us_daily(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """美股日线行情（前复权）"""
        return self._fetch_kline("us", "kline_daily", code, today, earliest_start_date)

    def fetch_cn_daily(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股日线行情（前复权）"""
        return self._fetch_kline("cn", "kline_daily", code, today, earliest_start_date)

    # ── 估值指标（替代 adj_factor + daily_basic）──

    def fetch_cn_daily_basic(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股每日估值指标（从 Longbridge calc-index 估算）"""
        lb_symbol = to_longbridge_symbol(code, "CN")
        trade_date_str = today.strftime("%Y%m%d")

        # 缓存已有当日估值数据则跳过 calc-index（调试/盘中保护）
        local_df = self.cache.load("cn", "daily_basic", code)
        if not local_df.empty and "trade_date" in local_df.columns:
            if trade_date_str in set(local_df["trade_date"].values):
                logger.info(f"[cn][daily_basic][{code}] Cache has {trade_date_str}, skip calc-index")
                return local_df

        try:
            calc_data = self._get_calc_index(lb_symbol)
            df = calc_index_to_valuation_df(calc_data, trade_date_str, ts_code=code)

            merged = self.cache.merge_and_save(
                "cn", "daily_basic", code, df,
                primary_keys=["ts_code", "trade_date"],
            )
            return merged
        except Exception as e:
            logger.warning(f"Calc-index failed for {code}: {e}")
            return local_df

    def fetch_hk_daily_basic(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """港股每日估值指标（从 Longbridge calc-index 获取）"""
        lb_symbol = to_longbridge_symbol(code, "HK")
        trade_date_str = today.strftime("%Y%m%d")

        # 缓存已有当日估值数据则跳过 calc-index（调试/盘中保护）
        local_df = self.cache.load("hk", "daily_basic", code)
        if not local_df.empty and "trade_date" in local_df.columns:
            if trade_date_str in set(local_df["trade_date"].values):
                logger.info(f"[hk][daily_basic][{code}] Cache has {trade_date_str}, skip calc-index")
                return local_df

        try:
            calc_data = self._get_calc_index(lb_symbol)
            df = calc_index_to_valuation_df(calc_data, trade_date_str, ts_code=code)

            merged = self.cache.merge_and_save(
                "hk", "daily_basic", code, df,
                primary_keys=["ts_code", "trade_date"],
            )
            return merged
        except Exception as e:
            logger.warning(f"HK Calc-index failed for {code}: {e}")
            return local_df

    def fetch_us_daily_basic(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """美股每日估值指标（从 Longbridge calc-index 获取）"""
        lb_symbol = to_longbridge_symbol(code, "US")
        trade_date_str = today.strftime("%Y%m%d")

        # 缓存已有当日估值数据则跳过 calc-index（调试/盘中保护）
        local_df = self.cache.load("us", "daily_basic", code)
        if not local_df.empty and "trade_date" in local_df.columns:
            if trade_date_str in set(local_df["trade_date"].values):
                logger.info(f"[us][daily_basic][{code}] Cache has {trade_date_str}, skip calc-index")
                return local_df

        try:
            calc_data = self._get_calc_index(lb_symbol)
            df = calc_index_to_valuation_df(calc_data, trade_date_str, ts_code=code)

            merged = self.cache.merge_and_save(
                "us", "daily_basic", code, df,
                primary_keys=["ts_code", "trade_date"],
            )
            return merged
        except Exception as e:
            logger.warning(f"US Calc-index failed for {code}: {e}")
            return local_df

    def fetch_cn_adj_factor(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """A 股复权因子（Longbridge 前复权 K 线已含复权，此处返回空）"""
        return pd.DataFrame()

    # ── 财务数据 ──

    def _fetch_financial(
        self,
        market: str,
        report_kind: str,
        api_name: str,
        code: str,
    ) -> pd.DataFrame:
        """通用财务数据拉取"""
        lb_symbol = to_longbridge_symbol(code, market)

        try:
            report_data = self.client.financial_report(lb_symbol, kind=report_kind)

            if not report_data:
                return self.cache.load(market, api_name, code)

            if report_kind == "IS":
                new_df = income_to_dataframe(report_data, ts_code=code)
            elif report_kind == "BS":
                new_df = balance_to_dataframe(report_data, ts_code=code)
            elif report_kind == "CF":
                new_df = cashflow_to_dataframe(report_data, ts_code=code)
            else:
                new_df = pd.DataFrame(report_data)

            if new_df.empty:
                return self.cache.load(market, api_name, code)

            merged = self.cache.merge_and_save(
                market, api_name, code, new_df,
                primary_keys=["ts_code", "end_date"],
            )
            return merged
        except Exception as e:
            logger.warning(f"Financial fetch failed for {code} ({report_kind}): {e}")
            return self.cache.load(market, api_name, code)

    def fetch_hk_income(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("hk", "IS", "income", code)

    def fetch_hk_balancesheet(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("hk", "BS", "balancesheet", code)

    def fetch_hk_cashflow(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("hk", "CF", "cashflow", code)

    def fetch_hk_fina_indicator(self, code: str) -> pd.DataFrame:
        """港股财务指标（从 financial-report --latest + calc-index 合成）"""
        lb_symbol = to_longbridge_symbol(code, "HK")
        try:
            latest = self.client.financial_report_latest(lb_symbol)
            calc = self._get_calc_index(lb_symbol)

            new_df = financial_report_to_fina_indicator(
                latest_data=latest,
                calc_index=calc,
                ts_code=code,
            )

            if not new_df.empty:
                merged = self.cache.merge_and_save(
                    "hk", "fina_indicator", code, new_df,
                    primary_keys=["ts_code", "end_date"],
                )
                return merged
        except Exception as e:
            logger.warning(f"HK fina_indicator failed for {code}: {e}")
        return self.cache.load("hk", "fina_indicator", code)

    def fetch_us_income(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("us", "IS", "income", code)

    def fetch_us_balancesheet(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("us", "BS", "balancesheet", code)

    def fetch_us_cashflow(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("us", "CF", "cashflow", code)

    def fetch_us_fina_indicator(self, code: str) -> pd.DataFrame:
        """美股财务指标（从 financial-report --latest + calc-index 合成）"""
        lb_symbol = to_longbridge_symbol(code, "US")
        try:
            latest = self.client.financial_report_latest(lb_symbol)
            calc = self._get_calc_index(lb_symbol)

            new_df = financial_report_to_fina_indicator(
                latest_data=latest,
                calc_index=calc,
                ts_code=code,
            )

            if not new_df.empty:
                merged = self.cache.merge_and_save(
                    "us", "fina_indicator", code, new_df,
                    primary_keys=["ts_code", "end_date"],
                )
                return merged
        except Exception as e:
            logger.warning(f"US fina_indicator failed for {code}: {e}")
        return self.cache.load("us", "fina_indicator", code)

    def fetch_cn_income(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("cn", "IS", "income", code)

    def fetch_cn_balancesheet(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("cn", "BS", "balancesheet", code)

    def fetch_cn_cashflow(self, code: str) -> pd.DataFrame:
        return self._fetch_financial("cn", "CF", "cashflow", code)

    def fetch_cn_fina_indicator(self, code: str) -> pd.DataFrame:
        """A 股财务指标（从 financial-report --latest + calc-index 合成）"""
        lb_symbol = to_longbridge_symbol(code, "CN")
        try:
            latest = self.client.financial_report_latest(lb_symbol)
            calc = self._get_calc_index(lb_symbol)

            new_df = financial_report_to_fina_indicator(
                latest_data=latest,
                calc_index=calc,
                ts_code=code,
            )

            if not new_df.empty:
                merged = self.cache.merge_and_save(
                    "cn", "fina_indicator", code, new_df,
                    primary_keys=["ts_code", "end_date"],
                )
                return merged
        except Exception as e:
            logger.warning(f"CN fina_indicator failed for {code}: {e}")
        return self.cache.load("cn", "fina_indicator", code)

    # ── PE 历史分位 ──

    def fetch_pe_percentile(self, code: str, market: str) -> Optional[float]:
        """计算当前 PE 在近 5 年历史中的分位"""
        lb_symbol = to_longbridge_symbol(code, market)
        try:
            pe_history = self.client.valuation_history(
                lb_symbol, indicator="pe", range_years=5
            )
            if not pe_history:
                return None

            # 从嵌套的 {metrics: {pe: {list: [...]}}} 中提取时间序列
            pe_items = extract_valuation_history_values(pe_history, indicator="pe")
            if not pe_items:
                return None

            pe_values = []
            for item in pe_items:
                v = item.get("value") if isinstance(item, dict) else item
                if v is not None:
                    try:
                        pe_values.append(float(v))
                    except (ValueError, TypeError):
                        pass

            if len(pe_values) < 2:
                return None

            current_pe = pe_values[-1]
            rank = sum(1 for v in pe_values if v <= current_pe)
            percentile = round(rank / len(pe_values) * 100, 1)
            return percentile
        except Exception as e:
            logger.warning(f"PE percentile failed for {code}: {e}")
            return None

    # ── 行业估值中位数 ──

    def fetch_industry_valuation_median(
        self, code: str, market: str, indicator: str = "pe"
    ) -> Optional[float]:
        """
        获取股票所属行业的 PE/PB 中位数。

        通过 valuation --history API 的 desc 字段解析而来：
          "当前市盈率 ... 行业中位数 <strong>8.84</strong>。"

        参数:
          indicator: "pe" 或 "pb"
        """
        lb_symbol = to_longbridge_symbol(code, market)
        try:
            history = self.client.valuation_history(
                lb_symbol, indicator=indicator, range_years=5
            )
            if not history:
                return None
            return extract_industry_median_from_valuation(history, indicator=indicator)
        except Exception as e:
            logger.warning(f"Industry {indicator} median failed for {code}: {e}")
            return None

    def fetch_valuation_analysis(
        self, code: str, market: str
    ) -> dict:
        """
        一次性获取 PE 分位 + PE/PB 行业中位数，合并冗余 API 调用。

        原来每只股票需要 3 次 valuation_history 调用:
          - fetch_pe_percentile(pe)         → PE 5年分位
          - fetch_industry_valuation_median(pe) → PE 行业中位数  (重复!)
          - fetch_industry_valuation_median(pb) → PB 行业中位数

        合并后只需 2 次调用:
          - valuation_history(pe) → PE 分位 + PE 行业中位数
          - valuation_history(pb) → PB 行业中位数

        返回:
          {
            "pe_percentile_5y": float | None,
            "industry_pe_median": float | None,
            "industry_pb_median": float | None,
          }
        """
        lb_symbol = to_longbridge_symbol(code, market)
        result = {}

        # ── PE: 分位 + 行业中位数（一次调用）──
        try:
            pe_history = self.client.valuation_history(
                lb_symbol, indicator="pe", range_years=5
            )
            if pe_history:
                # PE 分位计算
                pe_items = extract_valuation_history_values(pe_history, indicator="pe")
                if pe_items:
                    pe_values = []
                    for item in pe_items:
                        v = item.get("value") if isinstance(item, dict) else item
                        if v is not None:
                            try:
                                pe_values.append(float(v))
                            except (ValueError, TypeError):
                                pass
                    if len(pe_values) >= 2:
                        current_pe = pe_values[-1]
                        rank = sum(1 for v in pe_values if v <= current_pe)
                        result["pe_percentile_5y"] = round(rank / len(pe_values) * 100, 1)

                # PE 行业中位数（从同一个 response 的 desc 字段解析）
                result["industry_pe_median"] = extract_industry_median_from_valuation(
                    pe_history, indicator="pe"
                )
        except Exception as e:
            logger.warning(f"PE analysis failed for {code}: {e}")

        # ── PB: 行业中位数 ──
        try:
            pb_history = self.client.valuation_history(
                lb_symbol, indicator="pb", range_years=5
            )
            if pb_history:
                result["industry_pb_median"] = extract_industry_median_from_valuation(
                    pb_history, indicator="pb"
                )
        except Exception as e:
            logger.warning(f"PB median failed for {code}: {e}")

        return result

    def fetch_static_info(self, code: str, market: str) -> dict:
        """获取股票静态信息（名称、行业等）"""
        lb_symbol = to_longbridge_symbol(code, market)
        try:
            info = self.client.static_info(lb_symbol)
            if isinstance(info, list) and info:
                info = info[0]
            return info if isinstance(info, dict) else {}
        except Exception as e:
            logger.warning(f"Static info failed for {code}: {e}")
            return {}

    # ── 港股通持仓 ──

    def fetch_hk_hold(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        """
        港股通持仓（Longbridge 无直接等价 API，暂时返回空）
        """
        logger.debug(f"HK hold fetch skipped (not available via Longbridge CLI): {code}")
        return pd.DataFrame()

    # ── A 股特有数据（Longbridge 不支持，返回空）──

    def fetch_cn_moneyflow(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_forecast(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_dividend(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_stk_holdernumber(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_top10_holders(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_pledge_stat(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_share_float(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_suspend_d(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_namechange(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_stock_st(
        self, code: str, today: date, earliest_start_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_cn_fina_audit(self, code: str) -> pd.DataFrame:
        return pd.DataFrame()
