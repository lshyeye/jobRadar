"""Agent 层的数据结构。

类型集中定义在此，禁止在函数内联定义（与前端 ``src/types/index.ts`` 同一约定）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """一个可被 Agent 或 MCP 客户端调用的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]

    def to_openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI /chat/completions 的 tools 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_mcp_schema(self) -> dict[str, Any]:
        """转成 MCP tools/list 的格式（字段名不同，别混用）。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


@dataclass
class StepTrace:
    """单步轨迹：一次工具调用，或最终答复。

    前端监控面板直接消费这个结构，所以每一步都带耗时与状态——
    Agent 不可观测就等于不可维护。
    """

    index: int
    kind: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    latency_ms: float = 0.0
    summary: str = ""
    result_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRun:
    """一次完整运行。"""

    run_id: str
    question: str
    started_at: str
    duration_ms: int = 0
    status: str = "ok"
    planner: str = "offline"
    answer: str = ""
    steps: list[StepTrace] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [s.to_dict() for s in self.steps]
        return payload
