"""MCP Server（stdio 传输，JSON-RPC 2.0，按行分隔）。

把 ``tools`` 里的四个工具原样暴露出去，任何 MCP 客户端（Claude Desktop、IDE 插件、
自己的 agent）都能直接调——这样「Agent 能力」不再绑定在本仓库的 CLI 上。

实现上只用标准库：MCP 的 stdio 传输就是「一行一条 JSON-RPC 消息」，
不需要 SDK。协议版本走 ``2024-11-05``（当前主流实现兼容的版本）。

启动::

    python -m agent mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from .tools import call_tool, tool_schemas_for_mcp

SERVER_NAME = "job-radar"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _result(msg_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": payload}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """处理单条消息。通知（无 id）返回 None，不产生响应。"""
    method = message.get("method")
    msg_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return _result(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {"tools": tool_schemas_for_mcp()})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _error(msg_id, INVALID_PARAMS, "tools/call 需要 name(string) 与 arguments(object)")
        try:
            payload = call_tool(name, args)
        except ValueError as exc:
            # 工具级错误按 MCP 约定放进 result.isError，而不是 JSON-RPC error，
            # 这样客户端能把「参数不合法」当成可读结果展示给模型去纠正。
            return _result(
                msg_id,
                {"content": [{"type": "text", "text": f"调用失败：{exc}"}], "isError": True},
            )
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": False,
            },
        )

    if is_notification:
        return None
    return _error(msg_id, METHOD_NOT_FOUND, f"不支持的方法：{method}")


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """逐行读取并响应，直到 stdin 关闭。"""
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sink.write(json.dumps(_error(None, PARSE_ERROR, "JSON 解析失败")) + "\n")
            sink.flush()
            continue
        if not isinstance(message, dict):
            sink.write(json.dumps(_error(None, INVALID_REQUEST, "请求必须是对象")) + "\n")
            sink.flush()
            continue
        response = handle(message)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False) + "\n")
            sink.flush()


if __name__ == "__main__":
    serve()
