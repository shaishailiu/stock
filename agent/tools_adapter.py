"""
Agent 工具适配器：将 Python 工具暴露为 LLM 可调用的 Function Tools
"""

import json
import sqlite3
from typing import Any, Optional

# ---- 工具 JSON Schema 定义 ----

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_candidate_pool",
            "description": "获取今日候选池总览，包括新进、老池、退出和风险警报股票",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "日期 YYYY-MM-DD，不传则用最新日期",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_snapshot",
            "description": "获取单只股票的结构化事实快照（价格、估值、财务、风险、数据质量）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码，如 00700.HK / AAPL.US / 600519.SH",
                    },
                    "date": {
                        "type": "string",
                        "description": "日期 YYYY-MM-DD，不传则用最新日期",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_signal_card",
            "description": "获取技术底部信号卡（底部信号分、信号等级、触发原因）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码",
                    },
                    "date": {
                        "type": "string",
                        "description": "日期 YYYY-MM-DD，不传则用最新日期",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_change_events",
            "description": "获取股票近期变化事件列表（价格/估值/财务/风险/资金变化）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码",
                    },
                    "days": {
                        "type": "integer",
                        "description": "回溯天数，默认 30",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_previous_analysis",
            "description": "获取股票最近一次 Agent 研究结论，用于复用或参考历史分析",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_stocks",
            "description": "按条件筛选股票（市场、回撤、RSI、底部信号、PE等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "description": "市场: HK/US/CN",
                    },
                    "min_drawdown": {
                        "type": "number",
                        "description": "最小回撤幅度(%)",
                    },
                    "max_rsi": {
                        "type": "number",
                        "description": "最大RSI值",
                    },
                    "min_bottom_signal": {
                        "type": "integer",
                        "description": "最小底部信号分",
                    },
                    "max_pe_ttm": {
                        "type": "number",
                        "description": "最大PE TTM",
                    },
                    "industry": {
                        "type": "string",
                        "description": "行业关键字",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_analysis",
            "description": "保存单只股票的 Agent 研究结论到数据库",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码",
                    },
                    "task_type": {
                        "type": "string",
                        "description": "任务类型: full_analysis / incremental_analysis / reuse_previous / reject / full_reanalysis / refresh_analysis",
                    },
                    "decision": {
                        "type": "string",
                        "description": "研究判断结论",
                    },
                    "research_priority": {
                        "type": "string",
                        "description": "研究优先级: very_high / high / medium / low / reject",
                    },
                    "opportunity_quality": {
                        "type": "integer",
                        "description": "机会质量 1-5",
                    },
                    "valuation_attractiveness": {
                        "type": "integer",
                        "description": "估值吸引力 1-5",
                    },
                    "fundamental_quality": {
                        "type": "integer",
                        "description": "基本面质量 1-5",
                    },
                    "risk_level": {
                        "type": "integer",
                        "description": "风险等级 1-5（越高风险越大）",
                    },
                    "value_trap_probability": {
                        "type": "string",
                        "description": "价值陷阱概率: low / medium / high",
                    },
                    "main_positive_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "正面因素列表",
                    },
                    "main_risks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "主要风险列表",
                    },
                    "key_contradictions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关键矛盾信号",
                    },
                    "data_missing": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "缺失数据字段列表",
                    },
                    "suggested_follow_up": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "后续观察点",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "置信度 0-1",
                    },
                },
                "required": ["code", "task_type", "research_priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_report",
            "description": "根据当前数据库中的候选池和分析结果，生成最终研究优先级日报",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "日期 YYYY-MM-DD，不传则用最新日期",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_research",
            "description": "标记当次研究流程结束。调用此函数表示已对所有需要分析的股票完成了判断。调用后系统将输出研究统计摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "本次研究流程的简要总结",
                    },
                    "total_analyzed": {
                        "type": "integer",
                        "description": "本次分析了多少只股票",
                    },
                },
                "required": ["summary", "total_analyzed"],
            },
        },
    },
]


# ---- 工具执行器 ----

class ToolExecutor:
    """工具执行器：将 LLM 工具调用请求路由到实际 Python 函数"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            from storage.db import get_connection
            self.conn = get_connection(self.db_path)
        return self.conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute(self, tool_name: str, arguments: dict) -> str:
        """执行工具并返回结果 JSON 字符串"""
        try:
            result = self._dispatch(tool_name, arguments)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _dispatch(self, tool_name: str, args: dict) -> Any:
        conn = self._get_conn()

        if tool_name == "get_candidate_pool":
            from agent_tools.candidate_tools import get_candidate_pool_tool
            return get_candidate_pool_tool(conn, args.get("date"))

        elif tool_name == "get_stock_snapshot":
            from agent_tools.snapshot_tools import get_stock_snapshot_tool
            result = get_stock_snapshot_tool(conn, args["code"], args.get("date"))
            if result is None:
                return {"error": f"No snapshot found for {args['code']}"}
            return _format_snapshot_for_llm(result)

        elif tool_name == "get_signal_card":
            from agent_tools.signal_tools import get_signal_card_tool
            result = get_signal_card_tool(conn, args["code"], args.get("date"))
            if result is None:
                return {"error": f"No signal card found for {args['code']}"}
            return result

        elif tool_name == "get_change_events":
            from agent_tools.change_tools import get_change_events_tool
            return get_change_events_tool(conn, args["code"], args.get("days", 30))

        elif tool_name == "get_previous_analysis":
            from agent_tools.analysis_tools import get_previous_analysis_tool
            result = get_previous_analysis_tool(conn, args["code"])
            if result is None:
                return {"message": f"No previous analysis for {args['code']}"}
            return {
                "code": result.get("code"),
                "analysis_date": result.get("analysis_date"),
                "task_type": result.get("task_type"),
                "decision": result.get("decision"),
                "research_priority": result.get("research_priority"),
                "opportunity_quality": result.get("opportunity_quality"),
                "valuation_attractiveness": result.get("valuation_attractiveness"),
                "fundamental_quality": result.get("fundamental_quality"),
                "risk_level": result.get("risk_level"),
                "value_trap_probability": result.get("value_trap_probability"),
                "confidence": result.get("confidence"),
                "main_logic": result.get("main_logic"),
                "main_positive_points": json.loads(result.get("main_positive_points_json", "[]")) if result.get("main_positive_points_json") else [],
                "main_risks": json.loads(result.get("main_risks_json", "[]")) if result.get("main_risks_json") else [],
                "key_contradictions": json.loads(result.get("key_contradictions_json", "[]")) if result.get("key_contradictions_json") else [],
            }

        elif tool_name == "search_stocks":
            from agent_tools.candidate_tools import search_stocks_tool
            results = search_stocks_tool(conn, args)
            return [{"code": r.get("code"), "name": r.get("name"), "market": r.get("market"),
                     "drawdown_from_high_pct": r.get("drawdown_from_high_pct"),
                     "bottom_signal_score": r.get("bottom_signal_score"),
                     "pe_ttm": r.get("pe_ttm"), "rsi_14": r.get("rsi_14")}
                    for r in results]

        elif tool_name == "save_analysis":
            from datetime import date
            from processing.validators import validate_llm_output
            from agent_tools.scoring_tools import (
                map_llm_priority_score_tool,
                calc_final_priority_tool,
            )

            # 校验
            errors = validate_llm_output(args)
            if errors:
                return {"success": False, "errors": errors}

            # 映射分数
            score_result = map_llm_priority_score_tool(args)
            llm_score = score_result["llm_priority_score"]

            # 获取底部信号分
            bottom = 0
            try:
                from storage.repositories import get_signal_card
                card = get_signal_card(conn, args["code"])
                if card:
                    bottom = card.get("bottom_signal_score", 0)
            except Exception:
                pass

            final = calc_final_priority_tool(llm_score, bottom)

            full_analysis = {
                "code": args["code"],
                "analysis_date": str(date.today()),
                "task_type": args.get("task_type"),
                "decision": args.get("decision"),
                "research_priority": args.get("research_priority"),
                "opportunity_quality": args.get("opportunity_quality"),
                "valuation_attractiveness": args.get("valuation_attractiveness"),
                "fundamental_quality": args.get("fundamental_quality"),
                "risk_level": args.get("risk_level"),
                "value_trap_probability": args.get("value_trap_probability"),
                "confidence": args.get("confidence"),
                "llm_priority_score": llm_score,
                "bottom_signal_score": bottom,
                "attention_score": 0,
                "final_priority": final,
                "main_logic": args.get("decision", ""),
                "main_positive_points": args.get("main_positive_points", []),
                "main_risks": args.get("main_risks", []),
                "key_contradictions": args.get("key_contradictions", []),
                "data_missing": args.get("data_missing", []),
                "suggested_follow_up": args.get("suggested_follow_up", []),
                "raw_llm_output": args,
            }

            from agent_tools.analysis_tools import save_analysis_tool
            result = save_analysis_tool(conn, full_analysis)
            return {
                "success": result["success"],
                "errors": result.get("errors", []),
                "llm_priority_score": llm_score,
                "final_priority": final,
            }

        elif tool_name == "generate_report":
            from agent_tools.report_tools import generate_report_tool
            return generate_report_tool(conn, args.get("date"))

        elif tool_name == "finalize_research":
            return {"status": "research_complete", "summary": args.get("summary", ""), "total_analyzed": args.get("total_analyzed", 0)}

        return {"error": f"Unknown tool: {tool_name}"}


def _format_snapshot_for_llm(snapshot: dict) -> dict:
    """精简快照，只保留 LLM 需要的字段"""
    return {
        "code": snapshot.get("code"),
        "name": snapshot.get("name"),
        "market": snapshot.get("market"),
        "industry": snapshot.get("industry"),
        "price_signal": snapshot.get("price_signal"),
        "valuation": snapshot.get("valuation"),
        "fundamental": _json_field(snapshot.get("financial_summary_json")),
        "balance_sheet": _json_field(snapshot.get("balance_summary_json")),
        "cashflow": _json_field(snapshot.get("cashflow_summary_json")),
        "risk": {
            "risk_flags": _json_field(snapshot.get("risk_flags_json")),
        },
        "capital_flow": _json_field(snapshot.get("capital_flow_json")),
        "data_quality": {
            "data_missing": _json_field(snapshot.get("data_missing_json")),
            "data_stale": _json_field(snapshot.get("data_stale_json")),
            "data_estimated": _json_field(snapshot.get("data_estimated_json")),
            "source_apis": _json_field(snapshot.get("source_apis_json")),
        },
    }


def _json_field(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val
