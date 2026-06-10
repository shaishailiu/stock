"""
股票代码标准化：港股/美股/A 股
"""

import logging
from typing import Optional

logger = logging.getLogger("newstock.processing.code_mapper")

# 标准后缀到市场映射
SUFFIX_MAP = {
    ".HK": "HK",
    ".US": "US",
    ".SH": "CN",
    ".SZ": "CN",
    ".BJ": "CN",
}


def normalize_code(raw_code: str) -> dict:
    """
    将 Tushare 原始代码标准化。

    返回:
      {
        "code": "00700.HK",       # 系统标准代码
        "raw_ts_code": "00700.HK",# 原始代码
        "market": "HK",           # HK / US / CN
        "exchange": "HKEX",       # 交易所简称
        "currency": "HKD",        # 默认币种
      }
    """
    raw_code = raw_code.strip().upper()

    # 根据后缀推断市场
    market = "CN"
    for suffix, mkt in SUFFIX_MAP.items():
        if raw_code.endswith(suffix):
            market = mkt
            break

    # 交易所和币种映射
    exchange = _get_exchange(raw_code, market)
    currency = _get_currency(market)

    result = {
        "code": raw_code,
        "raw_ts_code": raw_code,
        "market": market,
        "exchange": exchange,
        "currency": currency,
    }

    logger.debug(f"Normalized {raw_code} -> {result['code']} ({result['market']})")
    return result


def _get_exchange(code: str, market: str) -> str:
    """推断交易所"""
    if market == "HK":
        return "HKEX"
    if market == "US":
        return "NYSE"  # 默认，实际可能 NASDAQ
    if code.endswith(".SH"):
        return "SHSE"
    if code.endswith(".SZ"):
        return "SZSE"
    if code.endswith(".BJ"):
        return "BJSE"
    return "UNKNOWN"


def _get_currency(market: str) -> str:
    """获取默认币种"""
    return {"HK": "HKD", "US": "USD", "CN": "CNY"}.get(market, "CNY")


def get_market_from_code(code: str) -> Optional[str]:
    """从代码推断市场"""
    code = code.strip().upper()
    for suffix, mkt in SUFFIX_MAP.items():
        if code.endswith(suffix):
            return mkt
    return None
