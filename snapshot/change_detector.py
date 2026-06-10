"""
生成 ChangeEvent（对比上一日快照）
"""

import logging
from datetime import date
from typing import Any, Optional

import pandas as pd

from processing.calendar import to_date_str

logger = logging.getLogger("newstock.snapshot.change_detector")


def detect_changes(
    code: str,
    today_snapshot: dict,
    yesterday_snapshot: Optional[dict],
    pool_status_change: Optional[str] = None,
) -> list[dict]:
    """
    对比今日和昨日快照，生成变化事件列表。

    返回:
      list of {
        "code": str,
        "event_type": str,
        "event_level": "low" | "medium" | "high" | "critical",
        "event_desc": str,
        "attention_impact": int,
        "need_reanalysis": bool,
      }
    """
    events = []
    today_date = to_date_str(date.today())

    # ---- 池子变化 ----
    if pool_status_change == "new":
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "pool",
            "event_level": "medium",
            "event_desc": "新进入候选池",
            "attention_impact": 5,
            "need_reanalysis": True,
        })
    elif pool_status_change == "removed":
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "pool",
            "event_level": "high",
            "event_desc": "退出候选池",
            "attention_impact": 15,
            "need_reanalysis": False,
        })

    if yesterday_snapshot is None:
        return events

    # ---- 价格变化 ----
    old_ps = yesterday_snapshot.get("price_signal", {})
    new_ps = today_snapshot.get("price_signal", {})

    old_dd = old_ps.get("drawdown_from_high_pct") or 0
    new_dd = new_ps.get("drawdown_from_high_pct") or 0
    if new_dd - old_dd > 5:
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "price",
            "event_level": "medium",
            "event_desc": f"回撤扩大 {old_dd:.1f}% -> {new_dd:.1f}%",
            "attention_impact": 15,
            "need_reanalysis": True,
        })

    # 接近 52 周低点
    old_dist = old_ps.get("distance_from_low_pct")
    new_dist = new_ps.get("distance_from_low_pct")
    if new_dist is not None and new_dist <= 3 and (old_dist is None or old_dist > 3):
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "price",
            "event_level": "high",
            "event_desc": "价格接近 52 周低点",
            "attention_impact": 15,
            "need_reanalysis": True,
        })

    # ---- 估值变化 ----
    old_pe_ttm = yesterday_snapshot.get("valuation", {}).get("pe_ttm")
    new_pe_ttm = today_snapshot.get("valuation", {}).get("pe_ttm")
    if old_pe_ttm and new_pe_ttm and old_pe_ttm > 0:
        change_pct = abs((new_pe_ttm - old_pe_ttm) / old_pe_ttm * 100)
        if change_pct > 15:
            events.append({
                "code": code,
                "date": today_date,
                "event_type": "valuation",
                "event_level": "medium",
                "event_desc": f"PE TTM 变化 {change_pct:.1f}%: {old_pe_ttm:.1f} -> {new_pe_ttm:.1f}",
                "attention_impact": 10,
                "need_reanalysis": True,
            })

    # 估值分位明显下降
    old_pct = yesterday_snapshot.get("valuation", {}).get("pe_percentile_5y")
    new_pct = today_snapshot.get("valuation", {}).get("pe_percentile_5y")
    if old_pct is not None and new_pct is not None and old_pct > 25 and new_pct <= 20:
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "valuation",
            "event_level": "medium",
            "event_desc": f"PE 分位降至 {new_pct:.1f}%，进入历史低位",
            "attention_impact": 10,
            "need_reanalysis": True,
        })

    # ---- 风险变化 ----
    old_risk = set(yesterday_snapshot.get("risk", {}).get("risk_flags", []))
    new_risk = set(today_snapshot.get("risk", {}).get("risk_flags", []))
    new_flags = new_risk - old_risk
    if new_flags:
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "risk",
            "event_level": "high",
            "event_desc": f"新增风险标签: {', '.join(sorted(new_flags))}",
            "attention_impact": 30,
            "need_reanalysis": True,
        })

    # ---- 成交额明显放大 ----
    old_vol = old_ps.get("volume_ratio")
    new_vol = new_ps.get("volume_ratio")
    if old_vol is not None and new_vol is not None and new_vol >= 2.0 and old_vol < 2.0:
        events.append({
            "code": code,
            "date": today_date,
            "event_type": "capital_flow",
            "event_level": "medium",
            "event_desc": f"量比明显放大 {new_vol:.1f}",
            "attention_impact": 10,
            "need_reanalysis": False,
        })

    return events
