"""
WorkBuddy Agent 入口

提供 System Prompt + Tool Definitions + Tool Executor，
供 WorkBuddy 作为股票研究 Agent 的入口。
"""

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from agent.research_prompts import AGENT_SYSTEM_PROMPT
from agent.tools_adapter import TOOL_DEFINITIONS, ToolExecutor

# 全局 tool executor 实例（懒加载）
_executor: Optional[ToolExecutor] = None


def _get_executor() -> ToolExecutor:
    global _executor
    if _executor is None:
        config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        db_path = config["storage"]["sqlite_path"]
        _executor = ToolExecutor(db_path)
    return _executor


def get_system_prompt() -> str:
    """返回 Agent 系统提示词"""
    today = str(date.today())
    return AGENT_SYSTEM_PROMPT + f"\n\n当前日期: {today}"


def get_tool_definitions() -> list[dict]:
    """返回 Function Tool 定义列表（OpenAI 兼容格式）"""
    return TOOL_DEFINITIONS


def execute_tool(tool_name: str, arguments: dict) -> dict:
    """执行工具并返回结果字典"""
    executor = _get_executor()
    result_str = executor.execute(tool_name, arguments)
    return json.loads(result_str) if isinstance(result_str, str) else result_str
