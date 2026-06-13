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
from datetime import date, datetime
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


def load_watchlist(config: dict) -> dict:
    """
    加载多市场观察列表（从 config/watchlist.json）。
    符号已为 Longbridge 格式（AAPL.US / 700.HK / 600519.SH）。
    """
    wl = config.get("watchlist", {})
    file_path = wl.get("file", "")
    result = {"HK": [], "US": [], "CN": []}

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stocks = data.get("stocks", [])
        for s in stocks:
            market = s.get("market", "")
            symbol = s.get("symbol", "")

            if market == "US":
                result["US"].append(symbol)
            elif market == "HK":
                result["HK"].append(symbol)
            elif market == "CN":
                result["CN"].append(symbol)
            # 忽略 crypto

    return result


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

    # 初始化客户端（Longbridge CLI）
    client = LongbridgeClient(
        timeout=longbridge_cfg.get("timeout", 30),
        rate_limit_per_second=longbridge_cfg.get("rate_limit_per_second", 10),
    )
    cache = RawCache(root=storage_cfg["raw_cache_root"])
    fetcher = MarketFetcher(client, cache)

    watchlist = load_watchlist(config)
    enabled_markets = {k for k, v in data_cfg.get("markets", {}).items() if v.get("enabled")}

    conn = get_connection(db_path)

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
        "hk": lambda code: _process_hk_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date),
        "us": lambda code: _process_us_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date),
        "cn": lambda code: _process_cn_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date),
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
    yesterday = _get_previous_trade_date(target_date)
    conn = get_connection(db_path)

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


def _process_hk_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date):
    """处理单只港股"""
    daily = fetcher.fetch_hk_daily(code, today, earliest_start)

    if daily.empty:
        return None

    # 估值指标
    valuation_df = fetcher.fetch_hk_daily_basic(code, today, earliest_start)

    # 静态信息
    extra = _get_static_info(fetcher, code, "HK")

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

    # PE 分位
    _attach_pe_percentile(fetcher, code, "HK", snapshot)

    # 财务数据
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

    # 资产负债表 & 现金流
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

    signal_card = build_signal_card(code, daily)
    result = {**snapshot, "signal_card": signal_card, "bottom_signal": bottom}
    return result


def _process_us_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date):
    """处理单只美股"""
    daily = fetcher.fetch_us_daily(code, today, earliest_start)

    if daily.empty:
        return None

    # 估值指标
    valuation_df = fetcher.fetch_us_daily_basic(code, today, earliest_start)

    # 静态信息
    extra = _get_static_info(fetcher, code, "US")

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

    # PE 分位
    _attach_pe_percentile(fetcher, code, "US", snapshot)

    # 财务数据
    try:
        income = fetcher.fetch_us_income(code)
        fina = fetcher.fetch_us_fina_indicator(code)
        snapshot["fundamental"] = _extract_us_fundamental(income, fina)
    except Exception as e:
        logger.warning(f"US financial fetch failed for {code}: {e}")

    # 资产负债表 & 现金流
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

    signal_card = build_signal_card(code, daily)
    result = {**snapshot, "signal_card": signal_card, "bottom_signal": bottom}
    return result


def _process_cn_stock(code, fetcher, today, earliest_start, indicator_cfg, bottom_cfg, target_date):
    """处理单只 A 股"""
    daily = fetcher.fetch_cn_daily(code, today, earliest_start)
    daily_basic = fetcher.fetch_cn_daily_basic(code, today, earliest_start)

    if daily.empty:
        return None

    # 静态信息
    extra = _get_static_info(fetcher, code, "CN")

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

    # PE 分位
    _attach_pe_percentile(fetcher, code, "CN", snapshot)

    # 财务数据
    try:
        income = fetcher.fetch_cn_income(code)
        fina = fetcher.fetch_cn_fina_indicator(code)

        snapshot["fundamental"] = _extract_cn_fundamental(income, fina)
    except Exception as e:
        logger.warning(f"CN financial fetch failed for {code}: {e}")

    # 资产负债表 & 现金流
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
                       "debt_to_assets", "current_ratio", "quick_ratio"]:
            result[field] = safe_float(latest.get(field))
    # 从利润表提取 TTM
    if not income.empty:
        income_sorted = income.sort_values("end_date") if "end_date" in income.columns else income
        recent_4q = income_sorted.tail(4) if len(income_sorted) >= 4 else income_sorted
        if "revenue" in recent_4q.columns:
            result["revenue_ttm"] = safe_float(recent_4q["revenue"].sum())
        if "n_income" in recent_4q.columns:
            result["net_profit_ttm"] = safe_float(recent_4q["n_income"].sum())
    return result


def _extract_cn_fundamental(income: pd.DataFrame, fina: pd.DataFrame) -> dict:
    """提取 A 股财务摘要"""
    result = {}
    if not fina.empty:
        latest = fina.iloc[-1]
        for field in ["roe", "roe_waa", "roa", "grossprofit_margin", "netprofit_margin",
                       "debt_to_assets", "current_ratio", "quick_ratio",
                       "or_yoy", "netprofit_yoy", "basic_eps_yoy"]:
            result[field] = safe_float(latest.get(field))
    if not income.empty:
        income_sorted = income.sort_values("end_date") if "end_date" in income.columns else income
        recent_4q = income_sorted.tail(4) if len(income_sorted) >= 4 else income_sorted
        if "revenue" in recent_4q.columns:
            result["revenue_ttm"] = safe_float(recent_4q["revenue"].sum())
        if "n_income" in recent_4q.columns:
            result["net_profit_ttm"] = safe_float(recent_4q["n_income"].sum())
    return result


def _extract_us_fundamental(income: pd.DataFrame, fina: pd.DataFrame) -> dict:
    """提取美股财务摘要"""
    result = {}
    if not fina.empty:
        latest = fina.iloc[-1]
        for field in ["roe", "roa", "grossprofit_margin", "netprofit_margin",
                       "debt_to_assets", "current_ratio", "quick_ratio"]:
            result[field] = safe_float(latest.get(field))
    if not income.empty:
        income_sorted = income.sort_values("end_date") if "end_date" in income.columns else income
        recent_4q = income_sorted.tail(4) if len(income_sorted) >= 4 else income_sorted
        if "revenue" in recent_4q.columns:
            result["revenue_ttm"] = safe_float(recent_4q["revenue"].sum())
        if "n_income" in recent_4q.columns:
            result["net_profit_ttm"] = safe_float(recent_4q["n_income"].sum())
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
            "name": info.get("name") or info.get("name_cn") or info.get("symbol"),
            "industry": info.get("industry") or info.get("industry_gics"),
        }
    except Exception:
        return {}


def _attach_pe_percentile(fetcher, code: str, market: str, snapshot: dict) -> None:
    """计算 PE 5 年分位并写入 snapshot["valuation"]"""
    try:
        pe_pct = fetcher.fetch_pe_percentile(code, market)
        if pe_pct is not None:
            snapshot["valuation"]["pe_percentile_5y"] = pe_pct
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


def _get_previous_trade_date(current_date_str: str) -> Optional[str]:
    """获取上一交易日（简单实现）"""
    dt = datetime.strptime(current_date_str, "%Y-%m-%d")
    from datetime import timedelta
    for offset in range(1, 5):
        prev = dt - timedelta(days=offset)
        if prev.weekday() < 5:
            return prev.strftime("%Y-%m-%d")
    return None
