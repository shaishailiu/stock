"""
CLI 入口

命令:
  python main.py init-history --markets hk,us,cn
  python main.py daily-prepare --date 2026-06-10
  python main.py show-prompt
  python main.py pool-summary --date 2026-06-10
  python main.py generate-report --date 2026-06-10
  python main.py push-report --date 2026-06-10
"""

import argparse
import json
import logging
import logging.config
import os
import sys
from datetime import date
from pathlib import Path

# yaml 作为可选依赖，仅在需要时导入
def _import_yaml():
    import yaml
    return yaml

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from pipelines.daily_prepare import run_daily_prepare
from pipelines.init_history import run_init_history
from storage.db import get_connection, init_db
from agent_tools.report_tools import pool_summary_tool, generate_report_tool
from agent_tools.push_tools import push_report_tool
from report.generator import format_console, generate_daily_report


def setup_logging():
    """初始化日志"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    config_path = Path("config/logging.yaml")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = _import_yaml().safe_load(f)
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


def cmd_show_prompt(args):
    """输出 Agent 提示词"""
    setup_logging()
    prompt_path = Path("agent/research_prompts.md")
    if not prompt_path.exists():
        print(f"Error: prompt file not found: {prompt_path}", file=sys.stderr)
        sys.exit(1)
    print(prompt_path.read_text(encoding="utf-8"))


def cmd_pool_summary(args):
    """候选池摘要"""
    setup_logging()
    report_date = args.date or str(date.today())

    # 读取数据库路径
    with open("config/config.yaml", "r", encoding="utf-8") as f:
        config = _import_yaml().safe_load(f)
    db_path = config["storage"]["sqlite_path"]

    init_db(db_path)
    conn = get_connection(db_path)

    summary = pool_summary_tool(conn, report_date)
    conn.close()

    print(f"\n=== 候选池摘要 ===")
    print(f"日期: {summary['date']}")
    print(f"候选池总数: {summary['summary']['total_pool_count']}")
    print(f"今日新增: {summary['summary']['new_candidates']}")
    print(f"风险警报: {summary['summary']['risk_alerts']}")

    if summary.get("top_priority"):
        print(f"\n--- 今日最值得关注 ---")
        for i, stock in enumerate(summary["top_priority"], 1):
            print(f"  {i}. {stock['code']} {stock.get('name', '')} "
                  f"[{stock.get('market', '')}] "
                  f"信号分:{stock.get('bottom_signal_score', 0)} "
                  f"优先级:{stock.get('research_priority', 'N/A')}")

    if summary.get("new_candidates"):
        print(f"\n--- 今日新增候选 ---")
        for stock in summary["new_candidates"]:
            print(f"  {stock['code']} {stock.get('name', '')} "
                  f"回撤:{stock.get('drawdown_from_high_pct', 'N/A')}%")

    if summary.get("risk_alerts"):
        print(f"\n--- 风险警报 ---")
        for stock in summary["risk_alerts"]:
            print(f"  {stock['code']} {stock.get('name', '')}")

    # 输出 JSON 摘要文件
    import json
    report_dir = config.get("report", {}).get("output_dir", "reports")
    Path(report_dir).mkdir(exist_ok=True)
    summary_path = Path(report_dir) / f"pool_summary_{report_date}.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n摘要已保存: {summary_path}")


def cmd_generate_report(args):
    """生成每日研究报告"""
    setup_logging()
    report_date = args.date or str(date.today())

    # 读取配置
    with open("config/config.yaml", "r", encoding="utf-8") as f:
        config = _import_yaml().safe_load(f)
    db_path = config["storage"]["sqlite_path"]

    init_db(db_path)
    conn = get_connection(db_path)

    output_dir = config.get("report", {}).get("output_dir", "reports")

    try:
        result = generate_report_tool(conn, report_date, output_dir)

        # 打印终端预览
        report_data = generate_daily_report(conn, report_date)
        console_text = format_console(report_data)
        print(console_text)

        print(f"\n报告已保存:")
        for fmt, path in result.get("files", {}).items():
            print(f"  [{fmt.upper()}] {path}")

        # 生成企业微信推送版（去除市场概览、新进候选、移除候选）
        from report.generator import format_markdown
        push_md = format_markdown(report_data, push_mode=True)
        push_path = Path(output_dir) / f"daily_{report_date}_push.md"
        push_path.write_text(push_md, encoding="utf-8")
        print(f"  [PUSH] {push_path}")

    finally:
        conn.close()


def cmd_push_report(args):
    """推送每日研究报告到企业微信（分段推送）"""
    setup_logging()
    report_date = args.date or str(date.today())

    # 读取配置
    with open("config/config.yaml", "r", encoding="utf-8") as f:
        config = _import_yaml().safe_load(f)
    db_path = config["storage"]["sqlite_path"]

    init_db(db_path)
    conn = get_connection(db_path)

    try:
        result = push_report_tool(
            conn=conn,
            report_date=report_date,
            config_path="config/config.yaml",
        )

        print(f"\n=== 推送结果 ===")
        print(f"日期: {result['date']}")
        print(f"消息总数: {result.get('total_messages', 0)}")
        print(f"成功: {result.get('success_count', 0)}")
        print(f"失败: {result.get('failed_count', 0)}")

        if result.get("error"):
            print(f"\n错误: {result['error']}")

        for r in result.get("results", []):
            status = "✅" if r["success"] else "❌"
            detail = f" ({r.get('bytes', '?')}B)" if r["success"] else f" — {r.get('error', r.get('errmsg', '?'))}"
            print(f"  {status} 第{r['index']}条{detail}")

    finally:
        conn.close()


def print_detailed_help(parser):
    """打印详细帮助信息，包含每个子命令的参数说明和示例。"""
    print("=" * 60)
    print("  股票 LLM 价值筛选系统")
    print("=" * 60)
    print()
    print("用法: python main.py <命令> [参数]")
    print()

    # 子命令定义（(命令名, 描述, 参数列表, 示例)）
    commands = (
        (
            "init-history",
            "首次历史数据初始化",
            (
                ("--markets", "市场列表，逗号分隔 (hk,us,cn)，不指定则初始化所有已启用的市场", True),
            ),
            "python main.py init-history --markets hk,us,cn",
        ),
        (
            "daily-prepare",
            "每日预处理：拉取行情数据、计算技术指标、筛选候选池",
            (
                ("--date", "日期 YYYY-MM-DD，不指定则默认今天", True),
            ),
            "python main.py daily-prepare --date 2026-06-10",
        ),
        (
            "pool-summary",
            "候选池摘要：从数据库查询候选池状态，按优先级排序输出 top 10、新增候选和风险警报",
            (
                ("--date", "日期 YYYY-MM-DD，不指定则默认今天", True),
            ),
            "python main.py pool-summary --date 2026-06-10",
        ),
        (
            "generate-report",
            "生成每日研究报告：含市场概览、候选池总览、重点观察 Top 10、新进/风险/移除详情、个股分析\n"
            "              输出 reports/daily_YYYY-MM-DD.md 和 reports/daily_YYYY-MM-DD.json\n"
            "              同时生成企业微信精简版 reports/daily_YYYY-MM-DD_push.md",
            (
                ("--date", "日期 YYYY-MM-DD，不指定则默认今天", True),
            ),
            "python main.py generate-report --date 2026-06-10",
        ),
        (
            "push-report",
            "推送每日研究报告到企业微信机器人（markdown_v2 格式）\n"
            "              读取 reports/daily_YYYY-MM-DD_push.md 通过 webhook 发送",
            (
                ("--date", "日期 YYYY-MM-DD，不指定则默认今天", True),
            ),
            "python main.py push-report --date 2026-06-10",
        ),
        (
            "show-prompt",
            "输出 LLM Agent 系统提示词到终端",
            (),
            "python main.py show-prompt",
        ),
        (
            "help",
            "显示此详细帮助信息",
            (),
            "python main.py help",
        ),
    )

    print("命令列表:")
    print("-" * 60)
    for cmd_name, desc, params, example in commands:
        print(f"  {cmd_name}")
        print(f"    {desc}")
        if params:
            print(f"    参数:")
            for param_name, param_help, _ in params:
                print(f"      {param_name:<14} {param_help}")
        print()
        print(f"    示例: {example}")
        print()

    print("-" * 60)
    print("提示：在每个子命令后加 --help 可查看该命令的参数帮助。")
    print(f"      例: python main.py daily-prepare --help")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="股票 LLM 价值筛选系统")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # init-history
    p_init = subparsers.add_parser("init-history", help="首次历史数据初始化")
    p_init.add_argument("--markets", type=str, help="市场列表，逗号分隔 (hk,us,cn)")

    # daily-prepare
    p_prepare = subparsers.add_parser("daily-prepare", help="每日预处理")
    p_prepare.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    # show-prompt
    p_prompt = subparsers.add_parser("show-prompt", help="输出 Agent 系统提示词")

    # pool-summary
    p_summary = subparsers.add_parser("pool-summary", help="候选池摘要")
    p_summary.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    # generate-report
    p_report = subparsers.add_parser("generate-report", help="生成每日研究报告")
    p_report.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    # push-report
    p_push = subparsers.add_parser("push-report", help="推送每日研究报告到企业微信")
    p_push.add_argument("--date", type=str, help="日期 YYYY-MM-DD")

    # help
    subparsers.add_parser("help", help="显示详细帮助信息")

    args = parser.parse_args()

    if args.command == "init-history":
        cmd_init_history(args)
    elif args.command == "daily-prepare":
        cmd_daily_prepare(args)
    elif args.command == "show-prompt":
        cmd_show_prompt(args)
    elif args.command == "pool-summary":
        cmd_pool_summary(args)
    elif args.command == "generate-report":
        cmd_generate_report(args)
    elif args.command == "push-report":
        cmd_push_report(args)
    elif args.command == "help":
        print_detailed_help(parser)
    else:
        print_detailed_help(parser)


if __name__ == "__main__":
    main()
