"""
Agent 工具：候选池查询
"""

import logging
import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from storage.repositories import get_candidate_pool, get_snapshot

logger = logging.getLogger("newstock.agent_tools.candidate")


def get_candidate_pool_tool(conn: sqlite3.Connection, data_date: Optional[str] = None) -> dict:
    """
    返回候选池、老池、退出池、风险池。

    返回:
      {
        "date": str,
        "total_count": int,
        "new_candidates": list[str],
        "existing_candidates": list[str],
        "risk_alerts": list[str],
        "removed_candidates": list[str],
      }
    """
    rows = get_candidate_pool(conn, data_date)
    if not rows:
        return {"date": data_date or str(date.today()), "total_count": 0, "new_candidates": [], "existing_candidates": [], "risk_alerts": [], "removed_candidates": []}

    # 也查询退出和风险
    all_rows = conn.execute(
        "SELECT * FROM stock_pool_state WHERE date = (SELECT MAX(date) FROM stock_pool_state)"
    ).fetchall()

    result = {
        "date": rows[0].get("date", str(date.today())),
        "total_count": 0,
        "new_candidates": [],
        "existing_candidates": [],
        "risk_alerts": [],
        "removed_candidates": [],
    }

    for r in all_rows:
        status = r["pool_status"] or ""
        code = r["code"] or ""
        if status == "new":
            result["new_candidates"].append(code)
        elif status == "existing":
            result["existing_candidates"].append(code)
        elif status == "risk_alert":
            result["risk_alerts"].append(code)
        elif status == "removed":
            result["removed_candidates"].append(code)

    result["total_count"] = len(result["new_candidates"]) + len(result["existing_candidates"])
    return result


def search_stocks_tool(
    conn: sqlite3.Connection,
    filters: dict,
) -> list[dict]:
    """
    按条件筛选股票。

    支持的筛选条件:
      market: str (HK/US/CN)
      min_drawdown: float
      max_rsi: float
      min_bottom_signal: int
      max_pe_ttm: float
      industry: str
    """
    sql = "SELECT * FROM stock_daily_snapshot WHERE date = (SELECT MAX(date) FROM stock_daily_snapshot)"
    params = []

    if filters.get("market"):
        sql += " AND market = ?"
        params.append(filters["market"])

    if filters.get("industry"):
        sql += " AND industry LIKE ?"
        params.append(f"%{filters['industry']}%")

    if filters.get("min_drawdown"):
        sql += " AND drawdown_from_high_pct >= ?"
        params.append(filters["min_drawdown"])

    if filters.get("max_rsi"):
        sql += " AND rsi_14 <= ?"
        params.append(filters["max_rsi"])

    if filters.get("min_bottom_signal"):
        sql += " AND bottom_signal_score >= ?"
        params.append(filters["min_bottom_signal"])

    if filters.get("max_pe_ttm"):
        sql += " AND pe_ttm <= ? AND pe_ttm > 0"
        params.append(filters["max_pe_ttm"])

    sql += " ORDER BY bottom_signal_score DESC LIMIT 50"

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_llm_analysis_queue_tool(
    conn: sqlite3.Connection,
    data_date: Optional[str] = None,
    config_path: str = "config/config.yaml",
) -> dict:
    """
    按行业采样，返回 LLM 分析队列。
    每个行业取 bottom_signal_score 最高的 top_n × llm_slots_ratio 只股票。

    返回:
      {
        "date": str,
        "total_analyzed": int,
        "queues": {
          "sector_key": {
            "label": "科技",
            "icon": "💻",
            "top_n": 10,
            "llm_slots": 15,
            "codes": ["MSFT.US", "AAPL.US", ...]
          }
        },
        "all_codes": ["MSFT.US", "AAPL.US", ...]
      }
    """
    if data_date is None:
        data_date = str(date.today())

    # 加载配置
    config_full_path = Path(__file__).parent.parent / config_path
    with open(config_full_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sectors_cfg = config.get("report", {}).get("sectors", {})
    llm_slots_ratio = config.get("report", {}).get("llm_slots_ratio", 1.5)

    # 查候选池中所有 new + existing 的股票，附带 snapshot 中的行业和分数
    rows = conn.execute(
        """SELECT p.code, s.industry, s.bottom_signal_score, s.name, s.market,
                  p.pool_status, p.days_in_pool
           FROM stock_pool_state p
           JOIN stock_daily_snapshot s ON s.code = p.code AND s.date = ?
           WHERE p.date = ? AND p.pool_status IN ('new', 'existing')
           ORDER BY s.industry, s.bottom_signal_score DESC""",
        (data_date, data_date),
    ).fetchall()

    # 按行业分组
    by_sector: dict[str, list[dict]] = {}
    for r in rows:
        sector = r["industry"] or "_unknown"
        by_sector.setdefault(sector, []).append(dict(r))

    # 无配置时降级：返回全部候选（无行业分组）
    if not sectors_cfg:
        all_codes = [r["code"] for r in rows]
        return {
            "date": data_date,
            "total_analyzed": len(all_codes),
            "queues": {
                "_all": {
                    "label": "全部",
                    "icon": "📊",
                    "top_n": len(all_codes),
                    "llm_slots": len(all_codes),
                    "codes": all_codes,
                }
            },
            "all_codes": all_codes,
        }

    # 按配置构建队列
    queues: dict[str, dict] = {}
    all_codes: list[str] = []
    total = 0

    for sector_key, cfg in sectors_cfg.items():
        candidates = by_sector.get(sector_key, [])
        top_n = cfg.get("top_n", 5)
        slots = math.ceil(top_n * llm_slots_ratio)
        selected = [c["code"] for c in candidates[:slots]]

        queues[sector_key] = {
            "label": cfg.get("label", sector_key),
            "icon": cfg.get("icon", ""),
            "top_n": top_n,
            "llm_slots": slots,
            "candidate_count": len(candidates),
            "codes": selected,
        }
        all_codes.extend(selected)
        total += len(selected)

    return {
        "date": data_date,
        "total_analyzed": total,
        "queues": queues,
        "all_codes": all_codes,
    }
