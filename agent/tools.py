"""Agent 的工具实现。

四个工具全部是**确定性函数**，只读本地 ``data/*.json``，不联网、不依赖 LLM——
这样才能对它们写单测，也才能在没有 key 的情况下让 demo 跑出真实结果。

同一批工具同时被两处复用：

- ``loop``：作为 LLM 的 function calling / 离线规划器的执行目标；
- ``mcp_server``：作为 MCP 工具暴露给任意客户端。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import ToolSpec

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

JOB_TYPES = ("前端", "全栈", "Agent应用开发", "其他")


# ---------------------------------------------------------------- 数据加载


@lru_cache(maxsize=1)
def load_jobs() -> list[dict[str, Any]]:
    """岗位流（含匹配分），按 url 合并 stats 与 match_scores。"""
    stats = json.loads((DATA_DIR / "stats.json").read_text(encoding="utf-8"))
    scores_path = DATA_DIR / "match_scores.json"
    scores: dict[str, dict[str, Any]] = {}
    if scores_path.exists():
        rows = json.loads(scores_path.read_text(encoding="utf-8"))
        scores = {r["jobId"]: r for r in rows}

    merged: list[dict[str, Any]] = []
    for job in stats.get("jobs", []):
        row = dict(job)
        score = scores.get(job["url"])
        row["score"] = score["matchScore"] if score else None
        row["model"] = score["model"] if score else None
        row["advice"] = score["advice"] if score else ""
        merged.append(row)
    return merged


@lru_cache(maxsize=1)
def load_score_index() -> dict[str, dict[str, Any]]:
    path = DATA_DIR / "match_scores.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {r["jobId"]: r for r in rows}


def clear_cache() -> None:
    """数据文件更新后调用，供测试与管线同步使用。"""
    load_jobs.cache_clear()
    load_score_index.cache_clear()


# ---------------------------------------------------------------- 参数校验


def _as_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {name} 必须是整数，收到 {value!r}") from exc


def _as_limit(value: Any, default: int, cap: int) -> int:
    if value is None:
        return default
    limit = _as_int(value, "limit")
    if limit <= 0:
        raise ValueError("参数 limit 必须为正整数")
    return min(limit, cap)


def _clip(text: str, size: int = 48) -> str:
    text = (text or "").strip()
    return text if len(text) <= size else text[: size - 1] + "…"


# ---------------------------------------------------------------- 工具实现


def search_jobs(
    city: str | None = None,
    job_type: str | None = None,
    min_score: int | None = None,
    keyword: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """按城市 / 类型 / 最低分 / 关键词筛岗位，按匹配分降序返回。"""
    if job_type and job_type not in JOB_TYPES:
        raise ValueError(f"参数 job_type 只接受 {JOB_TYPES}，收到 {job_type!r}")
    top = _as_limit(limit, 8, 50)
    floor = 0 if min_score is None else _as_int(min_score, "min_score")
    if not 0 <= floor <= 100:
        raise ValueError("参数 min_score 必须在 0-100 之间")
    kw = (keyword or "").strip().lower()

    hits: list[dict[str, Any]] = []
    for job in load_jobs():
        if city and city not in (job.get("city") or ""):
            continue
        if job_type and job.get("type") != job_type:
            continue
        if floor and (job.get("score") is None or job["score"] < floor):
            continue
        if kw:
            hay = f"{job.get('title','')} {job.get('company','')} {' '.join(job.get('req') or [])}".lower()
            if kw not in hay:
                continue
        hits.append(job)

    hits.sort(key=lambda j: (j.get("score") is None, -(j.get("score") or 0), j.get("postedDate") or ""))
    items = [
        {
            "title": j["title"],
            "company": j.get("company", ""),
            "city": j.get("city", ""),
            "type": j.get("type", ""),
            "score": j.get("score"),
            "postedDate": j.get("postedDate", ""),
            "url": j["url"],
            "advice": j.get("advice", ""),
        }
        for j in hits[:top]
    ]
    return {
        "total": len(hits),
        "returned": len(items),
        "filters": {"city": city, "job_type": job_type, "min_score": min_score, "keyword": keyword},
        "items": items,
    }


def get_job(url: str) -> dict[str, Any]:
    """取单条岗位的完整信息与匹配分析。"""
    if not url or not isinstance(url, str):
        raise ValueError("参数 url 必需且为字符串")
    for job in load_jobs():
        if job["url"] == url:
            score = load_score_index().get(url)
            return {
                "found": True,
                "job": {
                    "title": job["title"],
                    "company": job.get("company", ""),
                    "city": job.get("city", ""),
                    "type": job.get("type", ""),
                    "salaryMin": job.get("salaryMin", 0),
                    "salaryMax": job.get("salaryMax", 0),
                    "postedDate": job.get("postedDate", ""),
                    "req": job.get("req", []),
                    "url": url,
                },
                "analysis": {
                    "matchScore": score.get("matchScore") if score else None,
                    "model": score.get("model") if score else None,
                    "hardReqs": score.get("hardReqs", []) if score else [],
                    "bonusReqs": score.get("bonusReqs", []) if score else [],
                    "matchedSkills": score.get("matchedSkills", []) if score else [],
                    "missingSkills": score.get("missingSkills", []) if score else [],
                    "advice": score.get("advice", "") if score else "",
                },
            }
    return {"found": False, "message": f"未找到该岗位：{url}"}


def market_stats(group_by: str | None = None, top: int | None = None) -> dict[str, Any]:
    """岗位分布统计：按城市 / 类型 / 公司集团聚合，附平均匹配分。"""
    field = group_by or "city"
    if field not in ("city", "type", "group"):
        raise ValueError("参数 group_by 只接受 city / type / group")
    size = _as_limit(top, 8, 30)

    buckets: dict[str, list[int | None]] = {}
    for job in load_jobs():
        key = job.get(field) or "未标注"
        buckets.setdefault(key, []).append(job.get("score"))

    rows = []
    for key, scores in buckets.items():
        valid = [s for s in scores if s is not None]
        rows.append(
            {
                "key": key,
                "count": len(scores),
                "avgScore": round(sum(valid) / len(valid), 1) if valid else None,
            }
        )
    rows.sort(key=lambda r: r["count"], reverse=True)

    all_scores = [j["score"] for j in load_jobs() if j.get("score") is not None]
    return {
        "group_by": field,
        "total": len(load_jobs()),
        "avgScore": round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
        "buckets": rows[:size],
    }


def skill_gaps(min_score: int | None = None, limit: int | None = None) -> dict[str, Any]:
    """聚合高匹配岗位的缺口技能频次——回答「接下来该补什么」。"""
    floor = 55 if min_score is None else _as_int(min_score, "min_score")
    size = _as_limit(limit, 10, 30)

    counter: dict[str, int] = {}
    matched = 0
    for score in load_score_index().values():
        if score.get("matchScore", 0) < floor:
            continue
        matched += 1
        for skill in score.get("missingSkills", []):
            key = _clip(skill, 40)
            counter[key] = counter.get(key, 0) + 1

    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:size]
    return {
        "min_score": floor,
        "jobs_considered": matched,
        "gaps": [{"skill": skill, "jobs": count} for skill, count in ranked],
    }


# ---------------------------------------------------------------- 工具注册表


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_jobs",
        description="按城市、岗位类型、最低匹配分、关键词筛选岗位，返回按匹配分降序的清单。",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，如 北京 / 深圳"},
                "job_type": {"type": "string", "enum": list(JOB_TYPES), "description": "岗位类型"},
                "min_score": {"type": "integer", "description": "最低匹配分 0-100"},
                "keyword": {"type": "string", "description": "标题/公司/技能关键词"},
                "limit": {"type": "integer", "description": "返回条数，默认 8，上限 50"},
            },
            "required": [],
        },
        handler=search_jobs,
    ),
    ToolSpec(
        name="get_job",
        description="取单条岗位的完整信息与匹配分析（硬性要求/加分项/已匹配/缺口/建议）。",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "岗位链接（主键）"}},
            "required": ["url"],
        },
        handler=get_job,
    ),
    ToolSpec(
        name="market_stats",
        description="岗位分布统计：按城市/类型/公司集团聚合数量与平均匹配分。",
        parameters={
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["city", "type", "group"], "description": "聚合维度"},
                "top": {"type": "integer", "description": "返回前 N 组，默认 8"},
            },
            "required": [],
        },
        handler=market_stats,
    ),
    ToolSpec(
        name="skill_gaps",
        description="聚合高匹配岗位的缺口技能频次，用来回答「接下来该补什么」。",
        parameters={
            "type": "object",
            "properties": {
                "min_score": {"type": "integer", "description": "只看该分数以上的岗位，默认 55"},
                "limit": {"type": "integer", "description": "返回条数，默认 10"},
            },
            "required": [],
        },
        handler=skill_gaps,
    ),
)

TOOL_INDEX: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """按名字调用工具，参数经 JSON Schema 白名单校验。

    校验放在这里而不是各工具内部：MCP 与 LLM 两条链路都走这个入口，
    保证「模型给的参数」和「客户端给的参数」被同一套规则约束。
    """
    spec = TOOL_INDEX.get(name)
    if spec is None:
        raise ValueError(f"未知工具：{name}")
    allowed = set(spec.parameters.get("properties", {}))
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"工具 {name} 不接受参数：{sorted(unknown)}")
    for key in spec.parameters.get("required", []):
        if key not in args:
            raise ValueError(f"工具 {name} 缺少必需参数：{key}")
    return spec.handler(**args)


def tool_schemas_for_openai() -> list[dict[str, Any]]:
    return [t.to_openai_schema() for t in TOOLS]


def tool_schemas_for_mcp() -> list[dict[str, Any]]:
    return [t.to_mcp_schema() for t in TOOLS]
