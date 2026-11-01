"""OpenAI 兼容端点的最小客户端。

不装 SDK：一个 POST 加一个 JSON 解析就是全部。这样做的好处是换模型只改环境变量，
而且能在受限网络里跑（用 urllib，不强依赖 httpx 之类的 TLS 栈）。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class LLMUnavailable(RuntimeError):
    """没有配置 key、或端点不可用时抛出——调用方据此切到离线规划器。"""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def load_config() -> LLMConfig | None:
    """从环境变量读取配置；没有 key 返回 None（不报错，让上层降级）。"""
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        return None
    return LLMConfig(
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    )


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    timeout: int = 60,
    config: LLMConfig | None = None,
) -> dict[str, Any]:
    """一次对话调用，返回归一化后的结果。

    返回结构::

        {"content": str, "tool_calls": [{"id", "name", "arguments"}], "usage": {...}}
    """
    cfg = config or load_config()
    if cfg is None:
        raise LLMUnavailable("未配置 LLM_API_KEY，改用离线规划器")

    payload: dict[str, Any] = {"model": cfg.model, "messages": messages, "temperature": 0.2}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    request = urllib.request.Request(
        cfg.endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise LLMUnavailable(f"LLM 端点返回 HTTP {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMUnavailable(f"LLM 调用失败：{exc}") from exc

    return normalize_response(body)


def normalize_response(body: dict[str, Any]) -> dict[str, Any]:
    """把 ``choices[0].message`` 压平成调用方好用的结构。"""
    choices = body.get("choices") or []
    message = (choices[0] or {}).get("message", {}) if choices else {}
    calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            parsed = {"__invalid_json__": raw_args}
        calls.append({"id": call.get("id", ""), "name": fn.get("name", ""), "arguments": parsed})
    return {
        "content": (message.get("content") or "").strip(),
        "tool_calls": calls,
        "usage": body.get("usage") or {},
    }
