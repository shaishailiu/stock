#!/usr/bin/env python3
"""
模拟 LLM Agent 执行流程的测试脚本。

按 research_prompts.md 定义的顺序调用所有 agent 工具，验证完整链路。

用法：
  cd /Users/toy/Desktop/github/newstock
  python3 tests/test_llm_agent_flow.py

依赖：
  - 必须先执行过 daily-prepare，确保数据库中有数据
  - python3 tests/test_llm_agent_flow.py --date 2026-06-10   # 指定日期
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from pprint import pprint
from typing import Any

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent_tools.analysis_tools import get_previous_analysis_tool, save_analysis_tool
from agent_tools.candidate_tools import get_candidate_pool_tool, search_stocks_tool
from agent_tools.change_tools import get_change_events_tool
from agent_tools.report_tools import pool_summary_tool
from agent_tools.signal_tools import get_signal_card_tool
from agent_tools.snapshot_tools import get_stock_snapshot_tool
from storage.db import get_connection, init_db


# ─── 工具函数 ────────────────────────────────────────────────────────

SEP = "=" * 72


def _load_json(path: Path) -> dict:
    """读取 JSON 配置文件"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _print_step(step: int, title: str):
    """打印步骤标题"""
    print(f"\n{SEP}")
    print(f"  Step {step}: {title}")
    print(SEP)


def _print_result(label: str, data: Any, compact: bool = False):
    """格式化打印工具返回结果"""
    if compact and isinstance(data, dict):
        # 对大型 dict 只打印关键摘要
        keys = list(data.keys())
        if len(keys) > 10:
            summary = {k: type(v).__name__ for k, v in data.items()}
            print(f"  [{label}] {len(keys)} 个字段: {summary}")
        else:
            print(f"  [{label}] {json.dumps(data, ensure_ascii=False, indent=2)}")
    elif compact and isinstance(data, list) and len(data) > 5:
        print(f"  [{label}] 共 {len(data)} 条记录，前 3 条：")
        for item in data[:3]:
            print(f"    - {json.dumps(item, ensure_ascii=False)}")
    else:
        print(f"  [{label}] {json.dumps(data, ensure_ascii=False, indent=2, default=str)}")


# ─── 主流程 ───────────────────────────────────────────────────────────

def simulate_llm_flow(data_date: str | None = None, verbose: bool = False):
    """
    模拟 LLM Agent 完整执行流程。

    流程（对应 research_prompts.md）：
      Step 1: get_candidate_pool   → 获取今日候选池、老池、退出池、风险池
      Step 2: search_stocks        → 按市场/回撤/信号分筛选重点股票
      Step 3: get_stock_snapshot   → 逐只获取结构化快照
      Step 4: get_signal_card      → 逐只获取技术底部信号卡
      Step 5: get_change_events    → 逐只获取近期变化事件
      Step 6: get_previous_analysis→ 逐只获取最近一次分析结论
      Step 7: save_analysis        → 保存结构化研究结论（模拟）
      Step 8: pool_summary         → 获取候选池摘要
    """

    # ── 初始化数据库 ──
    config_path = PROJECT_ROOT / "config" / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        import yaml
        config = yaml.safe_load(f)

    sqlite_path = config["storage"]["sqlite_path"]
    if not Path(sqlite_path).is_absolute():
        sqlite_path = str(PROJECT_ROOT / sqlite_path)

    print(f"\n{'#' * 72}")
    print(f"#  LLM Agent 执行流程模拟测试")
    print(f"#  数据库: {sqlite_path}")
    print(f"#  数据日期: {data_date or '今天 (' + str(date.today()) + ')'}")
    print(f"{'#' * 72}")

    init_db(sqlite_path)
    conn = get_connection(sqlite_path)

    try:
        # ── Step 1: 查看池况 ──
        _print_step(1, "get_candidate_pool — 获取候选池全景")
        pool = get_candidate_pool_tool(conn, data_date=data_date)
        _print_result("候选池", {
            "date": pool.get("date"),
            "total_count": pool["total_count"],
            "新进": pool["new_candidates"],
            "老池": pool["existing_candidates"],
            "风险": pool["risk_alerts"],
            "退出": pool["removed_candidates"],
        })
        print(f"\n  → 共 {pool['total_count']} 只候选股票，"
              f"新进 {len(pool['new_candidates'])} 只，"
              f"老池 {len(pool['existing_candidates'])} 只，"
              f"风险 {len(pool['risk_alerts'])} 只，"
              f"退出 {len(pool['removed_candidates'])} 只")

        # ── Step 2: 筛选重点（分市场） ──
        _print_step(2, "search_stocks — 按条件筛选重点股票")

        # 读取观察列表了解有哪些市场
        watchlist_path = PROJECT_ROOT / "config" / "watchlist.json"
        watchlist = _load_json(watchlist_path)
        stocks_list = watchlist.get("stocks", watchlist if isinstance(watchlist, list) else [])
        markets = set(s.get("market") for s in stocks_list)
        print(f"  → 观察列表涉及市场: {markets}")

        all_search_results: dict[str, list[dict]] = {}

        for market in sorted(markets):
            filters = {
                "market": market,
                "min_drawdown": 10,       # 回撤 >= 10%
                "min_bottom_signal": 20,  # 底部信号 >= 20
            }
            print(f"\n  --- 市场: {market} | 条件: {filters} ---")
            result = search_stocks_tool(conn, filters=filters)
            _print_result(f"{market} 筛选", result, compact=True)

            for r in result:
                all_search_results[r["code"]] = r

        # 合并去重，按底部信号分排序，取前 5 只作为重点分析对象
        focus_stocks = sorted(
            all_search_results.items(),
            key=lambda x: x[1].get("bottom_signal_score", 0),
            reverse=True,
        )[:5]
        print(f"\n  → 筛选出 {len(all_search_results)} 只股票，取信号分最高的前 5 只重点分析：")
        for code, info in focus_stocks:
            print(f"    - {code} ({info.get('name')}) "
                  f"底部信号={info.get('bottom_signal_score')} "
                  f"回撤={info.get('drawdown_from_high_pct')}% "
                  f"PE={info.get('pe_ttm')}")

        # ── Step 3-6: 逐只分析重点股票 ──
        focus_codes = [code for code, _ in focus_stocks]
        all_analyses = []

        for idx, (code, info) in enumerate(focus_stocks, start=1):
            _print_step(f"3-6.{idx}", f"分析重点股票: {code} ({info.get('name', '未知')})")

            # Step 3: 快照
            snapshot = get_stock_snapshot_tool(conn, code=code, data_date=data_date)
            _print_result("get_stock_snapshot", snapshot, compact=True) if verbose else \
                print(f"  [get_stock_snapshot] {code}: "
                      f"价格={snapshot.get('close')}, "
                      f"PE={snapshot.get('pe_ttm')}, "
                      f"ROE={snapshot.get('roe')}, "
                      f"信号分={snapshot.get('bottom_signal_score')}")

            # Step 4: 信号卡
            signal_card = get_signal_card_tool(conn, code=code, data_date=data_date)
            _print_result("get_signal_card", signal_card, compact=True) if verbose else \
                print(f"  [get_signal_card] {code}: {'有信号卡' if signal_card else '无信号卡'}")

            # Step 5: 变化事件（近 30 天）
            changes = get_change_events_tool(conn, code=code, days=30)
            _print_result("get_change_events", changes, compact=True)

            # Step 6: 历史分析结论
            prev_analysis = get_previous_analysis_tool(conn, code=code)
            _print_result("get_previous_analysis", prev_analysis, compact=True) if verbose else \
                print(f"  [get_previous_analysis] {code}: "
                      f"{'有历史分析' if prev_analysis else '无历史分析（新进股票）'}")

        # ── Step 7: 生成并保存模拟分析结论 ──
        _print_step(7, "save_analysis — 生成并保存结构化研究结论（模拟 LLM 输出 → 自动评分）")

        for code, info in focus_stocks:
            # 模拟 LLM 输出的结构化分析结论
            today_str = data_date or str(date.today())
            mock_analysis = {
                "code": code,
                "analysis_date": today_str,
                "task_type": "full_analysis",
                "decision": "值得进一步研究",
                "research_priority": "high",
                "opportunity_quality": 4,
                "valuation_attractiveness": max(1, min(5, int(info.get("pe_ttm", 20)) // 10)),
                "fundamental_quality": 3,
                "risk_level": 2,
                "value_trap_probability": "medium",
                "main_positive_points": [
                    f"技术底部信号分 {info.get('bottom_signal_score')}，处于超卖区域",
                    f"当前回撤 {info.get('drawdown_from_high_pct')}%，可能处于阶段性底部",
                ],
                "main_risks": [
                    "需确认基本面是否同步改善，排除价值陷阱",
                    "关注近期财报是否有利空",
                ],
                "key_contradictions": [],
                "data_missing": [],
                "suggested_follow_up": [
                    "查阅最新季度财报",
                    "关注行业/竞品动态",
                ],
                "confidence": 0.65,
            }

            result = save_analysis_tool(conn, analysis=mock_analysis)
            all_analyses.append(mock_analysis)

            if result["success"]:
                scoring = result.get("scoring", {})
                print(f"  ✓ {code} 分析结论已保存")
                print(f"    评分: llm_score={scoring.get('llm_priority_score')} "
                      f"(base={scoring.get('base_score')}) "
                      f"| bottom={scoring.get('bottom_signal_score')} "
                      f"| attention={scoring.get('attention_score')} "
                      f"| final={scoring.get('final_priority')}")
                if scoring.get("adjustments"):
                    for adj in scoring["adjustments"]:
                        print(f"      调整: {adj['reason']} → {adj['change']:+d}")
                print(f"    紧迫度参数: {json.dumps(scoring.get('attention_params', {}), ensure_ascii=False)}")
            else:
                print(f"  ✗ {code} 保存失败: {result['errors']}")

        # ── Step 8: 池摘要 ──
        _print_step(8, "pool_summary — 查看候选池摘要")
        summary = pool_summary_tool(conn, report_date=data_date)
        _print_result("pool_summary", {
            "date": summary["date"],
            "summary": summary["summary"],
            "新进候选": summary["new_candidates"][:5] if len(summary["new_candidates"]) > 5 else summary["new_candidates"],
            "风险警报": summary["risk_alerts"],
            "高优先级 TOP5": summary["top_priority"][:5],
        })

        # ── 流程总结 ──
        print(f"\n{'#' * 72}")
        print(f"#  LLM Agent 模拟流程完成")
        print(f"#  候选池: {pool['total_count']} 只股票")
        print(f"#  筛选: {len(all_search_results)} 只进入条件池")
        print(f"#  重点分析: {len(focus_codes)} 只 ({', '.join(focus_codes)})")
        print(f"#  保存分析: {len(all_analyses)} 份结论")
        print(f"#  池摘要: {summary['summary']}")
        print(f"{'#' * 72}")

        return 0

    finally:
        conn.close()


# ─── CLI 入口 ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="模拟 LLM Agent 完整执行流程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 tests/test_llm_agent_flow.py
  python3 tests/test_llm_agent_flow.py --date 2026-06-10
  python3 tests/test_llm_agent_flow.py --verbose
""",
    )
    parser.add_argument(
        "--date", "-d",
        help="数据日期 YYYY-MM-DD，默认今天",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出所有工具返回的完整内容",
    )
    parser.add_argument(
        "--db-path",
        help="覆盖配置中的 SQLite 路径",
    )
    args = parser.parse_args()

    return simulate_llm_flow(data_date=args.date, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
