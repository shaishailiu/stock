"""
日期处理工具
"""

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd


def to_date_str(d: str | date | datetime) -> str:
    """将日期转为 YYYY-MM-DD 字符串"""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.isoformat()
    if isinstance(d, str):
        # 尝试多种格式
        d = d.strip().replace("-", "").replace("/", "")
        if len(d) == 8:
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if len(d) == 10:
            return d
    return str(d)


def to_tushare_date(d: str | date | datetime) -> str:
    """将日期转为 Tushare 格式 YYYYMMDD"""
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    if isinstance(d, str):
        d = d.strip().replace("-", "").replace("/", "")
        if len(d) >= 8:
            return d[:8]
    return str(d)


def get_prev_n_quarters(report_period: str, n: int = 4) -> list[str]:
    """获取最近 n 个报告期"""
    periods = []
    try:
        dt = datetime.strptime(report_period[:8], "%Y%m%d")
    except ValueError:
        return periods
    for i in range(n):
        periods.append(dt.strftime("%Y%m%d"))
        # 回退一个季度
        if dt.month <= 3:
            dt = dt.replace(year=dt.year - 1, month=12, day=31)
        elif dt.month <= 6:
            dt = dt.replace(month=3, day=31)
        elif dt.month <= 9:
            dt = dt.replace(month=6, day=30)
        else:
            dt = dt.replace(month=9, day=30)
    return periods


def is_same_period(a: str, b: str) -> bool:
    """判断两个报告期是否同一时期（同比）"""
    a_clean = a.strip().replace("-", "")[:8]
    b_clean = b.strip().replace("-", "")[:8]
    if len(a_clean) < 8 or len(b_clean) < 8:
        return False
    return a_clean[4:8] == b_clean[4:8]


def get_yoy_period(report_period: str) -> Optional[str]:
    """获取去年同期报告期"""
    try:
        dt = datetime.strptime(report_period[:8], "%Y%m%d")
    except ValueError:
        return None
    return dt.replace(year=dt.year - 1).strftime("%Y%m%d")
