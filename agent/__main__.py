"""Agent CLI。

    python -m agent ask "北京 55 分以上的 Agent 岗有哪些"     # 单次问答
    python -m agent ask "..." --offline                       # 强制离线规划器
    python -m agent demo                                      # 跑一组示例并落盘 trace
    python -m agent mcp                                       # 启动 MCP server（stdio）
    python -m agent runs                                      # 查看历史与聚合指标
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import loop, trace

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "frontend" / "public" / "data"

DEMO_QUESTIONS = (
    "北京 55 分以上的 Agent 岗有哪些",
    "各城市岗位分布情况",
    "我现在的缺口主要是哪些，该补什么",
    "市场上有多少前端岗位",
)


def sync_public() -> None:
    """把 agent_runs.json 同步到前端可 fetch 的目录。"""
    source = trace.DEFAULT_PATH
    if not source.exists():
        return
    PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, PUBLIC_DATA / "agent_runs.json")
    # stats / match_scores 也一并同步，保证前端数据同源
    for name in ("stats.json", "match_scores.json"):
        local = ROOT / "data" / name
        if local.exists():
            shutil.copy2(local, PUBLIC_DATA / name)


def print_run(run) -> None:
    print(f"\n[{run.planner}] {run.question}")
    for step in run.steps:
        flag = "OK " if step.status == "ok" else "ERR"
        print(f"  {flag} #{step.index} {step.name:<14} {step.latency_ms:>5}ms  {step.summary}")
    print(f"  → {run.answer}")
    print(f"  状态 {run.status} · 总耗时 {run.duration_ms}ms")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="JobRadar Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="问一个问题")
    ask.add_argument("question")
    ask.add_argument("--offline", action="store_true", help="强制用离线规划器")
    ask.add_argument("--llm", action="store_true", help="强制用模型通道")
    ask.add_argument("--max-steps", type=int, default=loop.MAX_STEPS_DEFAULT)
    ask.add_argument("--no-save", action="store_true", help="不写入 agent_runs.json")

    demo = sub.add_parser("demo", help="跑一组示例问题并落盘")
    demo.add_argument("--offline", action="store_true")

    sub.add_parser("mcp", help="启动 MCP server（stdio）")
    sub.add_parser("runs", help="查看历史运行与聚合指标")

    args = parser.parse_args(argv)

    if args.command == "mcp":
        from .mcp_server import serve

        serve()
        return 0

    if args.command == "runs":
        payload = trace.load()
        print(json.dumps(payload.get("aggregate", {}), ensure_ascii=False, indent=2))
        print(f"历史运行 {len(payload.get('runs', []))} 条 → {trace.DEFAULT_PATH}")
        return 0

    if args.command == "ask":
        mode = "offline" if args.offline else "llm" if args.llm else "auto"
        run = loop.run(args.question, mode=mode, max_steps=args.max_steps)
        print_run(run)
        if not args.no_save:
            trace.save_run(run)
            sync_public()
        return 0

    if args.command == "demo":
        mode = "offline" if args.offline else "auto"
        for question in DEMO_QUESTIONS:
            run = loop.run(question, mode=mode)
            print_run(run)
            trace.save_run(run)
        sync_public()
        payload = trace.load()
        print("\n聚合指标：")
        print(json.dumps(payload["aggregate"], ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
