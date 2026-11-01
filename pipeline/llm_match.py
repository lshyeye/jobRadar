"""LLM 打分通道。

三条工程约束，比 prompt 写得多漂亮重要：

1. **输出必须过结构校验**。模型返回的 JSON 不可信（缺字段、分数给成字符串、
   数组里混进 null），校验不过就重试，重试还不过就降级到规则通道。
2. **重试有上限**（默认 2 次）。无限重试会把一次跑批拖成几小时。
3. **失败可降级、不中断**。单条岗位分析失败不能让整批数据空窗，
   降级后的结果带 ``heuristic`` 前缀，前端据此不把它当 LLM 分。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent.llm import LLMUnavailable, chat, load_config

from . import heuristic_match
from .types import MatchResult

MODEL_TAG = "llm"
MAX_RETRY = 2
DESCRIPTION_LIMIT = 1600
REQUIRED_KEYS = (
    "matchScore",
    "hardReqs",
    "bonusReqs",
    "matchedSkills",
    "missingSkills",
    "advice",
)

SYSTEM_PROMPT = """你是招聘匹配度分析器。只输出一个 JSON 对象，不要 markdown 代码块，不要解释。

JSON 结构：
{"matchScore": 0-100 的整数,
 "hardReqs": ["JD 中的硬性要求"],
 "bonusReqs": ["加分项"],
 "matchedSkills": ["候选人画像命中项"],
 "missingSkills": ["候选人缺口"],
 "advice": "一句话投递建议（不超过 50 字）"}

规则：只依据给定 JD 与画像判断，不臆测团队与业务；缺口必须来自 JD 要求。"""


def _build_prompt(job: dict[str, Any], profile: dict[str, Any]) -> str:
    skills = profile.get("skills", {}) or {}
    profile_text = "；".join(
        f"{level}: {', '.join(skills.get(level, []) or [])}" for level in ("expert", "proficient", "familiar")
    )
    description = (job.get("description") or "")[:DESCRIPTION_LIMIT]
    return (
        f"候选人画像：{profile.get('years', 3)} 年经验，当前 {profile.get('currentRole', '')}，"
        f"目标 {', '.join(profile.get('targetRoles', []) or [])}，"
        f"目标城市 {', '.join(profile.get('targetCities', []) or [])}。\n"
        f"技能：{profile_text}\n\n"
        f"岗位：{job.get('title')} · {job.get('company')} · {job.get('city')}\n"
        f"岗位要求：{'、'.join(job.get('req') or []) or '（原文见下）'}\n"
        f"JD 原文：\n{description or '（无正文，仅依据标题与标签判断）'}"
    )


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """严格校验模型输出。任何一项不合格都抛 ValueError，由上层决定重试还是降级。"""
    if not isinstance(payload, dict):
        raise ValueError("输出不是 JSON 对象")
    for key in REQUIRED_KEYS:
        if key not in payload:
            raise ValueError(f"缺少字段 {key}")

    raw_score = payload["matchScore"]
    try:
        score = int(raw_score)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"matchScore 不是整数：{raw_score!r}") from exc
    if not 0 <= score <= 100:
        raise ValueError(f"matchScore 超出 0-100：{score}")

    cleaned: dict[str, Any] = {"matchScore": score}
    for key in ("hardReqs", "bonusReqs", "matchedSkills", "missingSkills"):
        value = payload[key]
        if not isinstance(value, list):
            raise ValueError(f"{key} 不是数组")
        cleaned[key] = [str(item) for item in value if item is not None][:12]

    advice = str(payload.get("advice") or "").strip()
    if not advice:
        raise ValueError("advice 为空")
    cleaned["advice"] = advice[:120]
    return cleaned


def _parse_json(content: str) -> dict[str, Any]:
    """从模型回复里抠出 JSON（容忍 ```json 包裹与前后噪声）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text
        text = text.lstrip("json").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回复里没有 JSON 对象")
    return json.loads(text[start : end + 1])


def score_job(job: dict[str, Any], profile: dict[str, Any], model_name: str) -> MatchResult:
    """LLM 打分，失败自动降级为规则分（并在 model 上留痕）。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(job, profile)},
    ]
    last_error = ""
    for attempt in range(MAX_RETRY + 1):
        try:
            response = chat(messages)
            payload = _parse_json(response.get("content", ""))
            cleaned = validate_payload(payload)
            return MatchResult(
                jobId=job["url"],
                matchScore=cleaned["matchScore"],
                hardReqs=cleaned["hardReqs"],
                bonusReqs=cleaned["bonusReqs"],
                matchedSkills=cleaned["matchedSkills"],
                missingSkills=cleaned["missingSkills"],
                advice=cleaned["advice"],
                analyzedAt=datetime.now().strftime("%Y-%m-%d %H:%M"),
                model=model_name,
            )
        except (LLMUnavailable, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            # 端点不可用就别再重试了，直接降级——重试只会浪费时间
            if isinstance(exc, LLMUnavailable):
                break

    fallback = heuristic_match.score_job(job, profile)
    fallback.model = f"{heuristic_match.MODEL_NAME} (llm-fallback: {last_error[:48]})"
    return fallback


def available() -> bool:
    return load_config() is not None


def score_all(
    jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    limit: int | None = None,
) -> list[MatchResult]:
    """批量打分。``limit`` 用于控制成本——只分析重点岗位。"""
    config = load_config()
    model_name = config.model if config else MODEL_TAG
    targets = jobs[:limit] if limit else jobs
    results: list[MatchResult] = []
    for job in targets:
        results.append(score_job(job, profile, model_name))
    return results
