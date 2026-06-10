"""
数据质量校验
"""

import logging
from typing import Any

logger = logging.getLogger("newstock.processing.validators")


def validate_snapshot(snapshot: dict) -> list[str]:
    """校验 StockSnapshot 完整性"""
    errors = []

    required_top = ["code", "market", "date"]
    for key in required_top:
        if not snapshot.get(key):
            errors.append(f"missing required field: {key}")

    if snapshot.get("market") not in ("HK", "US", "CN"):
        errors.append(f"invalid market: {snapshot.get('market')}")

    price_signal = snapshot.get("price_signal", {})
    if "current_price" in price_signal and price_signal["current_price"] is not None:
        if price_signal["current_price"] <= 0:
            errors.append("current_price <= 0")

    valuation = snapshot.get("valuation", {})
    if valuation.get("pe_ttm") is not None and valuation["pe_ttm"] <= 0:
        # PE 为负不算错误，但需要标记
        pass

    # 数据质量必须有
    dq = snapshot.get("data_quality")
    if dq is None:
        errors.append("data_quality section is missing")

    return errors


def validate_llm_output(llm_output: dict) -> list[str]:
    """校验 Agent 结构化输出"""
    errors = []

    allowed_priorities = {"very_high", "high", "medium", "low", "reject"}
    if llm_output.get("research_priority") not in allowed_priorities:
        errors.append(f"invalid research_priority: {llm_output.get('research_priority')}")

    for field in ["opportunity_quality", "valuation_attractiveness", "fundamental_quality", "risk_level"]:
        val = llm_output.get(field)
        if val is not None and (not isinstance(val, int) or val < 1 or val > 5):
            errors.append(f"{field} out of range 1-5: {val}")

    if llm_output.get("value_trap_probability") not in ("low", "medium", "high", None):
        errors.append(f"invalid value_trap_probability: {llm_output.get('value_trap_probability')}")

    confidence = llm_output.get("confidence")
    if confidence is not None and (confidence < 0 or confidence > 1):
        errors.append(f"confidence out of range 0-1: {confidence}")

    if "llm_priority_score" in llm_output:
        errors.append("Agent output should not contain llm_priority_score")

    return errors
