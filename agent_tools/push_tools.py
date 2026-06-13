"""
报告推送工具

将每日研究报告分段推送到企业微信机器人（markdown_v2 格式）。
读取 reports/daily_{date}_push.md，按章节拆分后逐条发送。

依赖：requests（已在 requirements.txt 中）
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("newstock.agent_tools.push_tools")

# 企业微信 markdown_v2 单条消息上限 4096 字节，留余量
MAX_BYTES = 4000


def _load_push_config(config_path: str) -> dict[str, Any]:
    """加载推送配置"""
    path = Path(config_path)
    if not path.exists():
        return {}

    try:
        import yaml
    except ModuleNotFoundError:
        logger.warning("PyYAML not installed, cannot load push config")
        return {}

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    return config.get("push", {})


def _read_push_markdown(report_date: str, output_dir: str) -> str | None:
    """读取企业微信推送版 Markdown 文件"""
    push_path = Path(output_dir) / f"daily_{report_date}_push.md"
    if not push_path.exists():
        return None
    return push_path.read_text(encoding="utf-8").strip()


def _get_report_output_dir(config_path: str) -> str:
    """从主配置中读取 report.output_dir，兜底返回 reports"""
    path = Path(config_path)
    if not path.exists():
        return "reports"
    try:
        import yaml
        with path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config.get("report", {}).get("output_dir", "reports")
    except Exception:
        return "reports"


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def _split_into_messages(content: str) -> list[str]:
    """
    将推送内容按逻辑分段，每段不超过 MAX_BYTES 字节。

    分段策略：
      1. 提取「标题 + 候选池总览 + 重点观察」→ 消息 1
      2. 「个股详细分析」按 `---` 分隔为每只股票一条消息
    """
    messages: list[str] = []

    # 切掉末尾的 --- 和版权行（单独处理）
    content = re.sub(r'\n---\n\*报告由.*\*$', '', content.strip())

    # 用 ## 🔍 个股详细分析 作为分割点
    parts = content.split("## 🔍 个股详细分析", 1)

    # ── 头部：标题 + 候选池总览 + 重点观察 ──
    header = parts[0].strip()
    # 清理尾部多余分隔线
    header = re.sub(r'\n---\s*$', '', header)
    if _byte_len(header) > MAX_BYTES:
        # 头部太长则按 ## 二级标题拆
        header_sections = re.split(r'\n(?=## )', header)
        current = ""
        for sec in header_sections:
            if _byte_len(current + sec) > MAX_BYTES:
                if current:
                    messages.append(current.strip())
                current = sec
            else:
                current += "\n\n" + sec if current else sec
        if current:
            messages.append(current.strip())
    else:
        messages.append(header)

    # ── 个股详细分析 ──
    if len(parts) > 1:
        detail = parts[1].strip()
        # 按 --- 分隔每只股票的分析
        stock_blocks = re.split(r'\n---\n', detail)
        # 去掉尾部的空块和重复的 ---
        stock_blocks = [b.strip() for b in stock_blocks if b.strip() and b.strip() != '---']

        for block in stock_blocks:
            block = f"## 🔍 个股详细分析\n\n{block.strip()}"
            if _byte_len(block) <= MAX_BYTES:
                messages.append(block)
            else:
                # 个股分析仍然超长，按 ### 子标题拆分
                sub_sections = re.split(r'\n(?=### )', block)
                current = ""
                for sec in sub_sections:
                    if _byte_len(current + sec) > MAX_BYTES:
                        if current:
                            messages.append(current.strip())
                        current = sec
                    else:
                        current += "\n\n" + sec if current else sec
                if current:
                    messages.append(current.strip())

    return messages


def _send_wecom_markdown(webhook_url: str, content: str) -> dict[str, Any]:
    """发送单条 markdown_v2 消息到企业微信"""
    payload = {
        "msgtype": "markdown_v2",
        "markdown_v2": {"content": content},
    }
    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    return {"errcode": result.get("errcode", -1), "errmsg": result.get("errmsg", ""), "bytes": _byte_len(content)}


# ── 主工具函数 ──


def push_report_tool(
    conn: Any,
    report_date: str,
    config_path: str | None = None,
) -> dict[str, Any]:
    """
    分段推送每日研究报告到企业微信机器人。

    Args:
        conn: 数据库连接（由 tool_runner 自动注入）
        report_date: 报告日期，格式 YYYY-MM-DD
        config_path: 配置文件路径（tool_runner 自动注入）

    Returns:
        {
            "date": "2026-06-13",
            "total_messages": 4,
            "success_count": 4,
            "failed_count": 0,
            "results": [{"index": 1, "success": true, "errcode": 0, "bytes": 1234}, ...],
        }
    """
    cfg_path = config_path or "config/config.yaml"
    push_config = _load_push_config(cfg_path)

    wecom_cfg = push_config.get("wecom_bot", {}) if push_config else {}
    webhook_url = wecom_cfg.get("webhook_url", "")

    if not webhook_url or "YOUR_KEY" in webhook_url:
        return {
            "date": report_date,
            "success": False,
            "error": "企业微信 webhook_url 未配置或为占位符",
        }

    # 读取推送内容
    output_dir = push_config.get("output_dir") or _get_report_output_dir(cfg_path)
    content = _read_push_markdown(report_date, output_dir)

    if not content:
        return {
            "date": report_date,
            "success": False,
            "error": f"推送文件不存在: {output_dir}/daily_{report_date}_push.md（请先运行 generate-report）",
        }

    # 分段
    messages = _split_into_messages(content)

    # 逐条发送（企业微信机器人有频率限制，间隔 1 秒）
    results = []
    for i, msg in enumerate(messages, 1):
        try:
            res = _send_wecom_markdown(webhook_url, msg)
            success = res["errcode"] == 0
            results.append({
                "index": i,
                "success": success,
                "errcode": res["errcode"],
                "errmsg": res.get("errmsg", ""),
                "bytes": res["bytes"],
            })
            logger.info(
                f"Push msg {i}/{len(messages)}: {'OK' if success else 'FAIL'} "
                f"({res['bytes']}B, errcode={res['errcode']})"
            )
        except Exception as e:
            logger.warning(f"Push msg {i}/{len(messages)} failed: {e}")
            results.append({
                "index": i,
                "success": False,
                "error": str(e),
            })

        # 最后一条不需要等待
        if i < len(messages):
            time.sleep(1)

    success_count = sum(1 for r in results if r.get("success"))
    total_bytes = sum(r.get("bytes", 0) for r in results)

    logger.info(
        f"Push report {report_date}: {success_count}/{len(messages)} msgs OK, total {total_bytes}B"
    )

    return {
        "date": report_date,
        "total_messages": len(messages),
        "success_count": success_count,
        "failed_count": len(messages) - success_count,
        "results": results,
    }
