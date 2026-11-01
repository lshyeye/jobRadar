"""离线确定性规划器。

没有 LLM key 时，Agent 不能变成「哑巴」——否则任何人 clone 下来都跑不出结果，
这个模块就成了演示品。所以这里用规则把自然语言问题解析成**确定的工具调用序列**：

- 意图识别（找岗位 / 看详情 / 市场统计 / 补短板）
- 槽位抽取（城市、岗位类型、分数阈值、关键词）
- 多步编排（先检索，再聚合缺口，两步都进 trace）

它的局限必须写清楚：**只认关键词，不理解语义**。它存在的意义是「永远可用的兜底」
和「给 LLM 通道做对照」，不是替代 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .tools import load_jobs

CITY_HINTS = ("北京", "上海", "深圳", "杭州", "广州", "成都", "武汉", "天津", "南京", "西安")
TYPE_HINTS = (
    ("agent", "Agent应用开发"),
    ("智能体", "Agent应用开发"),
    ("全栈", "全栈"),
    ("前端", "前端"),
)
STATS_WORDS = ("统计", "分布", "多少", "趋势", "市场", "占比", "几家")
GAP_WORDS = (
    "缺口",
    "短板",
    "缺什么",
    "缺哪些",
    "补什么",
    "补哪",
    "该补",
    "该学",
    "差什么",
    "差距",
    "提升",
)
DETAIL_WORDS = ("详情", "为什么", "这条", "这条岗", "展开", "具体")


@dataclass
class PlannedCall:
    """一步计划：调哪个工具、带什么参数、为什么这么调。"""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class Plan:
    """完整计划：意图 + 有序工具调用。"""

    intent: str
    calls: list[PlannedCall]
    slots: dict[str, Any] = field(default_factory=dict)


def _known_cities() -> tuple[str, ...]:
    """优先用数据里真实出现过的城市名，避免硬编码失效。"""
    found = {j.get("city", "") for j in load_jobs()}
    extra = tuple(c for c in CITY_HINTS if c in found)
    return extra or CITY_HINTS


def extract_slots(question: str) -> dict[str, Any]:
    """从问句里抽槽位。抽不到就返回 None，不做猜测性填充。"""
    text = question.strip()
    slots: dict[str, Any] = {}

    city = next((c for c in _known_cities() if c in text), None)
    if city:
        slots["city"] = city

    lowered = text.lower()
    for word, job_type in TYPE_HINTS:
        if word in lowered:
            slots["job_type"] = job_type
            break

    match = re.search(r"(\d{2,3})\s*分?\s*(?:以上|以上岗位|分以上)?", text)
    if match:
        value = int(match.group(1))
        if 0 <= value <= 100:
            slots["min_score"] = value

    url = re.search(r"https?://\S+", text)
    if url:
        slots["url"] = url.group(0).rstrip("，。,.")

    return slots


def build_plan(question: str) -> Plan:
    """把问句变成有序工具调用。"""
    text = question.strip()
    slots = extract_slots(text)
    lowered = text.lower()

    if slots.get("url") or any(w in text for w in DETAIL_WORDS):
        if slots.get("url"):
            return Plan(
                intent="job_detail",
                calls=[PlannedCall("get_job", {"url": slots["url"]}, "问句里给了岗位链接，直接取详情")],
                slots=slots,
            )

    if any(w in text for w in STATS_WORDS):
        group = "type"
        if any(w in text for w in ("城市", "哪里", "地区")):
            group = "city"
        elif any(w in text for w in ("公司", "集团", "哪家")):
            group = "group"
        return Plan(
            intent="market_stats",
            calls=[PlannedCall("market_stats", {"group_by": group, "top": 8}, f"问的是分布，按 {group} 聚合")],
            slots=slots,
        )

    if any(w in text for w in GAP_WORDS):
        args: dict[str, Any] = {"limit": 10}
        if "min_score" in slots:
            args["min_score"] = slots["min_score"]
        return Plan(
            intent="skill_gaps",
            calls=[PlannedCall("skill_gaps", args, "问的是该补什么，聚合缺口技能频次")],
            slots=slots,
        )

    search_args: dict[str, Any] = {"limit": 8}
    for key in ("city", "job_type", "min_score"):
        if key in slots:
            search_args[key] = slots[key]
    keyword = _extract_keyword(text)
    if keyword:
        search_args["keyword"] = keyword

    calls = [PlannedCall("search_jobs", search_args, "默认意图是找岗位")]
    gap_args: dict[str, Any] = {"min_score": slots.get("min_score", 55), "limit": 5}
    calls.append(PlannedCall("skill_gaps", gap_args, "检索之后补一步缺口聚合，让回答能落到行动"))
    return Plan(intent="search", calls=calls, slots=slots)


def _extract_keyword(text: str) -> str | None:
    """抽一个技术关键词：优先英文缩写，其次中文赛道词。"""
    ascii_token = re.search(r"[A-Za-z][A-Za-z0-9\+\#\.]{1,12}", text)
    if ascii_token:
        return ascii_token.group(0)
    for word in ("大模型", "推荐", "音视频", "可视化", "数据平台", "测试", "算法"):
        if word in text:
            return word
    return None


def compose_answer(plan: Plan, results: list[dict[str, Any]]) -> str:
    """用**真实返回数据**拼答复。没有数据就直说没有，不编。"""
    if not results:
        return "没有拿到任何工具结果，无法回答。"

    first = results[0]

    if plan.intent == "job_detail":
        if not first.get("found"):
            return first.get("message", "未找到该岗位。")
        job = first["job"]
        analysis = first["analysis"]
        salary = (
            f"{job['salaryMin']}-{job['salaryMax']}K"
            if job.get("salaryMax", 0) > 0
            else "面议"
        )
        missing = "、".join(analysis.get("missingSkills") or []) or "无"
        return (
            f"《{job['title']}》· {job['company']} · {job['city']} · {salary}，"
            f"匹配分 {analysis.get('matchScore')}（{analysis.get('model')}）。"
            f"缺口：{missing}。建议：{analysis.get('advice') or '无'}"
        )

    if plan.intent == "market_stats":
        buckets = first.get("buckets", [])
        head = "、".join(f"{b['key']} {b['count']} 条" for b in buckets[:5])
        avg = first.get("avgScore")
        return (
            f"共 {first.get('total')} 条岗位，平均匹配分 {avg}。"
            f"按{ {'city': '城市', 'type': '类型', 'group': '公司集团'}[first['group_by']] }分布前五：{head}。"
        )

    if plan.intent == "skill_gaps":
        gaps = first.get("gaps", [])
        if not gaps:
            return f"{first.get('min_score')} 分以上岗位没有记录缺口。"
        top = "、".join(f"{g['skill']}（{g['jobs']} 个岗位）" for g in gaps[:5])
        return (
            f"在 {first.get('jobs_considered')} 条 {first.get('min_score')} 分以上的岗位里，"
            f"最常出现的缺口是：{top}。"
        )

    items = first.get("items", [])
    if not items:
        return "按当前条件没有筛到岗位，可以放宽城市或分数阈值。"

    lines = [
        f"筛到 {first.get('total')} 条，返回前 {first.get('returned')} 条："
        + "；".join(f"{it['title']}（{it['score']} 分）" for it in items[:3])
    ]
    if len(results) > 1 and results[1].get("gaps"):
        gaps = "、".join(g["skill"] for g in results[1]["gaps"][:3])
        lines.append(f"这些岗位的高频缺口：{gaps}。")
    best = items[0]
    if best.get("advice"):
        lines.append(f"最值得先看的是《{best['title']}》：{best['advice']}")
    return " ".join(lines)
