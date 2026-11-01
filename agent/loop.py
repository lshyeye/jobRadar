"""ReAct 式工具调用循环。

两条通道共用同一套工具与同一套 trace 结构：

- ``llm``：模型自己决定调哪个工具、调几次（function calling）；
- ``offline``：规则规划器给出确定序列（没有 key 时的兜底）。

无论哪条通道，**每一步都记进 trace**：工具名、参数、耗时、状态、返回条数。
这不是日志洁癖——Agent 不可观测就没法调优，而「可观测」正是这个模块对外的卖点。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Any

from . import llm
from . import planner
from .tools import call_tool, tool_schemas_for_openai
from .types import AgentRun, StepTrace

SYSTEM_PROMPT = """你是 JobRadar 的岗位分析助手。你只能通过工具查询数据，不要凭空编造岗位。

规则：
1. 任何涉及具体岗位、数量、分数的结论都必须来自工具返回结果；
2. 工具返回为空时，如实说明没有结果，不要补充想象中的岗位；
3. 回答用中文，先给结论，再给依据，不超过 5 句话；
4. 用户问「该补什么」时优先调用 skill_gaps。"""

MAX_STEPS_DEFAULT = 6


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_id() -> str:
    """带随机后缀：同一秒内多次运行不能互相覆盖（曾因此把 4 条轨迹压成 1 条）。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def _summarize(name: str, result: dict[str, Any]) -> tuple[str, int]:
    """给一步结果生成人类可读摘要 + 结果条数（供面板展示）。"""
    if name == "search_jobs":
        return f"命中 {result.get('total', 0)} 条，返回 {result.get('returned', 0)} 条", result.get("returned", 0)
    if name == "get_job":
        if not result.get("found"):
            return "未找到岗位", 0
        return f"匹配分 {result['analysis'].get('matchScore')}", 1
    if name == "market_stats":
        return f"{len(result.get('buckets', []))} 个分组，共 {result.get('total', 0)} 条", len(result.get("buckets", []))
    if name == "skill_gaps":
        return f"聚合 {result.get('jobs_considered', 0)} 条岗位的缺口", len(result.get("gaps", []))
    return "完成", 0


def _execute(step_index: int, name: str, args: dict[str, Any]) -> tuple[StepTrace, dict[str, Any]]:
    """执行一次工具调用并把耗时/异常都变成 trace，不向上抛。"""
    started = time.perf_counter()
    try:
        result = call_tool(name, args)
        summary, count = _summarize(name, result)
        trace = StepTrace(
            index=step_index,
            kind="tool",
            name=name,
            args=args,
            status="ok",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            summary=summary,
            result_count=count,
        )
        return trace, result
    except ValueError as exc:
        trace = StepTrace(
            index=step_index,
            kind="tool",
            name=name,
            args=args,
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            summary="参数校验失败",
            error=str(exc),
        )
        return trace, {"error": str(exc)}


def run_offline(question: str, max_steps: int = MAX_STEPS_DEFAULT) -> AgentRun:
    """规则规划器通道：确定、可复现、无外部依赖。"""
    started = time.perf_counter()
    plan = planner.build_plan(question)
    run = AgentRun(
        run_id=_run_id(),
        question=question,
        started_at=_now(),
        planner="offline",
        status="ok",
    )

    results: list[dict[str, Any]] = []
    for index, call in enumerate(plan.calls[:max_steps], start=1):
        trace, result = _execute(index, call.name, call.args)
        run.steps.append(trace)
        results.append(result)

    answer_started = time.perf_counter()
    run.answer = planner.compose_answer(plan, results)
    run.steps.append(
        StepTrace(
            index=len(run.steps) + 1,
            kind="final",
            name="compose_answer",
            status="ok",
            latency_ms=int((time.perf_counter() - answer_started) * 1000),
            summary=f"意图 {plan.intent}",
        )
    )
    run.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    if any(s.status == "error" for s in run.steps):
        run.status = "degraded"
    return run


def run_llm(question: str, max_steps: int = MAX_STEPS_DEFAULT) -> AgentRun:
    """模型通道：由模型决定工具调用序列。"""
    started = time.perf_counter()
    run = AgentRun(
        run_id=_run_id(),
        question=question,
        started_at=_now(),
        planner="llm",
        status="ok",
    )
    usage = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for step in range(1, max_steps + 1):
        response = llm.chat(messages, tools=tool_schemas_for_openai())
        raw_usage = response.get("usage") or {}
        usage["promptTokens"] += int(raw_usage.get("prompt_tokens", 0) or 0)
        usage["completionTokens"] += int(raw_usage.get("completion_tokens", 0) or 0)
        usage["totalTokens"] += int(raw_usage.get("total_tokens", 0) or 0)

        calls = response.get("tool_calls") or []
        if not calls:
            run.answer = response.get("content") or "（模型没有给出答复）"
            break

        messages.append(
            {
                "role": "assistant",
                "content": response.get("content", ""),
                "tool_calls": [
                    {
                        "id": c["id"] or f"call_{step}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                    }
                    for c in calls
                ],
            }
        )
        for offset, call in enumerate(calls):
            if "__invalid_json__" in call["arguments"]:
                trace = StepTrace(
                    index=len(run.steps) + 1,
                    kind="tool",
                    name=call["name"],
                    status="error",
                    summary="模型返回了非法 JSON 参数",
                    error="invalid json arguments",
                )
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"error": "invalid json"})})
            else:
                trace, result = _execute(len(run.steps) + 1, call["name"], call["arguments"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"] or f"call_{step}_{offset}",
                        "content": json.dumps(result, ensure_ascii=False)[:4000],
                    }
                )
            run.steps.append(trace)
    else:
        run.status = "max_steps"
        run.answer = run.answer or f"达到最大步数 {max_steps}，未能收敛出结论。"

    run.usage = usage
    run.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    if any(s.status == "error" for s in run.steps):
        run.status = "degraded" if run.status == "ok" else run.status
    return run


def run(question: str, mode: str = "auto", max_steps: int = MAX_STEPS_DEFAULT) -> AgentRun:
    """统一入口。

    ``mode="auto"`` 时有 key 走模型、没 key 走规则；两者都失败时**不抛异常**，
    而是返回带 degraded 状态的离线结果——Agent 的失败必须能被面板看见。
    """
    if mode == "offline":
        return run_offline(question, max_steps)
    if mode == "llm":
        try:
            return run_llm(question, max_steps)
        except llm.LLMUnavailable:
            fallback = run_offline(question, max_steps)
            fallback.status = "degraded"
            fallback.steps.insert(
                0,
                StepTrace(index=0, kind="final", name="llm_unavailable", status="error",
                          summary="LLM 不可用，已切到离线规划器"),
            )
            return fallback

    if llm.load_config() is None:
        return run_offline(question, max_steps)
    try:
        return run_llm(question, max_steps)
    except llm.LLMUnavailable:
        return run_offline(question, max_steps)
