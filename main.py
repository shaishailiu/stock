"""
CLI 入口

命令:
  python main.py init-history --markets hk,us,cn
  python main.py daily-prepare --date 2026-06-10
  python main.py run-agent --date 2026-06-10
  python main.py generate-report --date 2026-06-10
"""

import argparse
import logging
import logging.config
import os
import sys
from datetime import date
from pathlib import Path

import yaml

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from pipelines.daily_prepare import run_daily_prepare
from pipelines.init_history import run_init_history
from storage.db import get_connection, init_db
from agent_tools.report_tools import generate_report_tool
from agent.stock_agent import StockResearchAgent


def setup_logging():
    """初始化日志"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    config_path = Path("config/logging.yaml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        logging.config.dictConfig(config)
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )


def cmd_init_history(args):
    """首次历史数据初始化"""
    setup_logging()
    markets = None
    if args.markets:
        markets = [m.strip().lower() for m in args.markets.split(",")]

    print(f"Initializing history for markets: {markets or 'all enabled'}")
    result = run_init_history(markets=markets)

    print(f"\nDone. Fetched: {len(result['fetched'])} symbols")
    if result["errors"]:
        print(f"Errors: {len(result['errors'])}")
        for err in result["errors"]:
            print(f"  - {err}")


def cmd_daily_prepare(args):
    """每日预处理"""
    setup_logging()
    target_date = args.date or str(date.today())
    print(f"Running daily prepare for: {target_date}")

    result = run_daily_prepare(target_date=target_date)

    print(f"\n=== Prepare Summary ===")
    print(f"Date: {result['data_date']}")
    print(f"Markets: {result['markets']}")
    print(f"Updated symbols: {result['updated_symbols']}")
    print(f"New candidates: {result['new_candidates']}")
    print(f"Existing candidates: {result['existing_candidates']}")
    print(f"Risk alerts: {result['risk_alerts']}")

    if result.get("errors"):
        print(f"\nErrors ({len(result['errors'])}):")
        for err in result["errors"]:
            print(f"  - {err}")


def cmd_run_agent(args):
    """启动 Agent 研究流程"""
    setup_logging()
    target_date = args.date or str(date.today())

    agent = StockResearchAgent()
    stats = agent.run(target_date=target_date)

    print(f"\n=== Agent Research Complete ===")
    print(f"Tool calls: {stats.get('tool_calls', 0)}")
    print(f"Stocks analyzed: {stats.get('total_analyzed', 0)}")
    print(f"Rounds: {stats.get('rounds', 0)}")
    if stats.get("error"):
        print(f"Error: {stats['error']}")


def cmd_generate_report(args):
    """生成日报"""
    setup_logging()
    report_date = args.date or str(date.today())

    # 读取数据库路径
    with open("config/config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    db_path = config["storage"]["sqlite_path"]

    init_db(db_path)
    conn = get_connection(db_path)

    report = generate_report_tool(conn, report_date)
    conn.close()

    print(f"\n=== 股票研究优先级报告 ===")
    print(f"日期: {report['date']}")
    print(f"候选池总数: {report['summary']['total_pool_count']}")
    print(f"今日新增: {report['summary']['new_candidates']}")
    print(f"风险警报: {report['summary']['risk_alerts']}")

    if report.get("top_priority"):
        print(f"\n--- 今日最值得关注 ---")
        for i, stock in enumerate(report["top_priority"], 1):
            print(f"  {i}. {stock['code']} {stock.get('name', '')} "
                  f"[{stock.get('market', '')}] "
                  f"信号分:{stock.get('bottom_signal_score', 0)} "
                  f"优先级:{stock.get('research_priority', 'N/A')}")

    if report.get("new_candidates"):
        print(f"\n--- 今日新增候选 ---")
        for stock in report["new_candidates"]:
            print(f"  {stock['code']} {stock.get('name', '')} "
                  f"回撤:{stock.get('drawdown_from_high_pct', 'N/A')}%")

    if report.get("risk_alerts"):
        print(f"\n--- 风险警报 ---")
        for stock in report["risk_alerts"]:
            print(f"  {stock['code']} {stock.get('name', '')}")

    # 输出 JSON 报告文件
    import json
    report_dir = config.get("report", {}).get("output_dir", "reports")
    Path(report_dir).mkdir(exist_ok=True)
    report_path = Path(report_dir) / f"report_{report_date}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已保存: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="股票 LLM 价值筛选系统")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # init-history
    p_init = subparsers.add_parser("init-history", help="首次历史数据初始化")
    p_init.add_argument("--markets", type=str, help="市场列表，逗号分隔 (hk,us,cn)")

    # daily-prepare
    p_prepare = subparsers.add_parser("daily-prepare", help="每日预处理")
    p_prepare.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    # run-agent
    p_agent = subparsers.add_parser("run-agent", help="启动 LLM Agent 研究流程")
    p_agent.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    # generate-report
    p_report = subparsers.add_parser("generate-report", help="生成日报")
    p_report.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    args = parser.parse_args()

    if args.command == "init-history":
        cmd_init_history(args)
    elif args.command == "daily-prepare":
        cmd_daily_prepare(args)
    elif args.command == "run-agent":
        cmd_run_agent(args)
    elif args.command == "generate-report":
        cmd_generate_report(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
