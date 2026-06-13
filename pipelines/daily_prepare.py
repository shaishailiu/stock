"""
每日预处理主流程（Longbridge 版）

Agent 启动前执行：
1. 读取配置和观察列表
2. 按市场执行增量数据更新
3. 清洗并标准化字段
4. 计算技术指标
5. 运行价格底部筛选
6. 生成 SignalCard、StockSnapshot、ChangeEvent
7. 更新 stock_pool_state
8. 写入 SQLite
9. 输出 prepare_summary
"""

import json
import logging
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import yaml

from storage.db import init_db, get_connection
from storage.repositories import (
    upsert_snapshot,
    upsert_signal_card,
    insert_change_events,
    upsert_pool_state,
    get_snapshot,
    get_fetch_state,
    mark_fetch_success,
    mark_fetch_failed,
)
from data_fetcher.longbridge_client import LongbridgeClient
from data_fetcher.longbridge_adapter import (
    to_longbridge_symbol,
    convert_us_symbol,
    convert_hk_symbol,
    convert_cn_symbol,
)
from data_fetcher.market_fetcher import MarketFetcher
from cache.raw_cache import RawCache
from processing.code_mapper import normalize_code
from processing.calendar import to_date_str
from processing.cleaner import clean_ohlc, detect_gaps, safe_float
from indicators.cycle_high import find_cycle_high
from indicators.technical import (
    calc_ma, calc_rsi, calc_weekly_rsi, calc_macd,
    calc_bollinger, calc_bias, calc_volume_ratio, calc_price_percentile,
)
from indicators.bottom_signal import compute_bottom_signal
from indicators.price_screen import run_price_screen
from snapshot.snapshot_builder import build_stock_snapshot
from snapshot.signal_builder import build_signal_card
from snapshot.change_detector import detect_changes

logger = logging.getLogger("newstock.pipelines.daily_prepare")


def load_config(config_path: str = "config/config.yaml") -> dict:
    """加载 YAML 配置"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_watchlist(config: dict):
    """
    加载多市场观察列表（从 config/watchlist.json）。
    符号已为 Longbridge 格式（AAPL.US / 700.HK / 600519.SH）。

    返回:
        (watchlist: dict, sector_map: dict)
        - watchlist: {"HK": [...], "US": [...], "CN": [...]}
        - sector_map: {"700.HK": "internet", "MSFT.US": "tech", ...}
    """
    wl = config.get("watchlist", {})
    file_path = wl.get("file", "")
    result = {"HK": [], "US": [], "CN": []}
    sector_map = {}

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        for s in stocks:
            if s.get("stock_type") != "cyclical":
                continue
            market = s.get("market", "")
            symbol = s.get("symbol", "")
            sector = s.get("sector", "")

            if market == "US":
                result["US"].append(symbol)
            elif market == "HK":
                result["HK"].append(symbol)
            elif market == "CN":
                result["CN"].append(symbol)
            # 忽略 crypto

            if symbol and sector:
                sector_map[symbol] = sector

    return result, sector_map


def run_daily_prepare(
    config_path: str = "config/config.yaml",
    target_date: Optional[str] = None,
) -> dict:
    """
    执行每日预处理流程。

    返回 prepare_summary
    """
    config = load_config(config_path)
    longbridge_cfg = config["longbridge"]
    data_cfg = config["data"]
    storage_cfg = config["storage"]
    indicator_cfg = config.get("indicators", {})
    bottom_cfg = config.get("bottom_signal", {})

    if target_date is None:
        target_date = to_date_str(date.today())
    today = date.fromisoformat(target_date)
    earliest_start = data_cfg.get("earliest_start_date", "2019-01-01")

    # 初始化数据库
    db_path = storage_cfg["sqlite_path"]
    init_db(db_path)
    conn = get_connection(db_path)

    # 计算上一个交易日（供缓存回填用）
    yesterday = _get_previous_trade_date(target_date)
    yesterday_date = date.fromisoformat(yesterday) if yesterday else today

    # 盘中保护：如果目标市场尚未收盘，数据拉取退回到上一交易日
    eff_today_hk = today if _is_market_closed_today("hk") else yesterday_date
    eff_today_us = today if _is_market_closed_today("us") else yesterday_date
    eff_today_cn = today if _is_market_closed_today("cn") else yesterday_date

    if eff_today_hk != today:
        logger.info(f"HK market not yet closed, using {eff_today_hk} as effective data date")
    if eff_today_us != today:
        logger.info(f"US market not yet closed, using {eff_today_us} as effective data date")
    if eff_today_cn != today:
        logger.info(f"CN market not yet closed, using {eff_today_cn} as effective data date")

    # 初始化客户端（Longbridge CLI）
    client = LongbridgeClient(
        timeout=longbridge_cfg.get("timeout", 30),
        rate_limit_per_second=longbridge_cfg.get("rate_limit_per_second", 10),
    )
    cache = RawCache(root=storage_cfg["raw_cache_root"])
    fetcher = MarketFetcher(client, cache)

    watchlist, sector_map = load_watchlist(config)
    enabled_markets = {k for k, v in data_cfg.get("markets", {}).items() if v.get("enabled")}

    summary = {
        "data_date": target_date,
        "markets": sorted(enabled_markets),
        "updated_symbols": 0,
        "new_candidates": 0,
        "existing_candidates": 0,
        "risk_alerts": 0,
        "data_missing_count": 0,
        "errors": [],
    }

    all_stock_results = []

    market_processor = {
        "hk": lambda code: _process_hk_stock(code, fetcher, eff_today_hk, earliest_start, indicator_cfg, bottom_cfg, target_date, conn, yesterday),
        "us": lambda code: _process_us_stock(code, fetcher, eff_today_us, earliest_start, indicator_cfg, bottom_cfg, target_date, conn, yesterday),
        "cn": lambda code: _process_cn_stock(code, fetcher, eff_today_cn, earliest_start, indicator_cfg, bottom_cfg, target_date, conn, yesterday),
    }

    for market_key, enabled in data_cfg.get("markets", {}).items():
        mkt = market_key.upper()
        if not enabled.get("enabled", True):
            continue
        codes = watchlist.get(mkt, [])
        processor = market_processor.get(market_key)
        if processor is None:
            continue

        for code in codes:
            try:
                result = processor(code)
                if result:
                    all_stock_results.append(result)
                    summary["updated_symbols"] += 1
            except Exception as e:
                err_msg = f"[{mkt}][{code}] {e}"
                logger.exception(err_msg)
                summary["errors"].append(err_msg)

    # ---- 底部筛选 ----
    scored_stocks = run_price_screen(
        all_stock_results,
        min_drawdown_pct=bottom_cfg.get("drawdown_threshold", 20),
        min_bottom_signal=15,
    )

    # ---- 获取昨日快照做变化检测 ----
    for stock in scored_stocks:
        code = stock["code"]
        name = stock.get("name")
        market = stock.get("market")

        # 昨日快照
        yesterday_snap = get_snapshot(conn, code, yesterday)

        # 池状态
        old_pool = conn.execute(
            "SELECT pool_status, days_in_pool FROM stock_pool_state WHERE date = ? AND code = ?",
            (yesterday, code),
        ).fetchone()

        if stock["passed_price_screen"]:
            if old_pool is None or old_pool["pool_status"] in ("removed", "risk_alert"):
                pool_status = "new"
                days_in_pool = 1
                first_seen = target_date
            else:
                pool_status = "existing"
                days_in_pool = (old_pool["days_in_pool"] or 0) + 1
                first_seen = old_pool.get("first_seen_date", target_date)
        else:
            pool_status = "removed"
            days_in_pool = old_pool["days_in_pool"] if old_pool else 0
            first_seen = old_pool.get("first_seen_date", target_date) if old_pool else target_date

        # 变化检测
        pool_change = None
        if old_pool:
            old_status = old_pool["pool_status"]
            if old_status != pool_status:
                pool_change = pool_status
        elif pool_status == "new":
            pool_change = "new"

        changes = detect_changes(code, stock, yesterday_snap, pool_change)
        insert_change_events(conn, changes)

        # 写入池状态
        upsert_pool_state(conn, target_date, {
            "code": code,
            "pool_status": pool_status,
            "first_seen_date": first_seen,
            "last_seen_date": target_date,
            "days_in_pool": days_in_pool,
        })

        # 写入快照
        # 用 watchlist 的 sector 覆盖 API 返回的 industry
        if code in sector_map:
            stock["industry"] = sector_map[code]
        upsert_snapshot(conn, target_date, stock)

        # 写入信号卡
        signal_card = stock.get("signal_card", {})
        if signal_card:
            upsert_signal_card(conn, target_date, signal_card)

        if pool_status == "new":
            summary["new_candidates"] += 1
        elif pool_status == "existing":
            summary["existing_candidates"] += 1
        if stock.get("risk", {}).get("risk_flags"):
            summary["risk_alerts"] += 1

    conn.close()
    logger.info(f"Daily prepare completed: {json.dumps(summary, ensure_ascii=False)}")
    return summary


def _process_hk_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date, conn, yesterday):
    """处理单只港股（含 API 调用频次优化）"""
    daily = fetcher.fetch_hk_daily(code, today, earliest_start)

    if daily.empty:
        return None

    # 估值指标（每天必须：PE/PB 随股价变化）
    valuation_df = fetcher.fetch_hk_daily_basic(code, today, earliest_start)

    # 静态信息（30 天刷新一次；降级时从昨天快照回填）
    extra = _get_static_info_cached(fetcher, conn, code, "HK", yesterday, target_date)

    # 技术指标
    bottom = compute_bottom_signal(
        daily, price_col="close",
        drawdown_threshold=bottom_cfg.get("drawdown_threshold", 20),
        rsi_oversold=bottom_cfg.get("rsi_oversold", 30),
        rsi_weekly_oversold=bottom_cfg.get("rsi_weekly_oversold", 35),
        bias_120_threshold=bottom_cfg.get("bias_120_threshold", -15),
    )

    snapshot = build_stock_snapshot(code, daily, valuation_df=valuation_df, extra=extra)
    snapshot["price_signal"]["alert_level"] = bottom["alert_level"]
    snapshot["price_signal"]["bottom_signal_score"] = bottom["bottom_signal_score"]

    # PE 分位 + 行业 PE/PB 中位数（7 天刷新一次；降级时从昨天快照回填）
    _attach_valuation_analysis_cached(fetcher, conn, code, "HK", snapshot, yesterday, target_date)

    # 财务数据（季度刷新：parquet 缓存中最大 end_date 距今 > 100 天才调 API）
    if fetcher.is_financial_stale("hk", "income", code, max_age_days=100):
        try:
            income = fetcher.fetch_hk_income(code)
            fina = fetcher.fetch_hk_fina_indicator(code)
            hold = fetcher.fetch_hk_hold(code, today, earliest_start)

            snapshot["fundamental"] = _extract_hk_fundamental(income, fina)
            if not hold.empty:
                latest_hold = hold.iloc[-1]
                snapshot["capital_flow"] = {
                    "southbound_hold_ratio": safe_float(latest_hold.get("ratio")),
                }
        except Exception as e:
            logger.warning(f"HK financial fetch failed for {code}: {e}")
            _load_hk_financial_from_cache(fetcher, code, snapshot)

        try:
            balance_df = fetcher.fetch_hk_balancesheet(code)
            snapshot["balance_sheet"] = _extract_balance_sheet(balance_df)
        except Exception as e:
            logger.warning(f"HK balance sheet fetch failed for {code}: {e}")

        try:
            cashflow_df = fetcher.fetch_hk_cashflow(code)
            snapshot["cashflow"] = _extract_cashflow(cashflow_df)
        except Exception as e:
            logger.warning(f"HK cashflow fetch failed for {code}: {e}")
    else:
        # 财务数据未过期，从 parquet 缓存直接加载
        _load_hk_financial_from_cache(fetcher, code, snapshot)

    signal_card = build_signal_card(code, daily)
    result = {**snapshot, "signal_card": signal_card, "bottom_signal": bottom}
    return result


def _process_us_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date, conn, yesterday):
    """处理单只美股（含 API 调用频次优化）"""
    daily = fetcher.fetch_us_daily(code, today, earliest_start)

    if daily.empty:
        return None

    # 估值指标
    valuation_df = fetcher.fetch_us_daily_basic(code, today, earliest_start)

    # 静态信息（30 天刷新一次）
    extra = _get_static_info_cached(fetcher, conn, code, "US", yesterday, target_date)

    bottom = compute_bottom_signal(
        daily, price_col="close",
        drawdown_threshold=bottom_cfg.get("drawdown_threshold", 20),
        rsi_oversold=bottom_cfg.get("rsi_oversold", 30),
        rsi_weekly_oversold=bottom_cfg.get("rsi_weekly_oversold", 35),
        bias_120_threshold=bottom_cfg.get("bias_120_threshold", -15),
    )

    snapshot = build_stock_snapshot(code, daily, valuation_df=valuation_df, extra=extra)
    snapshot["price_signal"]["alert_level"] = bottom["alert_level"]
    snapshot["price_signal"]["bottom_signal_score"] = bottom["bottom_signal_score"]

    # PE 分位 + 行业 PE/PB 中位数（7 天刷新一次）
    _attach_valuation_analysis_cached(fetcher, conn, code, "US", snapshot, yesterday, target_date)

    # 财务数据（季度刷新）
    if fetcher.is_financial_stale("us", "income", code, max_age_days=100):
        try:
            income = fetcher.fetch_us_income(code)
            fina = fetcher.fetch_us_fina_indicator(code)
            snapshot["fundamental"] = _extract_us_fundamental(income, fina)
        except Exception as e:
            logger.warning(f"US financial fetch failed for {code}: {e}")
            _load_us_financial_from_cache(fetcher, code, snapshot)

        try:
            balance_df = fetcher.fetch_us_balancesheet(code)
            snapshot["balance_sheet"] = _extract_balance_sheet(balance_df)
        except Exception as e:
            logger.warning(f"US balance sheet fetch failed for {code}: {e}")

        try:
            cashflow_df = fetcher.fetch_us_cashflow(code)
            snapshot["cashflow"] = _extract_cashflow(cashflow_df)
        except Exception as e:
            logger.warning(f"US cashflow fetch failed for {code}: {e}")
    else:
        _load_us_financial_from_cache(fetcher, code, snapshot)

    signal_card = build_signal_card(code, daily)
    result = {**snapshot, "signal_card": signal_card, "bottom_signal": bottom}
    return result


def _process_cn_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date, conn, yesterday):
    """处理单只 A 股（含 API 调用频次优化）"""
    daily = fetcher.fetch_cn_daily(code, today, earliest_start)
    daily_basic = fetcher.fetch_cn_daily_basic(code, today, earliest_start)

    if daily.empty:
        return None

    # 静态信息（30 天刷新一次）
    extra = _get_static_info_cached(fetcher, conn, code, "CN", yesterday, target_date)

    bottom = compute_bottom_signal(
        daily, price_col="close",
        drawdown_threshold=bottom_cfg.get("drawdown_threshold", 20),
        rsi_oversold=bottom_cfg.get("rsi_oversold", 30),
        rsi_weekly_oversold=bottom_cfg.get("rsi_weekly_oversold", 35),
        bias_120_threshold=bottom_cfg.get("bias_120_threshold", -15),
    )

    snapshot = build_stock_snapshot(code, daily, valuation_df=daily_basic, extra=extra)
    snapshot["price_signal"]["alert_level"] = bottom["alert_level"]
    snapshot["price_signal"]["bottom_signal_score"] = bottom["bottom_signal_score"]

    # PE 分位 + 行业 PE/PB 中位数（7 天刷新一次）
    _attach_valuation_analysis_cached(fetcher, conn, code, "CN", snapshot, yesterday, target_date)

    # 财务数据（季度刷新）
    if fetcher.is_financial_stale("cn", "income", code, max_age_days=100):
        try:
            income = fetcher.fetch_cn_income(code)
            fina = fetcher.fetch_cn_fina_indicator(code)
            snapshot["fundamental"] = _extract_cn_fundamental(income, fina)
        except Exception as e:
            logger.warning(f"CN financial fetch failed for {code}: {e}")
            _load_cn_financial_from_cache(fetcher, code, snapshot)

        try:
            balance_df = fetcher.fetch_cn_balancesheet(code)
            snapshot["balance_sheet"] = _extract_balance_sheet(balance_df)
        except Exception as e:
            logger.warning(f"CN balance sheet fetch failed for {code}: {e}")

        try:
            cashflow_df = fetcher.fetch_cn_cashflow(code)
            snapshot["cashflow"] = _extract_cashflow(cashflow_df)
        except Exception as e:
            logger.warning(f"CN cashflow fetch failed for {code}: {e}")
    else:
        _load_cn_financial_from_cache(fetcher, code, snapshot)

    # 风险数据（Longbridge 不支持 A 股特有风险数据，返回空标记）
    try:
        risk = _extract_cn_risk(fetcher, code, today, earliest_start)
        snapshot["risk"] = risk
    except Exception as e:
        logger.warning(f"CN risk fetch failed for {code}: {e}")

    signal_card = build_signal_card(code, daily)
    result = {**snapshot, "signal_card": signal_card, "bottom_signal": bottom}
    return result


def _extract_hk_fundamental(income: pd.DataFrame, fina: pd.DataFrame) -> dict:
    """提取港股财务摘要"""
    result = {}
    # 从财务指标接口提取
    if not fina.empty:
        latest = fina.iloc[-1]
        for field in ["roe", "roa", "grossprofit_margin", "netprofit_margin",
                       "debt_to_assets", "current_ratio", "quick_ratio",
                       "revenue_yoy", "profit_yoy"]:
            result[field] = safe_float(latest.get(field))
    # 从利润表提取 TTM + 毛利率
    if not income.empty:
        income_sorted = income.sort_values("end_date") if "end_date" in income.columns else income
        recent_4q = income_sorted.tail(4) if len(income_sorted) >= 4 else income_sorted
        if "revenue" in recent_4q.columns:
            result["revenue_ttm"] = safe_float(recent_4q["revenue"].sum())
        if "n_income" in recent_4q.columns:
            result["net_profit_ttm"] = safe_float(recent_4q["n_income"].sum())
        # 毛利率来自利润表 gross_profit 列（从 GrossMgn 映射，是比率非金额）
        if "gross_profit" in income_sorted.columns:
            result["gross_margin"] = safe_float(income_sorted["gross_profit"].iloc[-1])
    return result


def _extract_cn_fundamental(income: pd.DataFrame, fina: pd.DataFrame) -> dict:
    """提取 A 股财务摘要"""
    result = {}
    if not fina.empty:
        latest = fina.iloc[-1]
        for field in ["roe", "roe_waa", "roa", "grossprofit_margin", "netprofit_margin",
                       "debt_to_assets", "current_ratio", "quick_ratio",
                       "revenue_yoy", "profit_yoy"]:
            result[field] = safe_float(latest.get(field))
    if not income.empty:
        income_sorted = income.sort_values("end_date") if "end_date" in income.columns else income
        recent_4q = income_sorted.tail(4) if len(income_sorted) >= 4 else income_sorted
        if "revenue" in recent_4q.columns:
            result["revenue_ttm"] = safe_float(recent_4q["revenue"].sum())
        if "n_income" in recent_4q.columns:
            result["net_profit_ttm"] = safe_float(recent_4q["n_income"].sum())
        if "gross_profit" in income_sorted.columns:
            result["gross_margin"] = safe_float(income_sorted["gross_profit"].iloc[-1])
    return result


def _extract_us_fundamental(income: pd.DataFrame, fina: pd.DataFrame) -> dict:
    """提取美股财务摘要"""
    result = {}
    if not fina.empty:
        latest = fina.iloc[-1]
        for field in ["roe", "roa", "grossprofit_margin", "netprofit_margin",
                       "debt_to_assets", "current_ratio", "quick_ratio",
                       "revenue_yoy", "profit_yoy"]:
            result[field] = safe_float(latest.get(field))
    if not income.empty:
        income_sorted = income.sort_values("end_date") if "end_date" in income.columns else income
        recent_4q = income_sorted.tail(4) if len(income_sorted) >= 4 else income_sorted
        if "revenue" in recent_4q.columns:
            result["revenue_ttm"] = safe_float(recent_4q["revenue"].sum())
        if "n_income" in recent_4q.columns:
            result["net_profit_ttm"] = safe_float(recent_4q["n_income"].sum())
        if "gross_profit" in income_sorted.columns:
            result["gross_margin"] = safe_float(income_sorted["gross_profit"].iloc[-1])
    return result


def _extract_balance_sheet(balance_df: pd.DataFrame) -> dict:
    """提取资产负债表摘要"""
    if balance_df is None or balance_df.empty:
        return {}
    latest = balance_df.iloc[-1]
    result = {}
    for field in ["total_assets", "total_liabs", "total_hldr_eqy_exc_min_int",
                   "total_cur_assets", "total_cur_liab"]:
        val = latest.get(field)
        if val is not None:
            result[field] = safe_float(val)
    return result


def _extract_cashflow(cashflow_df: pd.DataFrame) -> dict:
    """提取现金流量表摘要"""
    if cashflow_df is None or cashflow_df.empty:
        return {}
    latest = cashflow_df.iloc[-1]
    result = {}
    for field in ["n_cashflow_act", "n_cashflow_inv_act", "n_cashflow_fin_act",
                   "free_cashflow"]:
        val = latest.get(field)
        if val is not None:
            result[field] = safe_float(val)
    return result


def _get_static_info(fetcher, code: str, market: str) -> dict:
    """获取股票静态信息（名称、行业等）"""
    try:
        info = fetcher.fetch_static_info(code, market)
        if not info:
            return {}
        return {
            "name": info.get("name_cn") or info.get("name") or info.get("symbol"),
            "industry": info.get("industry") or info.get("industry_gics"),
        }
    except Exception:
        return {}


def _attach_valuation_analysis(fetcher, code: str, market: str, snapshot: dict) -> None:
    """一次性获取 PE 分位 + PE/PB 行业中位数，合并冗余 API 调用"""
    try:
        result = fetcher.fetch_valuation_analysis(code, market)
        if result:
            if "pe_percentile_5y" in result:
                snapshot["valuation"]["pe_percentile_5y"] = result["pe_percentile_5y"]
            if "industry_pe_median" in result:
                snapshot["valuation"]["industry_pe_median"] = result["industry_pe_median"]
            if "industry_pb_median" in result:
                snapshot["valuation"]["industry_pb_median"] = result["industry_pb_median"]
    except Exception:
        pass


def _extract_cn_risk(fetcher, code: str, today, earliest_start: str) -> dict:
    """
    提取 A 股风险数据。
    Longbridge CLI 不支持 A 股特有风险数据（质押、审计、ST 等），
    返回 data_missing 标记。
    """
    risk = {
        "risk_flags": ["data_missing: risk APIs not available via Longbridge CLI"],
        "data_missing": True,
    }
    return risk


def _is_market_closed_today(market: str) -> bool:
    """
    判断目标市场今天是否已收盘（基于当前 UTC 时间近似）。

    各市场常规收盘时间转 UTC 的小时近似:
      - HK: 16:00 HKT = 08:00 UTC
      - CN: 15:00 CST = 07:00 UTC
      - US: 16:00 EDT = 20:00 UTC (夏季，保守取 21:00)
    """
    now_utc = datetime.now(timezone.utc)
    close_hour_utc = {
        "hk": 8,
        "cn": 7,
        "us": 21,
    }
    hour = close_hour_utc.get(market.lower(), 0)
    return now_utc.hour >= hour


def _get_previous_trade_date(current_date_str: str) -> Optional[str]:
    """获取上一交易日（简单实现）"""
    dt = datetime.strptime(current_date_str, "%Y-%m-%d")
    for offset in range(1, 5):
        prev = dt - timedelta(days=offset)
        if prev.weekday() < 5:
            return prev.strftime("%Y-%m-%d")
    return None


# ── API 刷新频次控制 ──

def _should_skip_api(conn, market: str, api_name: str, code: str, max_age_days: int) -> bool:
    """通过 fetch_state 表判断 API 是否在 max_age_days 内调用过"""
    state = get_fetch_state(conn, market, api_name, code)
    if not state:
        return False
    last_fetch = state.get("last_fetch_at", "")
    if not last_fetch:
        return False
    try:
        # SQLite datetime 格式兼容: "2025-01-15 09:30:00" 或 ISO "2025-01-15T09:30:00"
        last_fetch_clean = last_fetch.replace("T", " ").split(".")[0]
        last_dt = datetime.strptime(last_fetch_clean, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - last_dt).days < max_age_days
    except (ValueError, TypeError):
        return False


def _get_static_info_cached(
    fetcher, conn, code: str, market: str, yesterday, target_date: str
) -> dict:
    """获取静态信息（30 天刷新一次；降级时从昨天快照回填）"""
    if _should_skip_api(conn, market, "static_info", code, max_age_days=30):
        snap = get_snapshot(conn, code, yesterday)
        if snap and snap.get("name"):
            return {"name": snap["name"], "industry": snap.get("industry")}
    # 需要刷新
    result = _get_static_info(fetcher, code, market)
    if result:
        mark_fetch_success(
            conn, market, "static_info", code, last_success_date=target_date
        )
    return result


def _attach_valuation_analysis_cached(
    fetcher, conn, code: str, market: str, snapshot: dict, yesterday, target_date: str
) -> None:
    """PE 分位 + 行业中位数（7 天刷新一次；降级时从昨天快照回填）"""
    if _should_skip_api(conn, market, "valuation_analysis", code, max_age_days=7):
        snap = get_snapshot(conn, code, yesterday)
        if snap:
            val = snapshot.get("valuation", {})
            if snap.get("pe_percentile_5y") is not None:
                val["pe_percentile_5y"] = snap["pe_percentile_5y"]
            if snap.get("industry_pe_median") is not None:
                val["industry_pe_median"] = snap["industry_pe_median"]
            if snap.get("industry_pb_median") is not None:
                val["industry_pb_median"] = snap["industry_pb_median"]
            return
    # 需要刷新
    _attach_valuation_analysis(fetcher, code, market, snapshot)
    mark_fetch_success(
        conn, market, "valuation_analysis", code, last_success_date=target_date
    )


# ── 财务数据缓存加载（API 未过期 / 降级路径）──

def _load_hk_financial_from_cache(fetcher, code: str, snapshot: dict) -> None:
    """从 parquet 缓存加载港股财务数据"""
    try:
        income = fetcher.cache.load("hk", "income", code)
        fina = fetcher.cache.load("hk", "fina_indicator", code)
        if not income.empty or not fina.empty:
            snapshot["fundamental"] = _extract_hk_fundamental(income, fina)
    except Exception as e:
        logger.warning(f"HK financial cache load failed for {code}: {e}")
    try:
        balance_df = fetcher.cache.load("hk", "balancesheet", code)
        if not balance_df.empty:
            snapshot["balance_sheet"] = _extract_balance_sheet(balance_df)
    except Exception as e:
        logger.warning(f"HK balance cache load failed for {code}: {e}")
    try:
        cashflow_df = fetcher.cache.load("hk", "cashflow", code)
        if not cashflow_df.empty:
            snapshot["cashflow"] = _extract_cashflow(cashflow_df)
    except Exception as e:
        logger.warning(f"HK cashflow cache load failed for {code}: {e}")


def _load_us_financial_from_cache(fetcher, code: str, snapshot: dict) -> None:
    """从 parquet 缓存加载美股财务数据"""
    try:
        income = fetcher.cache.load("us", "income", code)
        fina = fetcher.cache.load("us", "fina_indicator", code)
        if not income.empty or not fina.empty:
            snapshot["fundamental"] = _extract_us_fundamental(income, fina)
    except Exception as e:
        logger.warning(f"US financial cache load failed for {code}: {e}")
    try:
        balance_df = fetcher.cache.load("us", "balancesheet", code)
        if not balance_df.empty:
            snapshot["balance_sheet"] = _extract_balance_sheet(balance_df)
    except Exception as e:
        logger.warning(f"US balance cache load failed for {code}: {e}")
    try:
        cashflow_df = fetcher.cache.load("us", "cashflow", code)
        if not cashflow_df.empty:
            snapshot["cashflow"] = _extract_cashflow(cashflow_df)
    except Exception as e:
        logger.warning(f"US cashflow cache load failed for {code}: {e}")


def _load_cn_financial_from_cache(fetcher, code: str, snapshot: dict) -> None:
    """从 parquet 缓存加载 A 股财务数据"""
    try:
        income = fetcher.cache.load("cn", "income", code)
        fina = fetcher.cache.load("cn", "fina_indicator", code)
        if not income.empty or not fina.empty:
            snapshot["fundamental"] = _extract_cn_fundamental(income, fina)
    except Exception as e:
        logger.warning(f"CN financial cache load failed for {code}: {e}")
    try:
        balance_df = fetcher.cache.load("cn", "balancesheet", code)
        if not balance_df.empty:
            snapshot["balance_sheet"] = _extract_balance_sheet(balance_df)
    except Exception as e:
        logger.warning(f"CN balance cache load failed for {code}: {e}")
    try:
        cashflow_df = fetcher.cache.load("cn", "cashflow", code)
        if not cashflow_df.empty:
            snapshot["cashflow"] = _extract_cashflow(cashflow_df)
    except Exception as e:
        logger.warning(f"CN cashflow cache load failed for {code}: {e}")
