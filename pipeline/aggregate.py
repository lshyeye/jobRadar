"""统计聚合：产出符合 docs/DATA_CONTRACT.md 的 stats.json。

两个语义约定（前端 TypeTrend 的文案就是「本周 vs 上周」，必须对齐）：

- ``byType`` / ``byGroup``：**整个过滤窗口**内的数量
- ``trend.this``：最近 7 天；``trend.last``：再往前 7 天
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .types import PipelineOptions

TYPE_ORDER = ("前端", "全栈", "Agent应用开发", "其他")
THIS_WEEK_DAYS = 7


def _window_counts(jobs: list[dict[str, Any]], start: str, end: str) -> dict[str, int]:
    counts = {t: 0 for t in TYPE_ORDER}
    for job in jobs:
        posted = job.get("postedDate") or ""
        if start <= posted <= end:
            counts[job.get("type", "其他")] = counts.get(job.get("type", "其他"), 0) + 1
    return counts


def _pct(this: int, last: int) -> float:
    if last == 0:
        return 0.0 if this == 0 else 100.0
    return round((this - last) / last * 100, 1)


def build_stats(
    jobs: list[dict[str, Any]],
    options: PipelineOptions,
    failed_sources: list[str],
    jd_coverage: float = 0.0,
    today: date | None = None,
    profile_label: str = "",
    classifier: str = "",
) -> dict[str, Any]:
    """组装 stats.json。

    `profile_label` / `classifier` 必须由调用方传入，不能在这里写死——
    早期版本把 scenario 硬编码成示例画像的文案，换成自定义画像就会显示错的场景名。
    """
    ref = today or date.today()
    period_end = ref
    period_start = ref - timedelta(days=options.window_days)

    this_end = ref
    this_start = ref - timedelta(days=THIS_WEEK_DAYS - 1)
    last_end = this_start - timedelta(days=1)
    last_start = last_end - timedelta(days=THIS_WEEK_DAYS - 1)

    by_type = {t: 0 for t in TYPE_ORDER}
    by_group: dict[str, int] = {}
    for job in jobs:
        by_type[job.get("type", "其他")] = by_type.get(job.get("type", "其他"), 0) + 1
        group = job.get("group", "其他互联网")
        by_group[group] = by_group.get(group, 0) + 1

    this_week = _window_counts(jobs, this_start.isoformat(), this_end.isoformat())
    last_week = _window_counts(jobs, last_start.isoformat(), last_end.isoformat())
    trend = {
        t: {
            "last": last_week[t],
            "this": this_week[t],
            "delta": this_week[t] - last_week[t],
            "pct": _pct(this_week[t], last_week[t]),
        }
        for t in TYPE_ORDER
    }

    req_counter: dict[str, int] = {}
    for job in jobs:
        for tag in job.get("req") or []:
            req_counter[tag] = req_counter.get(tag, 0) + 1
    top_requirements = [
        {"name": name, "count": count}
        for name, count in sorted(req_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]

    salary_caps = [j.get("salaryMax", 0) for j in jobs if j.get("salaryMax", 0) > 0]
    avg_salary = round(sum(salary_caps) / len(salary_caps), 1) if salary_caps else 0

    public_jobs = [{k: v for k, v in job.items() if k != "description"} for job in jobs]

    return {
        "meta": {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "last_week_start": last_start.isoformat(),
            "last_week_end": last_end.isoformat(),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "sample" if options.offline else "live",
            "scenario": profile_label,
            "classifier": classifier,
            "recentFilter": {
                "window_days": options.window_days,
                "drop_undated": False,
                "kept": len(jobs),
                "dropped_old": 0,
                "dropped_undated": 0,
            },
            "jdCoverage": jd_coverage,
            "avgSalaryMax": avg_salary,
        },
        "lastWeek": last_week,
        "jobs": public_jobs,
        "byType": by_type,
        "byGroup": by_group,
        "trend": trend,
        "topRequirements": top_requirements,
        "requirementSummary": _summaries(jobs, by_type, this_week, last_week, avg_salary),
        "failedSources": failed_sources,
    }


def _summaries(
    jobs: list[dict[str, Any]],
    by_type: dict[str, int],
    this_week: dict[str, int],
    last_week: dict[str, int],
    avg_salary: float,
) -> dict[str, str]:
    """每类岗位一段摘要文字。措辞只引用真实统计，不写结论性判断。"""
    out: dict[str, str] = {}
    for job_type in TYPE_ORDER[:-1]:
        subset = [j for j in jobs if j.get("type") == job_type]
        cities: dict[str, int] = {}
        for job in subset:
            if job.get("city"):
                cities[job["city"]] = cities.get(job["city"], 0) + 1
        top_city = "、".join(f"{c}({n})" for c, n in sorted(cities.items(), key=lambda kv: -kv[1])[:3])
        delta = this_week[job_type] - last_week[job_type]
        arrow = "增加" if delta >= 0 else "减少"
        reqs = {}
        for job in subset:
            for tag in job.get("req") or []:
                reqs[tag] = reqs.get(tag, 0) + 1
        top_req = "、".join(name for name, _ in sorted(reqs.items(), key=lambda kv: -kv[1])[:5])
        # 示例数据的薪资全是 0（原始数据就没给），这时整段省略薪资，
        # 而不是渲染成「平均薪资上限 —K」这种读起来像占位符的句子。
        salary_part = f"平均薪资上限 {avg_salary}K，" if avg_salary else ""
        out[job_type] = (
            f"本周共 {this_week[job_type]} 个{job_type} JD（较上周{arrow} {abs(delta)} 个），"
            f"{salary_part}主要城市：{top_city or '—'}。"
            f"最高频要求：{top_req or '—'}。"
        )
    return out
