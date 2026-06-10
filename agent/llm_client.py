"""
LLM API 客户端（支持 OpenAI 兼容接口）
"""

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("newstock.agent.llm_client")

# 尝试导入 openai
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    logger.warning("openai package not installed, LLM calls will use requests fallback")


class LLMClient:
    """LLM API 客户端"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        if HAS_OPENAI:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
        else:
            self.client = None

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto",
    ) -> dict:
        """发送聊天请求"""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        if self.client:
            response = self.client.chat.completions.create(**kwargs)
            return self._parse_openai_response(response)
        else:
            return self._requests_fallback(messages, tools)

    def _parse_openai_response(self, response) -> dict:
        """解析 OpenAI SDK 响应"""
        choice = response.choices[0]
        result = {"finish_reason": choice.finish_reason}

        if choice.finish_reason == "tool_calls":
            result["tool_calls"] = []
            for tc in choice.message.tool_calls:
                args = {}
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    pass
                result["tool_calls"].append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })
        else:
            result["content"] = choice.message.content

        return result

    def _requests_fallback(self, messages: list[dict], tools: Optional[list[dict]]) -> dict:
        """使用 requests 的 fallback（不需要 openai 包）"""
        import requests

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        resp = requests.post(url, headers=headers, json=body, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        result = {"finish_reason": choice["finish_reason"]}

        if choice["finish_reason"] == "tool_calls":
            result["tool_calls"] = []
            for tc in choice["message"].get("tool_calls", []):
                args = {}
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    pass
                result["tool_calls"].append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": args,
                })
        else:
            result["content"] = choice["message"]["content"]

        return result
