"""归一化：把各家招聘站的原始字段，变成统一形状的 Job。

这里的每个函数都是**纯函数**，不碰网络、不读文件——所以能直接写单测。
管线最容易出 bug 的地方就在这里（同一城市三种写法、薪资单位不统一、
日期格式五种并存），因此把它单独拎出来。

出现「同一城市三种写法」会导致前端筛选失真，这是刻意修的已知 bug。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from .lexicon import CITY_CANON, GROUP_RULES, SKILL_ALIASES
from .types import SourceConfig

DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"),
    re.compile(r"^(\d{1,2})[-/.](\d{1,2})$"),
)

SALARY_RANGE = re.compile(r"(\d{1,3})\s*[-~至]\s*(\d{1,3})\s*[kK千]")
SALARY_MONTHS = re.compile(r"(\d{1,3})\s*[kK千]")


def normalize_city(raw: str | None) -> str:
    """城市归一化。未知城市原样返回（不猜测），空值返回空串。"""
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    key = text.lower()
    if key in CITY_CANON:
        return CITY_CANON[key]
    # "北京·海淀" / "北京/上海" 这类带修饰的写法，取第一个能识别的城市
    for token in re.split(r"[·/,、\s（）()]+", text):
        if token and token.lower() in CITY_CANON:
            return CITY_CANON[token.lower()]
    return text


def parse_salary(raw_min: Any, raw_max: Any) -> tuple[int, int]:
    """薪资归一化为「月薪 K」。0 表示面议。"""
    low = _to_number(raw_min)
    high = _to_number(raw_max)
    if high and not low:
        low = high
    if low and high and low > high:
        low, high = high, low
    return low, high


def _to_number(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def parse_salary_text(text: str | None) -> tuple[int, int]:
    """从「20-40K」「30K 起」这类文本里提薪资，兜底用。"""
    if not text:
        return 0, 0
    match = SALARY_RANGE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    single = SALARY_MONTHS.search(text)
    if single:
        value = int(single.group(1))
        return value, value
    return 0, 0


def parse_date(raw: Any, today: date | None = None) -> tuple[str, str]:
    """返回 (原始展示文本, 归一化 YYYY-MM-DD)。

    支持：毫秒时间戳、2026-10-30、2026/10/30、10-30（补当前年，跨年则退一年）。
    """
    ref = today or date.today()
    if raw is None or raw == "":
        return "", ""

    text = str(raw).strip()

    # 毫秒 / 秒时间戳
    if text.isdigit() and len(text) in (10, 13):
        stamp = int(text) / (1000 if len(text) == 13 else 1)
        try:
            parsed = datetime.fromtimestamp(stamp).date()
            return parsed.isoformat(), parsed.isoformat()
        except (OverflowError, OSError, ValueError):
            return text, ""

    full = DATE_PATTERNS[0].match(text)
    if full:
        year, month, day = (int(x) for x in full.groups())
        try:
            parsed = date(year, month, day)
        except ValueError:
            return text, ""
        return parsed.isoformat(), parsed.isoformat()

    short = DATE_PATTERNS[1].match(text)
    if short:
        month, day = (int(x) for x in short.groups())
        try:
            parsed = date(ref.year, month, day)
        except ValueError:
            return text, ""
        # 只写「月-日」且日期在未来 → 说明是去年的岗位
        if parsed > ref + timedelta(days=1):
            parsed = date(ref.year - 1, month, day)
        return text, parsed.isoformat()

    return text, ""


def extract_skills(*texts: str) -> list[str]:
    """从标题/正文里抽技能标签，输出规范名（去重、保持出现顺序）。"""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return []
    seen: list[str] = []
    for canonical, aliases in SKILL_ALIASES.items():
        if any(alias in blob for alias in aliases) and canonical not in seen:
            seen.append(canonical)
    return seen


def canonical_skill(name: str) -> str:
    """把**人写的技能名**解析成词表规范名。

    为什么需要这层：画像配置是人手写的（`Prompt Engineering`、`Vite`、`SQL`），
    词表里对应的规范名却是 `Prompt工程`、`前端工程化`、`数据库`。
    直接拿字符串比对会**静默失配**——技能明明会，却永远算不中，分数偏低还不报错。
    这是实测到的真问题（画像 13 项里有 3 项从未命中过）。

    匹配顺序：规范名精确 → 别名精确 → 别名包含。
    """
    raw = (name or "").strip()
    key = raw.lower()
    if not key:
        return ""
    for canonical in SKILL_ALIASES:
        if canonical.lower() == key:
            return canonical
    for canonical, aliases in SKILL_ALIASES.items():
        if any(alias == key for alias in aliases):
            return canonical
    for canonical, aliases in SKILL_ALIASES.items():
        if any(alias in key for alias in aliases):
            return canonical
    return raw


# 要求句的切分与识别
_SENTENCE_SPLIT = re.compile(r"[。；;！!\n]+")
HARD_REQ_HINTS = (
    "年以上", "经验", "本科", "硕士", "博士", "学历",
    "熟悉", "精通", "掌握", "了解", "具备", "任职要求", "岗位要求",
)
BONUS_REQ_HINTS = ("优先", "加分", "nice to have", "尤佳", "更好")


def extract_requirements(description: str, limit: int = 5) -> tuple[list[str], list[str]]:
    """从 JD 正文里抽「硬性要求」与「加分项」两句列表。

    规则通道不解析语义，所以这里只做**句子级筛选**：按句号/分号切开，
    含「熟悉/精通/年以上/本科」的句子算硬性要求，含「优先/加分」的算加分项。
    抽不到就返回空列表——空着比塞错东西好（早期版本把技能名塞进 hardReqs，
    前端「硬性要求」里显示的是一串 TypeScript / Java，既重复又错位）。
    """
    text = (description or "").strip()
    if not text:
        return [], []
    hard: list[str] = []
    bonus: list[str] = []
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = raw.strip(" 　\t-·:：")
        if not (8 <= len(sentence) <= 80):
            continue
        low = sentence.lower()
        if any(hint in low for hint in BONUS_REQ_HINTS):
            if len(bonus) < limit:
                bonus.append(sentence)
            continue
        if any(hint in low for hint in HARD_REQ_HINTS) and len(hard) < limit:
            hard.append(sentence)
    return hard, bonus


def infer_group(company: str) -> str:
    """按公司名推断集团归类，判不出来归「其他互联网」。"""
    name = (company or "").lower()
    for group, keywords in GROUP_RULES:
        if any(kw.lower() in name for kw in keywords):
            return group
    return "其他互联网"


def normalize_job(raw: dict[str, Any], source: SourceConfig, today: date | None = None) -> dict[str, Any] | None:
    """把一条原始记录映射成 Job。缺关键字段返回 None（不猜、不补）。"""
    mapping = source.field_map

    def pick(field: str) -> Any:
        key = mapping.get(field)
        return raw.get(key) if key else None

    title = str(pick("title") or "").strip()
    url = str(pick("url") or "").strip()
    if not title or not url:
        return None

    city = normalize_city(pick("city"))
    salary_min, salary_max = parse_salary(pick("salaryMin"), pick("salaryMax"))
    if salary_max == 0:
        salary_min, salary_max = parse_salary_text(str(pick("salaryText") or ""))
    posted, posted_date = parse_date(pick("postedDate"), today)

    description = str(pick("description") or "")
    company = str(pick("company") or "").strip() or "未标注"

    return {
        "company": company,
        "group": infer_group(company),
        "title": title,
        "type": "",  # 由 classify 填充
        "city": city,
        "salaryMin": salary_min,
        "salaryMax": salary_max,
        "posted": posted,
        "postedDate": posted_date,
        "req": extract_skills(title, description),
        "url": url,
        "source": source.id,
        "description": description,
    }
