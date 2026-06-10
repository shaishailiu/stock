"""
股票研究 Agent：LLM 驱动研究流程
"""

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

from agent.llm_client import LLMClient
from agent.research_prompts import AGENT_SYSTEM_PROMPT
from agent.tools_adapter import ToolExecutor, TOOL_DEFINITIONS

logger = logging.getLogger("newstock.agent")


class StockResearchAgent:
    """股票研究 Agent"""

    MAX_TOOL_ROUNDS = 30  # 最大工具调用轮数

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = self._load_config(config_path)
        self.db_path = self.config["storage"]["sqlite_path"]

        llm_cfg = self.config.get("llm", {})
        self.client = LLMClient(
            api_key=llm_cfg.get("api_key", ""),
            base_url=llm_cfg.get("base_url", "https://api.openai.com/v1"),
            model=llm_cfg.get("model", "gpt-4o"),
            temperature=llm_cfg.get("temperature", 0.3),
            max_tokens=llm_cfg.get("max_tokens", 4096),
        )
        self.tool_executor = ToolExecutor(self.db_path)

    def _load_config(self, config_path: str) -> dict:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def run(self, target_date: Optional[str] = None) -> dict:
        """
        运行 Agent 研究流程。

        返回研究结果摘要。
        """
        if target_date is None:
            target_date = str(date.today())

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"今天是 {target_date}。请开始每日股票研究优先级排序流程。先查看今日候选池情况，然后确定需要分析的股票。",
            },
        ]

        logger.info(f"Agent starting research for {target_date}")
        print(f"\n{'='*60}")
        print(f"  Agent 股票研究引擎启动 - {target_date}")
        print(f"{'='*60}\n")

        stats = {"tool_calls": 0, "total_analyzed": 0}
        round_num = 0

        try:
            while round_num < self.MAX_TOOL_ROUNDS:
                round_num += 1

                response = self.client.chat(messages, tools=TOOL_DEFINITIONS)

                if "tool_calls" in response:
                    # 处理工具调用
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                                },
                            }
                            for tc in response["tool_calls"]
                        ],
                    })

                    for tc in response["tool_calls"]:
                        tool_name = tc["name"]
                        tool_args = tc["arguments"]

                        print(f"  🔧 {tool_name}({_format_args(tool_args)})", end="")

                        try:
                            result_str = self.tool_executor.execute(tool_name, tool_args)
                            result = json.loads(result_str) if isinstance(result_str, str) else result_str
                            print(f" ✓")
                        except Exception as e:
                            result = {"error": str(e)}
                            print(f" ✗ {e}")

                        stats["tool_calls"] += 1

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        })

                        # 检查是否结束
                        if tool_name == "finalize_research" and result.get("status") == "research_complete":
                            stats["total_analyzed"] = result.get("total_analyzed", 0)
                            print(f"\n  ✓ 研究完成: {result.get('summary', '')}")
                            round_num = self.MAX_TOOL_ROUNDS  # 退出循环
                            break

                else:
                    # LLM 文本回复
                    content = response.get("content", "")
                    messages.append({"role": "assistant", "content": content})

                    if content:
                        # 尝试提取 JSON
                        json_blocks = _extract_json_blocks(content)
                        if json_blocks:
                            for jb in json_blocks:
                                if jb.get("task_type") and jb.get("research_priority"):
                                    print(f"  📊 分析完成: {jb.get('code')}")
                                    stats["total_analyzed"] += 1
                        else:
                            print(f"  💬 {content[:200]}...")

                    # 如果没有工具调用且没有 JSON，可能结束了
                    if round_num >= 5:
                        # 给足够的机会让 agent 工作
                        logger.info("Agent finished (no more tool calls)")
                        break

            stats["rounds"] = round_num

        except KeyboardInterrupt:
            print("\n  ⚠️  用户中断")
            stats["interrupted"] = True
        except Exception as e:
            logger.exception("Agent error")
            stats["error"] = str(e)
        finally:
            self.tool_executor.close()

        return stats


def _format_args(args: dict) -> str:
    """格式化参数用于显示"""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        if isinstance(v, list):
            parts.append(f"{k}=[{len(v)} items]")
        elif isinstance(v, str) and len(v) > 50:
            parts.append(f"{k}={v[:50]}...")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def _extract_json_blocks(text: str) -> list[dict]:
    """从文本中提取 JSON 块"""
    results = []
    # 尝试找 ```json ... ``` 块
    import re
    blocks = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    for block in blocks:
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                results.append(obj)
            elif isinstance(obj, list):
                results.extend(obj)
        except json.JSONDecodeError:
            pass

    # 尝试直接解析整段 JSON
    if not results:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                results.append(obj)
            elif isinstance(obj, list):
                results.extend(obj)
        except json.JSONDecodeError:
            pass

    return results
