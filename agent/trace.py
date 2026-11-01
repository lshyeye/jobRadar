"""运行轨迹的持久化与聚合。

产出 ``data/agent_runs.json``，既是 CLI 的历史记录，也是前端「Agent 运行监控」
面板的唯一数据源——所以这里刻意做成**前端零加工的形态**：聚合指标在后端算好，
面板只负责展示，不在浏览器里重算统计（否则监控页自己成了性能问题）。

字段与前端 ``src/types/index.ts`` 里的 ``AgentRunsData`` 一一对应。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .types import AgentRun

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "agent_runs.json"
MAX_RUNS = 50


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """算面板要展示的全部指标。空数据必须返回零值而不是 None，别让前端做空判断。"""
    tool_calls = 0
    errors = 0
    latencies: list[int] = []
    per_tool: dict[str, dict[str, Any]] = {}
    total_tokens = 0
    durations: list[int] = []

    for run in runs:
        durations.append(float(run.get("duration_ms") or 0))
        total_tokens += int((run.get("usage") or {}).get("totalTokens") or 0)
        for step in run.get("steps") or []:
            if step.get("kind") != "tool":
                continue
            tool_calls += 1
            latency = float(step.get("latency_ms") or 0)
            latencies.append(latency)
            bucket = per_tool.setdefault(
                step.get("name", "unknown"),
                {"name": step.get("name", "unknown"), "calls": 0, "errors": 0, "latencySum": 0},
            )
            bucket["calls"] += 1
            bucket["latencySum"] += latency
            if step.get("status") != "ok":
                errors += 1
                bucket["errors"] += 1

    by_tool = [
        {
            "name": b["name"],
            "calls": b["calls"],
            "errors": b["errors"],
            "avgLatencyMs": round(b["latencySum"] / b["calls"], 2) if b["calls"] else 0.0,
        }
        for b in per_tool.values()
    ]
    by_tool.sort(key=lambda b: b["calls"], reverse=True)

    return {
        "totalRuns": len(runs),
        "okRuns": sum(1 for r in runs if r.get("status") == "ok"),
        "degradedRuns": sum(1 for r in runs if r.get("status") != "ok"),
        "toolCalls": tool_calls,
        "toolErrors": errors,
        "toolSuccessRate": round((tool_calls - errors) / tool_calls, 4) if tool_calls else 0.0,
        "avgDurationMs": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "p50StepLatencyMs": _percentile(latencies, 0.5),
        "p95StepLatencyMs": _percentile(latencies, 0.95),
        "totalTokens": total_tokens,
        "byTool": by_tool,
    }


def load(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"meta": {}, "runs": [], "aggregate": aggregate([])}
    return json.loads(path.read_text(encoding="utf-8"))


def save_run(run: AgentRun, path: Path = DEFAULT_PATH, max_runs: int = MAX_RUNS) -> dict[str, Any]:
    """把一次运行并入历史文件（同 run_id 覆盖），并重算聚合。"""
    payload = load(path)
    runs = [r for r in payload.get("runs", []) if r.get("run_id") != run.run_id]
    runs.insert(0, run.to_dict())
    runs = runs[:max_runs]

    combined = {
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "maxRuns": max_runs,
            "schemaVersion": 1,
        },
        "runs": runs,
        "aggregate": aggregate(runs),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(combined, ensure_ascii=False, indent=1), encoding="utf-8")
    return combined
