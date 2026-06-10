"""
Agent 工具命令行执行入口。

示例：
  python3 agent_tools/tool_runner.py --tool get_candidate_pool -p '{"data_date":"2026-06-10"}'
  python3 agent_tools/tool_runner.py --tool search_stocks -p '{"filters":{"market":"HK","min_bottom_signal":70}}'
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

# 允许直接执行 python3 agent_tools/tool_runner.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_tools.analysis_tools import get_previous_analysis_tool, save_analysis_tool
from agent_tools.candidate_tools import get_candidate_pool_tool, search_stocks_tool
from agent_tools.change_tools import get_change_events_tool
from agent_tools.report_tools import generate_report_tool
from agent_tools.signal_tools import get_signal_card_tool
from agent_tools.snapshot_tools import get_stock_snapshot_tool
from storage.db import get_connection, init_db

logger = logging.getLogger("newstock.agent_tools.tool_runner")

ToolFunc = Callable[..., Any]

TOOL_MAP: dict[str, ToolFunc] = {
    "get_candidate_pool": get_candidate_pool_tool,
    "search_stocks": search_stocks_tool,
    "get_stock_snapshot": get_stock_snapshot_tool,
    "get_signal_card": get_signal_card_tool,
    "get_change_events": get_change_events_tool,
    "get_previous_analysis": get_previous_analysis_tool,
    "save_analysis": save_analysis_tool,
    "generate_report": generate_report_tool,
}


def _json_response(success: bool, tool: str | None, **kwargs: Any) -> str:
    payload = {"success": success, "tool": tool}
    payload.update(kwargs)
    return json.dumps(payload, ensure_ascii=False, default=str)


def _load_params(raw_params: str | None) -> dict[str, Any]:
    if not raw_params:
        return {}

    params = json.loads(raw_params)
    if not isinstance(params, dict):
        raise ValueError("--params/-p 必须是 JSON object")
    return params


def _load_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        return _load_minimal_config(config_path)

    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_minimal_config(config_path: Path) -> dict[str, Any]:
    """在 PyYAML 未安装时，只解析本工具需要的 storage.sqlite_path。"""
    in_storage = False
    for line in config_path.read_text(encoding="utf-8").splitlines():
        raw = line.rstrip()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not raw.startswith(" ") and stripped == "storage:":
            in_storage = True
            continue
        if in_storage and not raw.startswith(" "):
            break
        if in_storage and stripped.startswith("sqlite_path:"):
            value = stripped.split(":", 1)[1].split("#", 1)[0].strip().strip('"\'')
            return {"storage": {"sqlite_path": value}}
    return {}


def _load_db_path(config_path: Path, db_path: str | None) -> str:
    if db_path:
        path = Path(db_path)
        return str(path if path.is_absolute() else PROJECT_ROOT / path)

    config = _load_config(config_path)
    configured_path = config.get("storage", {}).get("sqlite_path")
    if not configured_path:
        raise ValueError(f"配置文件缺少 storage.sqlite_path: {config_path}")

    path = Path(configured_path)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


def run_tool(tool_name: str, params: dict[str, Any], config_path: Path, db_path: str | None) -> Any:
    if tool_name not in TOOL_MAP:
        available = ", ".join(sorted(TOOL_MAP))
        raise ValueError(f"未知工具: {tool_name}; 可用工具: {available}")

    sqlite_path = _load_db_path(config_path, db_path)
    init_db(sqlite_path)

    conn = get_connection(sqlite_path)
    try:
        return TOOL_MAP[tool_name](conn=conn, **params)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行 Agent 工具并输出 JSON 结果")
    parser.add_argument("--tool", required=True, choices=sorted(TOOL_MAP), help="要执行的工具名")
    parser.add_argument("-p", "--params", default="{}", help="JSON 参数对象，不包含 conn")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config" / "config.yaml"),
        help="配置文件路径，默认使用 config/config.yaml",
    )
    parser.add_argument("--db-path", help="覆盖配置中的 SQLite 路径")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        params = _load_params(args.params)
        config_path = Path(args.config)
        if not config_path.is_absolute():
            config_path = PROJECT_ROOT / config_path
        data = run_tool(args.tool, params, config_path, args.db_path)
        print(_json_response(True, args.tool, data=data))
        return 0
    except Exception as exc:
        logger.exception("Agent tool failed")
        print(_json_response(False, getattr(args, "tool", None), error=str(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
