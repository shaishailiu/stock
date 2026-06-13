"""
报告生成器

从数据库聚合数据，生成可读的每日研究报告。
支持三种输出格式：Markdown 文件、终端彩色输出、JSON 数据文件。

用法:
  from report.generator import generate_daily_report, format_markdown, format_console, save_report

  report = generate_daily_report(conn, "2026-06-13")
  print(format_console(report))        # 终端彩色输出
  md = format_markdown(report)         # Markdown 字符串
  save_report(conn, "2026-06-13", "reports")  # 一站式保存
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional

from storage.repositories import _json_loads

logger = logging.getLogger("newstock.report.generator")

# ── 股票中文名映射（从 watchlist.json 构建） ──
_DISPLAY_NAME_MAP: dict[str, str] = {}


def _init_display_names() -> dict[str, str]:
    """从 watchlist.json 构建代码→中文名映射"""
    if _DISPLAY_NAME_MAP:
        return _DISPLAY_NAME_MAP
    try:
        watchlist_path = Path(__file__).parent.parent / "config" / "watchlist.json"
        if watchlist_path.exists():
            with open(watchlist_path, encoding="utf-8") as f:
                data = json.load(f)
            for s in data.get("stocks", []):
                symbol = s.get("symbol", "")
                name_raw = s.get("name", "")
                # 从 "00700（腾讯）" 中提取中文部分
                m = re.search(r"（(.+?)）", name_raw)
                if m:
                    _DISPLAY_NAME_MAP[symbol] = m.group(1)
    except Exception:
        pass
    return _DISPLAY_NAME_MAP


def _display_name(code: str, raw_name: str) -> str:
    """返回展示用名称：优先中文名，其次原始名"""
    names = _init_display_names()
    if code in names:
        return names[code]
    return raw_name or "?"


# sectors 配置缓存
_SECTORS_CONFIG: dict[str, dict] = {}
_LLM_SLOTS_RATIO: float = 1.5


def _load_sectors_config() -> dict[str, dict]:
    """加载 report.sectors 配置（带缓存）"""
    global _SECTORS_CONFIG, _LLM_SLOTS_RATIO
    if _SECTORS_CONFIG:
        return _SECTORS_CONFIG
    try:
        import yaml
    except ImportError:
        return {}
    try:
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        _SECTORS_CONFIG = config.get("report", {}).get("sectors", {})
        _LLM_SLOTS_RATIO = config.get("report", {}).get("llm_slots_ratio", 1.5)
    except Exception:
        pass
    return _SECTORS_CONFIG

# ──────────────────────────────────────────────────────────────────────
# 查询函数
# ──────────────────────────────────────────────────────────────────────


def _query_market_overview(
    conn: sqlite3.Connection, report_date: str
) -> list[dict[str, Any]]:
    """查询各市场概况"""
    rows = conn.execute(
        """SELECT
             market,
             COUNT(*) AS candidate_count,
             ROUND(AVG(drawdown_from_high_pct), 1) AS avg_drawdown,
             SUM(CASE WHEN alert_level = 'red' THEN 1 ELSE 0 END) AS risk_count
           FROM stock_daily_snapshot
           WHERE date = ?
           GROUP BY market
           ORDER BY market""",
        (report_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_top_priority(
    conn: sqlite3.Connection, report_date: str, limit: Optional[int] = None
) -> list[dict[str, Any]]:
    """查询排名靠前的重点观察股票（含分析结论）。

    Args:
        limit: 返回条数上限，None 表示不限制
    """
    sql = """SELECT s.code, s.name, s.market, s.industry,
                s.drawdown_from_high_pct, s.rsi_14,
                s.pe_ttm, s.pb, s.bottom_signal_score,
                s.alert_level, s.current_price,
                a.final_priority, a.research_priority,
                a.decision, a.confidence,
                a.llm_priority_score, a.bottom_signal_score AS analysis_bottom_score,
                a.attention_score, a.main_logic,
                a.main_positive_points_json, a.main_risks_json,
                a.key_contradictions_json, a.suggested_follow_up_json,
                a.valuation_attractiveness, a.fundamental_quality,
                a.opportunity_quality, a.risk_level,
                a.value_trap_probability
           FROM stock_pool_state p
           LEFT JOIN stock_daily_snapshot s
             ON s.code = p.code AND s.date = ?
           LEFT JOIN (
             SELECT code, final_priority, research_priority,
                    decision, confidence, llm_priority_score,
                    bottom_signal_score, attention_score,
                    main_logic, main_positive_points_json,
                    main_risks_json, key_contradictions_json,
                    suggested_follow_up_json,
                    valuation_attractiveness, fundamental_quality,
                    opportunity_quality, risk_level,
                    value_trap_probability,
                    ROW_NUMBER() OVER (PARTITION BY code ORDER BY analysis_date DESC) AS rn
             FROM stock_llm_analysis
           ) a ON s.code = a.code AND a.rn = 1
           WHERE p.date = ?
             AND p.pool_status IN ('new', 'existing')
           ORDER BY a.final_priority DESC NULLS LAST"""
    params: list = [report_date, report_date]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["main_positive_points"] = _json_loads(d.get("main_positive_points_json")) or []
        d["main_risks"] = _json_loads(d.get("main_risks_json")) or []
        d["key_contradictions"] = _json_loads(d.get("key_contradictions_json")) or []
        d["suggested_follow_up"] = _json_loads(d.get("suggested_follow_up_json")) or []
        results.append(d)
    return results


def _query_new_candidates(
    conn: sqlite3.Connection, report_date: str
) -> list[dict[str, Any]]:
    """查询今日新进候选"""
    rows = conn.execute(
        """SELECT s.code, s.name, s.market, s.industry,
                s.drawdown_from_high_pct, s.rsi_14,
                s.pe_ttm, s.pb, s.bottom_signal_score,
                s.current_price,
                sc.reason AS signal_reason,
                sc.score_detail_json
           FROM stock_pool_state p
           JOIN stock_daily_snapshot s
             ON s.code = p.code AND s.date = ?
           LEFT JOIN stock_signal_card sc
             ON sc.code = p.code AND sc.date = ?
           WHERE p.date = ? AND p.pool_status = 'new'
           ORDER BY s.bottom_signal_score DESC""",
        (report_date, report_date, report_date),
    ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["score_detail"] = _json_loads(d.get("score_detail_json")) or {}
        results.append(d)
    return results


def _query_risk_alerts(
    conn: sqlite3.Connection, report_date: str
) -> list[dict[str, Any]]:
    """查询风险警报股票"""
    rows = conn.execute(
        """SELECT s.code, s.name, s.market, s.industry,
                s.drawdown_from_high_pct, s.rsi_14,
                s.pe_ttm, s.pb, s.bottom_signal_score,
                s.current_price, s.risk_flags_json,
                a.decision, a.research_priority,
                a.main_risks_json, a.value_trap_probability
           FROM stock_pool_state p
           JOIN stock_daily_snapshot s
             ON s.code = p.code AND s.date = ?
           LEFT JOIN (
             SELECT code, decision, research_priority,
                    main_risks_json, value_trap_probability,
                    ROW_NUMBER() OVER (PARTITION BY code ORDER BY analysis_date DESC) AS rn
             FROM stock_llm_analysis
           ) a ON s.code = a.code AND a.rn = 1
           WHERE p.date = ? AND p.pool_status = 'risk_alert'
           ORDER BY s.bottom_signal_score DESC""",
        (report_date, report_date),
    ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["risk_flags"] = _json_loads(d.get("risk_flags_json")) or []
        d["main_risks"] = _json_loads(d.get("main_risks_json")) or []
        results.append(d)
    return results


def _query_removed_candidates(
    conn: sqlite3.Connection, report_date: str
) -> list[dict[str, Any]]:
    """查询移除的候选"""
    rows = conn.execute(
        """SELECT s.code, s.name, s.market, s.industry,
                s.current_price, s.drawdown_from_high_pct
           FROM stock_pool_state p
           LEFT JOIN stock_daily_snapshot s
             ON s.code = p.code AND s.date = ?
           WHERE p.date = ? AND p.pool_status = 'removed'
           ORDER BY s.code""",
        (report_date, report_date),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_pool_stats(
    conn: sqlite3.Connection, report_date: str
) -> dict[str, int]:
    """查询候选池统计"""
    row = conn.execute(
        """SELECT
             COUNT(*) AS total_pool_count,
             SUM(CASE WHEN pool_status = 'new' THEN 1 ELSE 0 END) AS new_candidates,
             SUM(CASE WHEN pool_status = 'existing' THEN 1 ELSE 0 END) AS existing_candidates,
             SUM(CASE WHEN pool_status = 'risk_alert' THEN 1 ELSE 0 END) AS risk_alerts,
             SUM(CASE WHEN pool_status = 'removed' THEN 1 ELSE 0 END) AS removed_candidates
           FROM stock_pool_state
           WHERE date = ?""",
        (report_date,),
    ).fetchone()
    return dict(row) if row else {}


# ──────────────────────────────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────────────────────────────


def generate_daily_report(
    conn: sqlite3.Connection, report_date: str
) -> dict[str, Any]:
    """
    从数据库生成完整报告数据结构。

    返回字典包含所有章节数据，可直接用于格式化输出。
    """
    pool_stats = _query_pool_stats(conn, report_date)

    report: dict[str, Any] = {
        "date": report_date,
        "generated_at": str(date.today()),
        "summary": pool_stats,
        "market_overview": _query_market_overview(conn, report_date),
        "top_priority": _query_top_priority(conn, report_date, limit=None),
        "new_candidates": _query_new_candidates(conn, report_date),
        "risk_alerts": _query_risk_alerts(conn, report_date),
        "removed_candidates": _query_removed_candidates(conn, report_date),
    }

    logger.info(
        "Report generated: date=%s, pool=%d, top=%d, new=%d, risk=%d, removed=%d",
        report_date,
        pool_stats.get("total_pool_count", 0),
        len(report["top_priority"]),
        len(report["new_candidates"]),
        len(report["risk_alerts"]),
        len(report["removed_candidates"]),
    )
    return report


# ──────────────────────────────────────────────────────────────────────
# Markdown 输出
# ──────────────────────────────────────────────────────────────────────


def format_markdown(report: dict[str, Any], push_mode: bool = False) -> str:
    """将报告格式化为 Markdown 字符串

    Args:
        report: 报告数据字典
        push_mode: True = 企业微信推送版（去除市场概览、新进候选、移除候选）
    """
    lines: list[str] = []
    _a = lines.append

    _a(f"# 📊 价值筛选日报 — {report['date']}")
    _a("")
    _a(f"> 生成时间：{report['generated_at']}")
    _a("")
    _a("---")
    _a("")

    # ── 大盘概览 ──
    if not push_mode:
        _a("## 📈 市场概览")
        _a("")
        if report["market_overview"]:
            _a("| 市场 | 候选数 | 平均回撤 | 风险警报 |")
            _a("|------|--------|----------|----------|")
            for m in report["market_overview"]:
                market_name = {"HK": "港股", "US": "美股", "CN": "A股"}.get(
                    m["market"], m["market"]
                )
                _a(
                    f"| {market_name} | {m['candidate_count']} | "
                    f"{m['avg_drawdown']:.1f}% | {m['risk_count']} |"
                )
            _a("")
        else:
            _a("今日无市场数据")
            _a("")

    # ── 候选池统计 ──
    _a("## 🏊 候选池总览")
    _a("")
    s = report.get("summary", {})
    total = s.get("total_pool_count", 0)
    new_c = s.get("new_candidates", 0)
    existing = s.get("existing_candidates", 0)
    risk_c = s.get("risk_alerts", 0)
    removed = s.get("removed_candidates", 0)
    _a(f"| 候选池总数 | 🆕 今日新进 | 📋 老池 | ⚠️ 风险警报 | ❌ 今日移除 |")
    _a(f"|-----------|-----------|-------|-----------|-----------|")
    _a(f"| {total} | {new_c} | {existing} | {risk_c} | {removed} |")
    _a("")

    # ── 重点观察（按行业） ──
    sectors_cfg = _load_sectors_config()
    top = report.get("top_priority", [])
    if top:
        # 按行业分组
        by_sector: dict[str, list] = {}
        for stock in top:
            sector = stock.get("industry") or "_unknown"
            by_sector.setdefault(sector, []).append(stock)

        _a("## 🔥 重点观察（按行业）")
        _a("")

        # 定义展示列
        table_header = (
            "| 代码 | 名称 | 市场 | 回撤 | PE | RSI | "
            "final_priority | 决策 |"
        )
        table_sep = (
            "|------|------|------|------|-----|-----|"
            "----------------|------|"
        )

        # 按行业配置顺序渲染（配置中已定义的行业优先）
        ordered_sectors = list(sectors_cfg.keys())
        for sector_key in ordered_sectors:
            stocks_in_sector = by_sector.get(sector_key, [])
            if not stocks_in_sector:
                continue
            top_n = sectors_cfg[sector_key].get("top_n", 5)
            label = sectors_cfg[sector_key].get("label", sector_key)
            icon = sectors_cfg[sector_key].get("icon", "")
            shown = stocks_in_sector[:top_n]

            _a(f"### {icon} {label}（Top {len(shown)}/{len(stocks_in_sector)}）")
            _a("")
            _a(table_header)
            _a(table_sep)
            for stock in shown:
                code = stock.get("code", "")
                name = _display_name(code, stock.get("name", "?") or "?")
                market = stock.get("market", "?")
                dd = stock.get("drawdown_from_high_pct")
                dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
                pe = stock.get("pe_ttm")
                pe_str = f"{pe:.1f}" if pe and pe > 0 else "亏损"
                rsi = stock.get("rsi_14")
                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                fp = stock.get("final_priority")
                fp_str = f"{fp:.1f}" if fp is not None else "—"
                decision = stock.get("decision", "—") or "—"
                _a(
                    f"| {code} | {name} | {market} | {dd_str} | "
                    f"{pe_str} | {rsi_str} | {fp_str} | {decision} |"
                )
            _a("")

        # 未配置的行业兜底
        for sector_key, stocks_in_sector in by_sector.items():
            if sector_key in ordered_sectors or sector_key == "_unknown":
                continue
            _a(f"### 📌 {sector_key}（Top {min(len(stocks_in_sector), 5)}）")
            _a("")
            _a(table_header)
            _a(table_sep)
            for stock in stocks_in_sector[:5]:
                code = stock.get("code", "")
                name = _display_name(code, stock.get("name", "?") or "?")
                market = stock.get("market", "?")
                dd = stock.get("drawdown_from_high_pct")
                dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
                pe = stock.get("pe_ttm")
                pe_str = f"{pe:.1f}" if pe and pe > 0 else "亏损"
                rsi = stock.get("rsi_14")
                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                fp = stock.get("final_priority")
                fp_str = f"{fp:.1f}" if fp is not None else "—"
                decision = stock.get("decision", "—") or "—"
                _a(
                    f"| {code} | {name} | {market} | {dd_str} | "
                    f"{pe_str} | {rsi_str} | {fp_str} | {decision} |"
                )
            _a("")
    else:
        _a("## 🔥 重点观察（按行业）")
        _a("")
        _a("暂无重点观察股票")
        _a("")

    # ── 今日新进 ──
    if not push_mode:
        _a("## 🆕 今日新进候选")
        _a("")
        new_list = report.get("new_candidates", [])
        if new_list:
            _a("| 代码 | 名称 | 市场 | 回撤 | RSI | 底部信号 | 触发原因 |")
            _a("|------|------|------|------|-----|----------|----------|")
            for stock in new_list:
                code = stock.get("code", "")
                name = _display_name(code, stock.get("name", "?") or "?")
                market = stock.get("market", "?")
                dd = stock.get("drawdown_from_high_pct")
                dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
                rsi = stock.get("rsi_14")
                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                bs = stock.get("bottom_signal_score", 0)
                signal_reason = (stock.get("signal_reason") or "").replace("|", "/")
                _a(f"| {code} | {name} | {market} | {dd_str} | {rsi_str} | {bs} | {signal_reason} |")
            _a("")
        else:
            _a("今日无新进候选")
            _a("")

    # ── 风险警报 ──
    risk_list = report.get("risk_alerts", [])
    if risk_list:
        _a("## ⚠️ 风险警报")
        _a("")
        _a("| 代码 | 名称 | 市场 | 回撤 | 风险标记 | 价值陷阱概率 | 主要风险 |")
        _a("|------|------|------|------|----------|--------------|----------|")
        for stock in risk_list:
            code = stock.get("code", "")
            name = _display_name(code, stock.get("name", "?") or "?")
            market = stock.get("market", "?")
            dd = stock.get("drawdown_from_high_pct")
            dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
            risk_flags = stock.get("risk_flags", [])
            flags_str = ", ".join(risk_flags) if risk_flags else "—"
            vtp = stock.get("value_trap_probability", "")
            vtp_labels = {"low": "低", "medium": "中", "high": "高"}
            vtp_str = vtp_labels.get(vtp, vtp) if vtp else "—"
            main_risks = stock.get("main_risks", [])
            risks_str = "; ".join(main_risks) if main_risks else "—"
            _a(f"| {code} | {name} | {market} | {dd_str} | {flags_str} | {vtp_str} | {risks_str} |")
        _a("")
        _a("")

    # ── 移除候选 ──
    if not push_mode:
        removed = report.get("removed_candidates", [])
        if removed:
            _a("## ❌ 今日移除候选")
            _a("")
            _a("| 代码 | 名称 | 市场 | 最新回撤 |")
            _a("|------|------|------|----------|")
            for stock in removed:
                code = stock.get("code", "")
                name = _display_name(code, stock.get("name", "?") or "?")
                market = stock.get("market", "?")
                dd = stock.get("drawdown_from_high_pct")
                dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
                _a(f"| {code} | {name} | {market} | {dd_str} |")
            _a("")

    # ── 个股详细分析 ──
    _a("## 🔍 个股详细分析")
    _a("")

    if top:
        for stock in top:
            _a(_stock_detail_markdown(stock))
    else:
        _a("暂无已分析的股票")
        _a("")

    # Footer
    _a("---")
    _a("")
    _a(f"*报告由 newstock 系统自动生成 | {report['generated_at']}*")
    _a("")

    return "\n".join(lines)


def _table_row(*cells: str) -> str:
    """生成表格行"""
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _stock_detail_markdown(stock: dict[str, Any]) -> str:
    """生成单只股票的详细 Markdown 分析（表格版）"""
    lines: list[str] = []
    _a = lines.append

    code = stock.get("code", "")
    name = _display_name(code, stock.get("name", "?") or "?")
    market = stock.get("market", "?")

    _a(f"### {code} — {name} [{market}]")
    _a("")

    # ── 基本信息 + 决策（表格 1） ──
    current_price = stock.get("current_price")
    dd = stock.get("drawdown_from_high_pct")
    pe = stock.get("pe_ttm")
    pb = stock.get("pb")
    rsi = stock.get("rsi_14")
    bs = stock.get("bottom_signal_score", 0)
    decision = stock.get("decision") or "—"
    rp = stock.get("research_priority") or "—"
    confidence = stock.get("confidence")

    price_str = f"{current_price:.2f}" if current_price is not None else "N/A"
    dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
    pe_str = f"{pe:.1f}" if pe is not None and pe > 0 else "亏损"
    pb_str = f"{pb:.2f}" if pb is not None else "N/A"
    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
    conf_str = f"{confidence:.2f}" if confidence is not None else "—"

    headers1 = ["现价", "回撤", "PE", "PB", "RSI", "底部信号", "决策", "优先级", "置信度"]
    values1 = [price_str, dd_str, pe_str, pb_str, rsi_str, str(bs), decision, f"`{rp}`", conf_str]
    _a(_table_row(*headers1))
    _a(_table_row(*["---" for _ in headers1]))
    _a(_table_row(*values1))
    _a("")

    # ── 评分拆解 + 质量评估（表格 2） ──
    llm_score = stock.get("llm_priority_score")
    bottom_score = stock.get("analysis_bottom_score") or stock.get("bottom_signal_score", 0)
    attn_score = stock.get("attention_score")
    final_p = stock.get("final_priority")
    opp_q = stock.get("opportunity_quality")
    val_q = stock.get("valuation_attractiveness")
    fund_q = stock.get("fundamental_quality")
    risk_l = stock.get("risk_level")
    vtp = stock.get("value_trap_probability")

    llm_str = str(llm_score) if llm_score is not None else "—"
    attn_str = str(attn_score) if attn_score is not None else "—"
    fp_str = f"**{final_p:.1f}**" if final_p is not None else "—"
    opp_str = f"{opp_q}/5" if opp_q is not None else "—"
    val_str = f"{val_q}/5" if val_q is not None else "—"
    fund_str = f"{fund_q}/5" if fund_q is not None else "—"
    risk_str = f"{risk_l}/5" if risk_l is not None else "—"
    vtp_labels = {"low": "低", "medium": "中", "high": "高"}
    vtp_str = vtp_labels.get(vtp, vtp) if vtp else "—"

    headers2 = ["llm", "bottom", "attention", "final", "机会质量", "估值吸引力", "基本面", "风险", "价值陷阱"]
    values2 = [llm_str, str(bottom_score), attn_str, fp_str, opp_str, val_str, fund_str, risk_str, vtp_str]
    _a(_table_row(*headers2))
    _a(_table_row(*["---" for _ in headers2]))
    _a(_table_row(*values2))
    _a("")

    # ── 主要逻辑 ──
    main_logic = stock.get("main_logic")
    if main_logic:
        _a(f"**核心逻辑：** {main_logic}")
        _a("")

    # ── 正面因素 vs 主要风险（表格 3） ──
    positives = stock.get("main_positive_points", [])
    risks = stock.get("main_risks", [])
    if positives or risks:
        _a(_table_row("✅ 正面因素", "⚠️ 主要风险"))
        _a(_table_row("---", "---"))
        max_len = max(len(positives), len(risks))
        for i in range(max_len):
            p = f"✅ {positives[i]}" if i < len(positives) else ""
            r = f"⚠️ {risks[i]}" if i < len(risks) else ""
            _a(_table_row(p, r))
        _a("")

    # ── 矛盾信号 ──
    contradictions = stock.get("key_contradictions", [])
    if contradictions:
        _a("⚡ **矛盾信号**")
        _a("")
        for c in contradictions:
            _a(f"> {c}")
        _a("")

    # ── 建议后续行动 ──
    follow_ups = stock.get("suggested_follow_up", [])
    if follow_ups:
        _a("📋 **建议后续行动**")
        _a("")
        for f in follow_ups:
            _a(f"- {f}")
        _a("")

    _a("---")
    _a("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# 终端彩色输出
# ──────────────────────────────────────────────────────────────────────

# 简单的 ANSI 颜色码（无需依赖第三方库）
_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bg_red": "\033[41m",
    "bg_green": "\033[42m",
    "bg_yellow": "\033[43m",
}


def _c(*codes: str) -> str:
    """生成 ANSI 颜色码"""
    return "".join(_COLORS.get(c, "") for c in codes)


def _sep(char: str = "=", width: int = 70) -> str:
    return char * width


def format_console(report: dict[str, Any]) -> str:
    """将报告格式化为彩色终端输出"""
    lines: list[str] = []
    _a = lines.append

    # 标题
    _a("")
    _a(_c("bold", "cyan") + _sep("=") + _c("reset"))
    _a(
        _c("bold", "cyan")
        + f"   📊 价值筛选日报 — {report['date']}"
        + _c("reset")
    )
    _a(_c("bold", "cyan") + _sep("=") + _c("reset"))
    _a(f"   {_c('dim')}生成时间：{report['generated_at']}{_c('reset')}")
    _a("")

    # ── 市场概览 ──
    _a(_c("bold", "yellow") + "📈 市场概览" + _c("reset"))
    _a("")
    if report["market_overview"]:
        header = f"  {'市场':<6} {'候选':<6} {'平均回撤':<10} {'风险':<6}"
        _a(_c("dim") + header + _c("reset"))
        for m in report["market_overview"]:
            market_name = {"HK": "港股", "US": "美股", "CN": "A股"}.get(
                m["market"], m["market"]
            )
            risk_str = (
                _c("red") + str(m["risk_count"]) + _c("reset")
                if m["risk_count"] > 0
                else str(m["risk_count"])
            )
            _a(
                f"  {market_name:<6} "
                f"{m['candidate_count']:<6} "
                f"{m['avg_drawdown']:.1f}%{'':<7} "
                f"{risk_str}"
            )
        _a("")
    else:
        _a("  今日无市场数据")
        _a("")

    # ── 候选池统计 ──
    s = report.get("summary", {})
    total = s.get("total_pool_count", 0)
    new_count = s.get("new_candidates", 0)
    existing = s.get("existing_candidates", 0)
    risk_count = s.get("risk_alerts", 0)
    removed = s.get("removed_candidates", 0)

    _a(_c("bold", "yellow") + "🏊 候选池总览" + _c("reset"))
    _a(
        f"  总数: {total}  |  "
        f"🆕 新进: {new_count}  |  "
        f"📋 老池: {existing}  |  "
        + (
            _c("red") + f"⚠️ 风险: {risk_count}" + _c("reset")
            if risk_count > 0
            else f"⚠️ 风险: {risk_count}"
        )
        + f"  |  ❌ 移除: {removed}"
    )
    _a("")

    # ── 重点观察（按行业） ──
    _a(_c("bold", "yellow") + "🔥 重点观察（按行业）" + _c("reset"))
    _a("")
    top = report.get("top_priority", [])
    if top:
        sectors_cfg = _load_sectors_config()
        # 按行业分组
        by_sector: dict[str, list] = {}
        for stock in top:
            sector = stock.get("industry") or "_unknown"
            by_sector.setdefault(sector, []).append(stock)

        console_header = (
            _c("dim")
            + f"  {'代码':<12} {'名称':<10} {'市场':<5} "
            f"{'回撤':<8} {'PE':<7} {'RSI':<6} {'final_priority':<10} {'决策'}"
            + _c("reset")
        )

        # 按配置顺序渲染
        ordered_sectors = list(sectors_cfg.keys())
        for sector_key in ordered_sectors:
            stocks_in_sector = by_sector.get(sector_key, [])
            if not stocks_in_sector:
                continue
            top_n = sectors_cfg[sector_key].get("top_n", 5)
            label = sectors_cfg[sector_key].get("label", sector_key)
            icon = sectors_cfg[sector_key].get("icon", "")
            shown = stocks_in_sector[:top_n]

            _a(f"  {icon} {label}（Top {len(shown)}/{len(stocks_in_sector)}）")
            _a(console_header)
            for stock in shown:
                code = stock.get("code", "")
                name = _display_name(code, (stock.get("name") or "?")[:10])
                market = stock.get("market", "?")
                dd = stock.get("drawdown_from_high_pct")
                dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
                pe = stock.get("pe_ttm")
                pe_str = f"{pe:.1f}" if pe and pe > 0 else "亏损"
                rsi = stock.get("rsi_14")
                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                fp = stock.get("final_priority")
                fp_str = f"{fp:.1f}" if fp is not None else "—"
                decision = (stock.get("decision") or "—")[:8]

                # 高优先级高亮
                prefix = _c("bold") if fp and fp >= 55 else ""
                suffix = _c("reset") if prefix else ""

                _a(
                    f"  {prefix}{code:<12} {name:<10} {market:<5} "
                    f"{dd_str:<8} {pe_str:<7} {rsi_str:<6} {fp_str:<10} "
                    f"{decision}{suffix}"
                )
            _a("")

        # 未配置的行业兜底
        for sector_key, stocks_in_sector in by_sector.items():
            if sector_key in ordered_sectors or sector_key == "_unknown":
                continue
            _a(f"  📌 {sector_key}")
            _a(console_header)
            for stock in stocks_in_sector[:5]:
                code = stock.get("code", "")
                name = _display_name(code, (stock.get("name") or "?")[:10])
                market = stock.get("market", "?")
                dd = stock.get("drawdown_from_high_pct")
                dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
                pe = stock.get("pe_ttm")
                pe_str = f"{pe:.1f}" if pe and pe > 0 else "亏损"
                rsi = stock.get("rsi_14")
                rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                fp = stock.get("final_priority")
                fp_str = f"{fp:.1f}" if fp is not None else "—"
                decision = (stock.get("decision") or "—")[:8]
                _a(
                    f"  {code:<12} {name:<10} {market:<5} "
                    f"{dd_str:<8} {pe_str:<7} {rsi_str:<6} {fp_str:<10} "
                    f"{decision}"
                )
            _a("")
    else:
        _a("  暂无重点观察股票")
        _a("")

    # ── 今日新进 ──
    _a(_c("bold", "green") + "🆕 今日新进候选" + _c("reset"))
    _a("")
    new_list = report.get("new_candidates", [])
    if new_list:
        for stock in new_list:
            code = stock.get("code", "")
            name = _display_name(code, stock.get("name", "?") or "?")
            market = stock.get("market", "?")
            dd = stock.get("drawdown_from_high_pct")
            dd_str = f"{dd:.1f}%" if dd is not None else "N/A"
            rsi = stock.get("rsi_14")
            rsi_str = f"RSI={rsi:.1f}" if rsi is not None else ""
            bs = stock.get("bottom_signal_score", 0)
            signal_reason = stock.get("signal_reason", "")
            score_detail = stock.get("score_detail", {})

            _a(f"  {_c('bold')}{code}{_c('reset')} "
               f"[{_c('cyan')}{market}{_c('reset')}] {name}")
            _a(f"    回撤: {dd_str}  |  {rsi_str}  |  底部信号分: {bs}")

            if signal_reason:
                _a(f"    触发: {signal_reason}")
            if score_detail:
                detail_parts = [f"{k}={v}" for k, v in score_detail.items()]
                _a(f"    信号拆解: {', '.join(detail_parts)}")
            _a("")
    else:
        _a("  今日无新进候选")
        _a("")

    # ── 风险警报 ──
    _a(_c("bold", "red") + "⚠️ 风险警报" + _c("reset"))
    _a("")
    risk_list = report.get("risk_alerts", [])
    if risk_list:
        for stock in risk_list:
            code = stock.get("code", "")
            name = _display_name(code, stock.get("name", "?") or "?")
            market = stock.get("market", "?")
            risk_flags = stock.get("risk_flags", [])
            main_risks = stock.get("main_risks", [])
            vtp = stock.get("value_trap_probability", "")
            vtp_labels = {"low": "低", "medium": "中", "high": "高"}

            _a(f"  {_c('bold', 'red')}{code}{_c('reset')} ")
            _a(f"  [{_c('cyan')}{market}{_c('reset')}] {name}")
            if risk_flags:
                flags_str = ", ".join(f"[{f}]" for f in risk_flags)
                _a(f"    风险标记: {_c('red')}{flags_str}{_c('reset')}")
            if vtp:
                _a(f"    价值陷阱概率: {_c('yellow')}{vtp_labels.get(vtp, vtp)}{_c('reset')}")
            if main_risks:
                for r in main_risks:
                    _a(f"    ✗ {r}")
            _a("")
    else:
        _a("  暂无风险警报")
        _a("")

    # ── 移除候选 ──
    if removed > 0:
        _a(_c("bold") + "❌ 今日移除候选" + _c("reset"))
        _a("")
        for stock in report.get("removed_candidates", []):
            code = stock.get("code", "")
            name = _display_name(code, stock.get("name", "?") or "?")
            market = stock.get("market", "?")
            _a(f"  {code} {name} [{market}]")
        _a("")

    # ── 个股详细分析 ──
    _a(_c("bold", "yellow") + "🔍 个股详细分析" + _c("reset"))
    _a("")
    _a(_c("dim") + _sep("-") + _c("reset"))

    if top:
        for stock in top:
            _a(_stock_detail_console(stock))
    else:
        _a("  暂无已分析的股票")
        _a("")

    _a(_c("dim") + _sep("=") + _c("reset"))
    _a("")

    return "\n".join(lines)


def _stock_detail_console(stock: dict[str, Any]) -> str:
    """生成单只股票的详细终端输出"""
    lines: list[str] = []
    _a = lines.append

    code = stock.get("code", "")
    name = _display_name(code, stock.get("name", "?") or "?")
    market = stock.get("market", "?")

    _a("")
    _a(
        _c("bold", "cyan")
        + f"  {code} — {name} [{market}]"
        + _c("reset")
    )

    # 基本信息
    parts = []
    current_price = stock.get("current_price")
    if current_price is not None:
        parts.append(f"现价: {_c('bold')}{current_price:.2f}{_c('reset')}")
    dd = stock.get("drawdown_from_high_pct")
    if dd is not None:
        color = "green" if dd < -40 else "yellow" if dd < -25 else ""
        parts.append(f"回撤: {_c(color)}{dd:.1f}%{_c('reset')}" if color else f"回撤: {dd:.1f}%")
    pe = stock.get("pe_ttm")
    if pe is not None and pe > 0:
        parts.append(f"PE(TTM): {pe:.1f}")
    pb = stock.get("pb")
    if pb is not None:
        parts.append(f"PB: {pb:.2f}")
    rsi = stock.get("rsi_14")
    if rsi is not None:
        color = "red" if rsi < 30 else "green" if rsi > 70 else ""
        parts.append(f"RSI(14): {_c(color)}{rsi:.1f}{_c('reset')}" if color else f"RSI(14): {rsi:.1f}")
    parts.append(f"底部信号分: {stock.get('bottom_signal_score', 0)}")
    _a(f"  {' | '.join(parts)}")

    # 决策信息
    decision = stock.get("decision")
    rp = stock.get("research_priority")
    confidence = stock.get("confidence")
    if decision or rp:
        rp_colors = {
            "very_high": "red",
            "high": "yellow",
            "medium": "cyan",
            "low": "dim",
            "reject": "dim",
        }
        rp_color = rp_colors.get(rp, "")
        decision_line = f"  LLM 分析: {_c('bold')}{decision or '—'}{_c('reset')}"
        if rp:
            decision_line += f" | 优先级: {_c(rp_color)}{rp}{_c('reset')}" if rp_color else f" | 优先级: {rp}"
        if confidence is not None:
            decision_line += f" | 置信度: {confidence:.2f}"
        _a(decision_line)

    # 评分拆解
    llm_score = stock.get("llm_priority_score")
    bottom_score = stock.get("analysis_bottom_score") or stock.get("bottom_signal_score", 0)
    attn_score = stock.get("attention_score")
    final_p = stock.get("final_priority")

    has_any_score = llm_score is not None or attn_score is not None or final_p is not None
    if has_any_score:
        score_parts = []
        if llm_score is not None:
            score_parts.append(f"llm={llm_score}")
        score_parts.append(f"bottom={bottom_score}")
        if attn_score is not None:
            score_parts.append(f"attention={attn_score}")
        fp_color = "green" if final_p and final_p >= 60 else "yellow" if final_p and final_p >= 50 else ""
        if final_p is not None:
            score_parts.append(
                f"{_c('bold')}final={_c(fp_color)}{final_p:.1f}{_c('reset')}{_c('reset')}"
                if fp_color
                else f"final={final_p:.1f}"
            )
        _a(f"  评分: {' | '.join(score_parts)}")

    _a("")

    # 质量评估
    q_parts = []
    for key, label in [
        ("opportunity_quality", "机会质量"),
        ("valuation_attractiveness", "估值吸引力"),
        ("fundamental_quality", "基本面质量"),
        ("risk_level", "风险等级"),
    ]:
        val = stock.get(key)
        if val is not None:
            q_parts.append(f"{label}: {_c('bold')}{val}/5{_c('reset')}")

    vtp = stock.get("value_trap_probability")
    if vtp:
        vtp_labels = {"low": "低", "medium": "中", "high": "高"}
        vtp_color = "green" if vtp == "low" else "yellow" if vtp == "medium" else "red"
        q_parts.append(f"价值陷阱: {_c(vtp_color)}{vtp_labels.get(vtp, vtp)}{_c('reset')}")

    if q_parts:
        _a(f"  {'  '.join(q_parts)}")
        _a("")

    # 正面因素
    positives = stock.get("main_positive_points", [])
    if positives:
        for p in positives:
            _a(f"  {_c('green')}✓{_c('reset')} {p}")

    # 主要风险
    risks = stock.get("main_risks", [])
    if risks:
        for r in risks:
            _a(f"  {_c('red')}✗{_c('reset')} {r}")

    # 矛盾信号
    contradictions = stock.get("key_contradictions", [])
    if contradictions:
        for c in contradictions:
            _a(f"  {_c('yellow')}⚡{_c('reset')} {c}")

    # 后续行动
    follow_ups = stock.get("suggested_follow_up", [])
    if follow_ups:
        _a(f"  {_c('dim')}建议行动:{_c('reset')}")
        for f in follow_ups:
            _a(f"    → {f}")

    _a("")
    _a(_c("dim") + "  " + _sep("-", 66) + _c("reset"))

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# JSON 输出
# ──────────────────────────────────────────────────────────────────────


def format_json(report: dict[str, Any]) -> str:
    """将报告格式化为 JSON 字符串"""
    # 清理 None 值，使 JSON 更紧凑
    def clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if v is not None}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return obj

    return json.dumps(clean(report), ensure_ascii=False, indent=2, default=str)


# ──────────────────────────────────────────────────────────────────────
# 一站式保存
# ──────────────────────────────────────────────────────────────────────


def save_report(
    conn: sqlite3.Connection,
    report_date: str,
    output_dir: str = "reports",
    formats: Optional[list[str]] = None,
) -> dict[str, str]:
    """
    一站式：生成报告 → 写文件。

    Args:
        conn: 数据库连接
        report_date: 报告日期
        output_dir: 输出目录
        formats: 输出格式列表，默认 ["md", "json"]

    Returns:
        {"md": "/path/to/report.md", "json": "/path/to/report.json"}
    """
    if formats is None:
        formats = ["md", "json"]

    report = generate_daily_report(conn, report_date)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results = {}

    if "md" in formats:
        md_content = format_markdown(report)
        md_path = out_path / f"daily_{report_date}.md"
        md_path.write_text(md_content, encoding="utf-8")
        results["md"] = str(md_path)
        logger.info("Markdown report saved: %s", md_path)

    if "json" in formats:
        json_content = format_json(report)
        json_path = out_path / f"daily_{report_date}.json"
        json_path.write_text(json_content, encoding="utf-8")
        results["json"] = str(json_path)
        logger.info("JSON report saved: %s", json_path)

    return results
