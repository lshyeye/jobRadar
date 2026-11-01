"""管线 CLI。

    python -m pipeline --offline                 # 用本地快照跑通全链路（不联网、不花钱）
    python -m pipeline --offline --limit 50      # 只跑 50 条，快速验证
    python -m pipeline                           # 用 config/sources.json 里的真实适配器
    python -m pipeline --offline --sync          # 顺带同步到前端 public/data
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .run import execute
from .types import ROOT, PipelineOptions


def build_options(args: argparse.Namespace) -> PipelineOptions:
    """命令行参数 → 运行参数。``--offline`` 同时意味着不调用 LLM（可复现）。"""
    sources_file = Path(args.sources) if args.sources else (
        ROOT / "config" / "sources.fixture.json" if args.offline else ROOT / "config" / "sources.example.json"
    )
    out_dir = Path(args.out) if args.out else (
        ROOT / "data" / "_offline" if args.offline else ROOT / "data"
    )
    return PipelineOptions(
        offline=args.offline,
        limit=args.limit,
        out_dir=out_dir,
        sources_file=sources_file,
        profile_file=Path(args.profile) if args.profile else ROOT / "config" / "profile.example.json",
        fixture_dir=Path(args.fixtures) if args.fixtures else ROOT / "fixtures",
        no_llm=args.offline or args.no_llm,
        sync_public=args.sync,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description="JobRadar 采集管线")
    parser.add_argument("--offline", action="store_true", help="用 fixtures 快照跑，不联网、不调 LLM")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条（调试用）")
    parser.add_argument("--out", default=None, help="输出目录，默认 data/（offline 默认 data/_offline）")
    parser.add_argument("--sources", default=None, help="数据源配置文件路径")
    parser.add_argument("--profile", default=None, help="求职画像配置文件路径")
    parser.add_argument("--fixtures", default=None, help="fixture 快照目录")
    parser.add_argument("--no-llm", action="store_true", help="强制走规则通道")
    parser.add_argument("--sync", action="store_true", help="产物同步到 frontend/public/data")
    args = parser.parse_args(argv)

    summary = execute(build_options(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
