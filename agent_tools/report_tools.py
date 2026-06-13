"""
Agent 工具：报告生成
"""

import json
import logging
import sqlite3
from datetime import date
from typing import Any, Optional

from storage.repositories import get_previous_analysis
from report.generator import generate_daily_report, save_report

logger = logging.getLogger("newstock.agent_tools.report")


def pool_summary_tool(conn: sqlite3.Connection, report_date: Optional[str] = None) -> dict:
    """
    生成候选池摘要（按行业分组）。

    返回:
      {
        "date": str,
        "summary": dict,
        "top_priority_by_sector": {"tech": [...], "internet": [...]},
        "top_priority": list,   // 扁平列表（兼容旧调用方）
        "new_candidates": list,
        "risk_alerts": list,
      }
    """
    import yaml
    from pathlib import Path
    import math

    if report_date is None:
        report_date = str(date.today())

    # 获取候选池
    all_states = conn.execute(
        "SELECT * FROM stock_pool_state WHERE date = ? ORDER BY code",
        (report_date,),
    ).fetchall()

    # 获取所有快照
    snapshots = conn.execute(
        "SELECT * FROM stock_daily_snapshot WHERE date = ?",
        (report_date,),
    ).fetchall()

    snapshot_map = {s["code"]: dict(s) for s in snapshots}

    # 构建报告
    new_candidates = []
    risk_alerts = []
    all_candidates = []

    for state in all_states:
        code = state["code"]
        status = state["pool_status"]
        snap = snapshot_map.get(code, {})

        entry = {
            "code": code,
            "name": snap.get("name"),
            "market": snap.get("market"),
            "industry": snap.get("industry"),
            "pool_status": status,
            "bottom_signal_score": snap.get("bottom_signal_score", 0),
            "drawdown_from_high_pct": snap.get("drawdown_from_high_pct"),
            "pe_ttm": snap.get("pe_ttm"),
        }

        # 获取分析结论
        analysis = get_previous_analysis(conn, code)
        if analysis:
            entry["research_priority"] = analysis.get("research_priority")
            entry["final_priority"] = analysis.get("final_priority")

        if status == "risk_alert":
            risk_alerts.append(entry)
        elif status == "new":
            new_candidates.append(entry)
        elif status in ("new", "existing"):
            all_candidates.append(entry)

    # 按 final_priority 降序排序
    all_candidates.sort(
        key=lambda x: x.get("final_priority", 0) or 0,
        reverse=True,
    )

    # 按行业分组
    by_sector: dict[str, list] = {}
    for c in all_candidates:
        sector = c.get("industry") or "_unknown"
        by_sector.setdefault(sector, []).append(c)

    # 加载行业配置
    sectors_cfg = {}
    try:
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        sectors_cfg = config.get("report", {}).get("sectors", {})
    except Exception:
        pass

    # 按配置的 top_n 截断
    top_priority_by_sector = {}
    for sector_key, stocks in by_sector.items():
        top_n = sectors_cfg.get(sector_key, {}).get("top_n", 5)
        top_priority_by_sector[sector_key] = stocks[:top_n]

    top_priority = all_candidates[:10]  # 兼容旧调用

    return {
        "date": report_date,
        "summary": {
            "total_pool_count": len(all_candidates),
            "new_candidates": len(new_candidates),
            "risk_alerts": len(risk_alerts),
        },
        "top_priority_by_sector": top_priority_by_sector,
        "top_priority": top_priority,
        "new_candidates": new_candidates,
        "risk_alerts": risk_alerts,
    }


def generate_report_tool(
    conn: sqlite3.Connection,
    report_date: Optional[str] = None,
    output_dir: str = "reports",
) -> dict:
    """
    生成并保存每日研究报告。

    Args:
        report_date: 报告日期，默认今天
        output_dir: 输出目录，默认 "reports"

    Returns:
        {
          "success": true,
          "date": str,
          "files": {"md": str, "json": str},
          "summary": {
            "total_pool_count": int,
            "new_candidates": int,
            "risk_alerts": int,
            "top_priority_count": int,
          },
          "top_priority": list,   // top 5 摘要
          "console_preview": str, // 终端预览前 3000 字符
        }
    """
    if report_date is None:
        report_date = str(date.today())

    # 生成完整报告
    report = generate_daily_report(conn, report_date)

    # 保存文件
    files = save_report(conn, report_date, output_dir)

    # 终端预览
    from report.generator import format_console

    console_text = format_console(report)

    # 生成企业微信推送版（push_mode 去除市场概览、新进候选、移除候选）
    from pathlib import Path
    from report.generator import format_markdown
    push_md = format_markdown(report, push_mode=True)
    push_path = Path(output_dir) / f"daily_{report_date}_push.md"
    push_path.write_text(push_md, encoding="utf-8")
    files["push"] = str(push_path)

    logger.info(
        "Report tool: date=%s, pool=%d, top=%d",
        report_date,
        report["summary"].get("total_pool_count", 0),
        len(report["top_priority"]),
    )

    # 按行业分组 top_priority
    by_sector = {}
    for s in report["top_priority"]:
        sector = s.get("industry") or "_unknown"
        by_sector.setdefault(sector, []).append(s)

    return {
        "success": True,
        "date": report_date,
        "files": files,
        "summary": {
            "total_pool_count": report["summary"].get("total_pool_count", 0),
            "new_candidates": report["summary"].get("new_candidates", 0),
            "existing_candidates": report["summary"].get("existing_candidates", 0),
            "risk_alerts": report["summary"].get("risk_alerts", 0),
            "removed_candidates": report["summary"].get("removed_candidates", 0),
            "top_priority_count": len(report["top_priority"]),
        },
        "top_priority": [
            {
                "code": s.get("code"),
                "name": s.get("name"),
                "market": s.get("market"),
                "final_priority": s.get("final_priority"),
                "decision": s.get("decision"),
                "research_priority": s.get("research_priority"),
                "bottom_signal_score": s.get("bottom_signal_score", 0),
                "drawdown_from_high_pct": s.get("drawdown_from_high_pct"),
            }
            for s in report["top_priority"][:5]
        ],
        "top_priority_by_sector": {
            sector: [
                {
                    "code": s.get("code"),
                    "name": s.get("name"),
                    "final_priority": s.get("final_priority"),
                    "decision": s.get("decision"),
                }
                for s in stocks[:10]
            ]
            for sector, stocks in by_sector.items()
        },
        "console_preview": console_text[:3000],
    }
