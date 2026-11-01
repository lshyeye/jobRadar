"""规则打分通道。

LLM 有外部依赖（key、配额、网络），一旦不可用不能让整个功能空窗，
所以这里有一套纯规则的打分：技能覆盖率 + 方向契合 + 城市 + 年限，
最后乘上岗位角色惩罚系数。

必须写清楚的局限：**它不懂语义**。它能判断「JD 要的技能你有没有」，
判断不了团队背景、业务前景、隐性要求。分数的相对排序可信，绝对值参考意义有限——
前端也因此把规则分和 LLM 分分开呈现，绝不混排。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .classify import ENGINEERING_TYPES, role_penalty
from .normalize import canonical_skill, extract_requirements
from .types import MatchResult

MODEL_NAME = "heuristic-v1"
CALIBRATION_EXPONENT = 1.4
SKILL_LEVEL_WEIGHT = {"expert": 1.0, "proficient": 0.8, "familiar": 0.6}
YEARS = re.compile(r"(\d{1,2})\s*年(?:以上|及以上|\+)?")

# 覆盖率平滑（贝叶斯加性平滑）：coverage' = (命中权重 + K·PRIOR) / (标签数 + K)
#
# 为什么需要它：分母是标签数，JD 只写 1 个技能时命中 1 个就是 100% 覆盖，
# 于是「前端开发实习生」（JD 只提 1 个技术词）拿到 95 分，读起来像完美匹配。
# 这本质上是**证据量不足**，不是匹配度高。平滑让证据少的岗位回归先验附近，
# 证据多的岗位才允许逼近真实覆盖率。K=2 表示「先假想先看 2 个标签、其中
# 0.2 覆盖率」，数量级取自标签数的中位数（3~5）——是标定值，不是随手取的。
COVERAGE_PRIOR_K = 2.0
COVERAGE_PRIOR = 0.2
COVERAGE_NO_TAG = 0.25

# 命中一票否决条件后的分数上限。
# 不是 0：0 分会被前端当成「无数据」处理，这里要的是「明确不投」。
DEAL_BREAKER_CAP = 12


def deal_breaker_hit(job: dict[str, Any], profile: dict[str, Any]) -> str:
    """检查岗位是否命中画像里的一票否决条件，命中返回该条件，否则返回空串。

    两条设计约束：

    1. **只匹配 title，不匹配正文。** 正文里「非外包」「不长期出差」这类否定表述
       非常常见，用正文匹配会大面积误杀。宁可漏杀，不可错杀。
    2. **整短语匹配。** 早期版本把短语按空格切开逐段匹配，结果
       「需要 5 年以上管理经验」切出了「需要」，任何标题带「需要」的岗位都被毙掉。
       像这种**描述性条件本来就不是标题特征**——年限由 `_seniority_fit` 处理，
       管理向由 `role_penalty` 处理。这里只负责「标题里能一眼看出的一票否决」。

    这个函数是补的实现——`dealBreakers` 之前只写在 profile 配置里，
    **没有任何代码读过它**，属于悬空字段。配置声明了却不生效，
    比没有这个配置更糟：使用者会以为自己设的底线在起作用。
    """
    breakers = profile.get("dealBreakers") or []
    title = (job.get("title") or "").lower().replace(" ", "")
    if not title:
        return ""
    for raw in breakers:
        phrase = str(raw or "").strip().lower().replace(" ", "")
        if len(phrase) >= 2 and phrase in title:
            return str(raw)
    return ""


def profile_skill_weights(profile: dict[str, Any]) -> dict[str, float]:
    """把画像里的技能分级展开成 {规范名: 权重}。

    走 `canonical_skill` 归一，原因是画像由人手写（`Prompt Engineering`），
    而 JD 侧的标签是词表规范名（`Prompt工程`）。不归一就会静默失配。
    同一规范名出现在多个等级时取较高权重。
    """
    skills = profile.get("skills", {}) or {}
    weights: dict[str, float] = {}
    for level, factor in SKILL_LEVEL_WEIGHT.items():
        for name in skills.get(level, []) or []:
            canonical = canonical_skill(str(name))
            if not canonical:
                continue
            weights[canonical] = max(weights.get(canonical, 0.0), factor)
    return weights


def _normalize_role(name: str) -> str:
    return (name or "").replace(" ", "")


def _seniority_fit(description: str, years: int) -> float:
    """年限契合：写在 JD 里的年限要求 ≤ 候选人年限算契合。"""
    matches = [int(m) for m in YEARS.findall(description or "")]
    if not matches:
        return 0.7  # JD 没写年限，中性偏保守
    required = max(matches)
    if required <= years:
        return 1.0
    return round(max(0.15, 1.0 - (required - years) * 0.25), 3)


def score_job(job: dict[str, Any], profile: dict[str, Any]) -> MatchResult:
    """给一条岗位打分。输出结构与 LLM 通道完全一致。"""
    weights = profile.get("weights", {}) or {}
    w_skill = float(weights.get("skillMatch", 0.45))
    w_direction = float(weights.get("direction", 0.25))
    w_city = float(weights.get("city", 0.15))
    w_years = float(weights.get("seniority", 0.15))

    profile_skills = profile_skill_weights(profile)
    job_skills = [s for s in (job.get("req") or [])]

    matched = [s for s in job_skills if s in profile_skills]
    missing = [s for s in job_skills if s not in profile_skills]
    if job_skills:
        hit_weight = sum(profile_skills[s] for s in matched)
        coverage = (hit_weight + COVERAGE_PRIOR_K * COVERAGE_PRIOR) / (
            len(job_skills) + COVERAGE_PRIOR_K
        )
    else:
        coverage = COVERAGE_NO_TAG  # 没抽到技能标签：不惩罚到底，但也不给高分

    targets = {_normalize_role(r) for r in (profile.get("targetRoles") or [])}
    job_type = job.get("type", "其他")
    if job_type in ENGINEERING_TYPES and _normalize_role(job_type) in targets:
        direction = 1.0
    elif job_type in ENGINEERING_TYPES:
        direction = 0.7
    else:
        direction = 0.2

    cities = set(profile.get("targetCities") or [])
    city = job.get("city", "")
    if city and city in cities:
        city_fit = 1.0
    elif not city:
        city_fit = 0.5
    else:
        city_fit = 0.25

    years = int(profile.get("years", 3) or 3)
    years_fit = _seniority_fit(job.get("description", ""), years)
    hard, bonus = extract_requirements(job.get("description", ""))

    base = w_skill * coverage + w_direction * direction + w_city * city_fit + w_years * years_fit
    # 校准曲线：线性加权会让「技能命中中等」的岗位直接拿到 50+，
    # 与真实分布不符——一批 900 条岗位里真正值得投的是少数。
    # 指数 1.4 是在同一批数据（fixtures 913 条）上标定的，
    # 标定结果是中位数 ≈ 29、≥55 分占 14%，不是拍脑袋的魔数。
    base = base ** CALIBRATION_EXPONENT
    penalty = role_penalty(job.get("title", ""))
    # 上限压在 95：规则分刻意不给满分。100 分会让绝对刻度被误读成「完美匹配」，
    # 而规则通道根本不看团队、业务与隐性要求，给不出满分结论。
    score = max(0, min(95, round(100 * base * penalty)))

    # 一票否决放在最后：它盖过一切加权结果。
    breaker = deal_breaker_hit(job, profile)
    if breaker:
        score = min(score, DEAL_BREAKER_CAP)

    return MatchResult(
        jobId=job["url"],
        matchScore=score,
        # hardReqs / bonusReqs 放**正文里的要求句**，不放技能名。
        # 技能名已经在 matchedSkills / missingSkills 里了，塞这里会重复且错位
        # （前端把它们渲染在「硬性要求」标题下）。
        hardReqs=hard[:5],
        bonusReqs=bonus[:3],
        matchedSkills=matched,
        missingSkills=missing,
        advice=_advice(score, penalty, job_skills, matched, missing, breaker),
        analyzedAt=datetime.now().strftime("%Y-%m-%d %H:%M"),
        model=MODEL_NAME,
    )


def _advice(
    score: int,
    penalty: float,
    job_skills: list[str],
    matched: list[str],
    missing: list[str],
    breaker: str = "",
) -> str:
    """一句话建议。措辞只依据分数与缺口，不编造团队/业务信息。

    这里有三个早期版本的病句/错句，都靠显式分支修掉了：
    1. 缺口为空时直接套模板 → 产出「缺口集中在 无明显技能缺口」；
    2. 只把 `missing` 传进来判空 → **JD 一个技能标签都没抽到时 missing 也是空**，
       于是落进「全部命中」分支，打出「技能要求基本命中」——而实际 matchedSkills 是空的。
       第 2 个是本次修的真 bug，所以这里显式接收 job_skills 与 matched；
    3. 命中一票否决时不说明原因，只给一个低分，使用者会以为算错了。
    """
    if breaker:
        return f"建议跳过：标题命中你在画像里设的一票否决条件「{breaker}」。"
    if penalty < 0.9:
        return "不建议投：岗位角色偏非研发向，技术栈即便部分匹配也与你的方向不符。"
    if not job_skills:
        return "信息不足：JD 未提取到可判定的技能标签，建议人工看一遍正文再决定。"
    if not missing:
        if score >= 70:
            return f"值得投：JD 要求的 {len(matched)} 项技能全部命中，可直接投递并准备项目深挖。"
        return "可投：技能要求基本命中，但方向/城市契合度一般，建议结合业务方向判断。"
    gap = "、".join(missing[:2])
    if score >= 70:
        return f"值得投：技能覆盖较好，面试前重点补 {gap}。"
    if score >= 55:
        return f"可投：核心栈部分匹配，需先补 {gap} 再投更稳。"
    if score >= 35:
        return f"谨慎：缺口集中在 {gap}，短期难补齐，优先级放后。"
    return "不建议投：匹配度低，技能与方向缺口都较大。"


def score_all(jobs: list[dict[str, Any]], profile: dict[str, Any]) -> list[MatchResult]:
    return [score_job(job, profile) for job in jobs]
