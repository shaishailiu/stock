"""
Agent 工具：评分映射
"""

import logging
from typing import Any

logger = logging.getLogger("newstock.agent_tools.scoring")

# research_priority -> 基础分
PRIORITY_BASE_SCORE = {
    "very_high": 90,
    "high": 75,
    "medium": 55,
    "low": 35,
    "reject": 15,
}


def map_llm_priority_score_tool(llm_output: dict) -> dict:
    """
    将 Agent 结构化判断映射为 llm_priority_score。

    映射规则：
    - research_priority 对应基础分
    - value_trap_probability=high: 扣分封顶
    - risk_level>=4: 扣分
    - confidence<0.6: 扣分
    - 数据缺失严重: 扣分
    - opportunity/fundamental/valuation 较高: 小幅加分
    """
    priority = llm_output.get("research_priority", "medium")
    base = PRIORITY_BASE_SCORE.get(priority, 55)
    score = base
    adjustments = []

    # 价值陷阱概率高 -> 封顶 35
    if llm_output.get("value_trap_probability") == "high":
        adjustments.append({"reason": "价值陷阱概率高", "change": min(35 - score, 0)})
        score = min(score, 35)

    # 风险等级高 -> 扣分
    risk = llm_output.get("risk_level", 0)
    if risk >= 4:
        penalty = -10 if risk == 4 else -20
        adjustments.append({"reason": f"风险等级 {risk}", "change": penalty})
        score = max(10, score + penalty)

    # 置信度低 -> 扣分
    confidence = llm_output.get("confidence", 0.7)
    if confidence < 0.6:
        penalty = -10
        adjustments.append({"reason": f"置信度 {confidence:.2f}", "change": penalty})
        score = max(10, score + penalty)

    # 数据缺失多 -> 扣分
    missing = llm_output.get("data_missing", [])
    if len(missing) > 3:
        penalty = -5
        adjustments.append({"reason": f"缺失字段 {len(missing)} 个", "change": penalty})
        score = max(10, score + penalty)

    # 分析质量高 -> 小幅加分
    quality_fields = {
        "opportunity_quality": llm_output.get("opportunity_quality", 0),
        "fundamental_quality": llm_output.get("fundamental_quality", 0),
        "valuation_attractiveness": llm_output.get("valuation_attractiveness", 0),
    }
    bonus = sum(max(0, v - 3) for v in quality_fields.values() if isinstance(v, (int, float)))
    if bonus > 0:
        adjustments.append({"reason": "分析质量加分", "change": bonus})
        score += bonus

    score = min(max(10, score), 100)

    return {
        "llm_priority_score": round(score),
        "base_score": base,
        "adjustments": adjustments,
    }


def calc_final_priority_tool(
    llm_priority_score: int,
    bottom_signal_score: int = 0,
    attention_score: int = 0,
    w_llm: float = 0.7,
    w_bottom: float = 0.2,
    w_attention: float = 0.1,
) -> float:
    """
    计算最终研究优先级。

    final_priority = llm_priority_score * 0.7 + bottom_signal_score * 0.2 + attention_score * 0.1
    """
    result = (
        llm_priority_score * w_llm
        + bottom_signal_score * w_bottom
        + attention_score * w_attention
    )
    return round(result, 1)


def calc_attention_score_tool(
    is_new_candidate: bool = False,
    major_risk_event: bool = False,
    earnings_report: bool = False,
    price_change_major: bool = False,
    valuation_change_major: bool = False,
    capital_flow_change: bool = False,
    days_since_last_analysis: int = 0,
) -> int:
    """
    计算今日关注紧迫度，满分 100。
    """
    score = 0
    if major_risk_event:
        score += 30
    if earnings_report:
        score += 25
    if price_change_major:
        score += 15
    if valuation_change_major:
        score += 10
    if capital_flow_change:
        score += 10
    if is_new_candidate:
        score += 5
    if days_since_last_analysis > 30:
        score += 5
    return min(score, 100)
