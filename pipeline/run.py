"""管线编排：collect → filter → classify → store → match → aggregate。

编排层只做三件事：串流程、处理单源失败、写盘。业务规则都在各模块里，
这样每个模块都能单独测，编排层也不需要单测覆盖到每个分支。
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import heuristic_match, llm_match
from .adapters import AdapterError, fetch_raw, load_sources
from .aggregate import build_stats
from .classify import CLASSIFIER_VERSION, classify_type
from .jd_store import JdStore
from .normalize import normalize_job
from .types import MatchResult, PipelineOptions


def _log(message: str) -> None:
    print(f"[pipeline] {message}", file=sys.stderr)


def load_profile(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def collect(options: PipelineOptions) -> tuple[list[dict[str, Any]], list[str]]:
    """取各源原始记录并归一化。单源失败只记名字，不让整批中断。"""
    sources = load_sources(options.sources_file)
    today = date.today()
    jobs: list[dict[str, Any]] = []
    failed: list[str] = []

    for source in sources:
        try:
            rows = fetch_raw(source, options.fixture_dir)
        except AdapterError as exc:
            _log(f"数据源 {source.id} 失败：{exc}")
            failed.append(source.id)
            continue
        kept = 0
        for row in rows:
            job = normalize_job(row, source, today)
            if job is None:
                continue
            jobs.append(job)
            kept += 1
        _log(f"数据源 {source.id}：原始 {len(rows)} 条 → 归一化 {kept} 条")
    return jobs, failed


def filter_recent(jobs: list[dict[str, Any]], window_days: int, today: date | None = None) -> tuple[list[dict[str, Any]], int, int]:
    """近期过滤。返回 (保留, 超期丢弃数, 无日期丢弃数)。

    无日期的岗位默认**保留**（drop_undated=False）——宁可多一条，
    也不要因为采集方没给日期就漏掉一个可能合适的岗位。
    """
    ref = today or date.today()
    cutoff = (ref - timedelta(days=window_days)).isoformat()
    kept: list[dict[str, Any]] = []
    dropped_old = 0
    dropped_undated = 0
    for job in jobs:
        posted = job.get("postedDate") or ""
        if not posted:
            kept.append(job)
            continue
        if posted < cutoff:
            dropped_old += 1
            continue
        kept.append(job)
    return kept, dropped_old, dropped_undated


def classify_all(jobs: list[dict[str, Any]]) -> None:
    """填 type。把归一化阶段抽出的技能标签一起传进去，供泛研发岗兜底推断技术栈。"""
    for job in jobs:
        job["type"] = classify_type(job.get("title", ""), job.get("description", ""), job.get("req") or ())


def match_all(jobs: list[dict[str, Any]], profile: dict[str, Any], options: PipelineOptions) -> list[MatchResult]:
    """打分。LLM 优先，不可用或离线时走规则通道；两条通道产出同一契约。"""
    use_llm = (not options.offline) and (not options.no_llm) and llm_match.available()
    if use_llm:
        _log("使用 LLM 通道打分")
        return llm_match.score_all(jobs, profile, limit=options.limit)
    _log("使用规则通道打分（离线模式或未配置 LLM_API_KEY）")
    targets = jobs[: options.limit] if options.limit else jobs
    return heuristic_match.score_all(targets, profile)


def write_outputs(
    stats: dict[str, Any],
    scores: list[MatchResult],
    options: PipelineOptions,
) -> dict[str, Path]:
    out = options.out_dir
    out.mkdir(parents=True, exist_ok=True)
    stats_path = out / "stats.json"
    scores_path = out / "match_scores.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    scores_path.write_text(
        json.dumps([s.to_dict() for s in scores], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {"stats": stats_path, "scores": scores_path}


def sync_public(stats_path: Path, scores_path: Path, options: PipelineOptions) -> None:
    """把产物同步到前端可 fetch 的目录（默认关闭，避免覆盖演示数据）。"""
    public = options.out_dir.parent.parent / "frontend" / "public" / "data"
    if not public.exists():
        return
    shutil.copy2(stats_path, public / "stats.json")
    shutil.copy2(scores_path, public / "match_scores.json")
    _log(f"已同步到 {public}")


def execute(options: PipelineOptions) -> dict[str, Any]:
    """跑一次完整管线，返回运行摘要（供 CLI 打印与测试断言）。"""
    started = datetime.now()
    profile = load_profile(options.profile_file)

    jobs, failed = collect(options)
    jobs, dropped_old, dropped_undated = filter_recent(jobs, options.window_days)
    classify_all(jobs)
    if options.limit:
        jobs = jobs[: options.limit]

    store = JdStore(options.out_dir / "jd_details.json")
    added = store.merge(jobs)
    coverage = store.coverage(jobs)
    store.save()

    scores = match_all(jobs, profile, options)
    stats = build_stats(
        jobs,
        options,
        failed,
        jd_coverage=coverage,
        profile_label=str(profile.get("label") or ""),
        classifier=CLASSIFIER_VERSION,
    )
    stats["meta"]["recentFilter"].update(
        {"kept": len(jobs), "dropped_old": dropped_old, "dropped_undated": dropped_undated}
    )

    paths = write_outputs(stats, scores, options)
    if options.sync_public:
        sync_public(paths["stats"], paths["scores"], options)

    summary = {
        "jobs": len(jobs),
        "sources": len(load_sources(options.sources_file)),
        "failedSources": failed,
        "newJd": added,
        "jdCoverage": coverage,
        "scored": len(scores),
        "planner": "llm" if scores and not scores[0].model.startswith("heuristic") else "heuristic",
        "durationMs": int((datetime.now() - started).total_seconds() * 1000),
        "outDir": str(options.out_dir),
    }
    _log(
        f"完成：{summary['jobs']} 条岗位 / {summary['scored']} 条打分 / "
        f"正文新增 {added} 条 / 耗时 {summary['durationMs']}ms"
    )
    return summary
