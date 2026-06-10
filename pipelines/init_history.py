"""
首次历史数据初始化

加载全量历史数据并首次生成快照。
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

from pipelines.daily_prepare import load_config, load_watchlist
from storage.db import init_db
from data_fetcher.tushare_client import TushareClient
from data_fetcher.market_fetcher import MarketFetcher
from cache.raw_cache import RawCache

logger = logging.getLogger("newstock.pipelines.init_history")


def run_init_history(
    config_path: str = "config/config.yaml",
    markets: Optional[list[str]] = None,
) -> dict:
    """
    首次历史数据初始化。

    参数:
      config_path: 配置文件路径
      markets: 要初始化的市场列表，例如 ["hk", "us", "cn"]；None 表示全部启用

    返回:
      初始化结果摘要
    """
    config = load_config(config_path)
    tushare_cfg = config["tushare"]
    data_cfg = config["data"]
    storage_cfg = config["storage"]

    if markets is None:
        markets = [k for k, v in data_cfg.get("markets", {}).items() if v.get("enabled")]

    # 初始化数据库
    init_db(storage_cfg["sqlite_path"])

    client = TushareClient(
        token=tushare_cfg["token"],
        timeout=tushare_cfg.get("timeout", 30),
        max_retries=tushare_cfg.get("max_retries", 3),
    )
    cache = RawCache(root=storage_cfg["raw_cache_root"])
    fetcher = MarketFetcher(client, cache)

    watchlist = load_watchlist(config)
    today = date.today()
    earliest_start = data_cfg.get("earliest_start_date", "2019-01-01")

    result = {
        "markets": markets,
        "fetched": [],
        "errors": [],
    }

    for market_key in markets:
        mkt = market_key.upper()
        codes = watchlist.get(mkt, [])

        for code in codes:
            try:
                if market_key == "hk":
                    fetcher.fetch_hk_daily(code, today, earliest_start)
                    fetcher.fetch_hk_income(code)
                    fetcher.fetch_hk_balancesheet(code)
                    fetcher.fetch_hk_cashflow(code)
                    fetcher.fetch_hk_fina_indicator(code)
                elif market_key == "us":
                    fetcher.fetch_us_daily(code, today, earliest_start)
                    fetcher.fetch_us_income(code)
                    fetcher.fetch_us_balancesheet(code)
                    fetcher.fetch_us_cashflow(code)
                elif market_key == "cn":
                    fetcher.fetch_cn_daily(code, today, earliest_start)
                    fetcher.fetch_cn_adj_factor(code, today, earliest_start)
                    fetcher.fetch_cn_daily_basic(code, today, earliest_start)
                    fetcher.fetch_cn_income(code)
                    fetcher.fetch_cn_balancesheet(code)
                    fetcher.fetch_cn_cashflow(code)
                    fetcher.fetch_cn_fina_indicator(code)

                result["fetched"].append(f"{market_key}/{code}")
                logger.info(f"Init done: {market_key}/{code}")
            except Exception as e:
                err = f"[{market_key}][{code}] {e}"
                logger.exception(err)
                result["errors"].append(err)

    return result
