"""岗位分类与岗位角色校准。

这里有两个踩过的坑，都写进了代码结构里：

**坑一：只看技术词判岗位类型。**
第一版把「AI Agent 产品经理」判成 Agent 开发岗、给到 87 分，因为它满篇都是技术词。
所以分类看 title 优先，且 **title 里的非研发角色词是最高优先级**——先排除，再分类。

**坑二：把「大模型」当分类主信号。**
第二版把 `大模型/llm/agent` 放在匹配链最前，结果 913 条里 417 条被吞进 Agent 应用开发：
现在几乎每份 JD 都会提一句大模型，这个词已经没有区分度了。
所以分类主链只用 title，标签（req）只在 title 是泛研发词时作兜底推断。

分类优先级（自上而下，先命中先返回）：
    1. title 是非研发岗（运营/设计/销售/策划…）  → 其他
    2. title 显式写全栈                          → 全栈
    3. title 显式写前端类                        → 前端
    4. title 显式写 AI/Agent 类                  → Agent应用开发
    5. title 是泛研发词（开发/工程师/研发）       → 按技能标签推断栈
    6. 其余                                      → 其他
"""

from __future__ import annotations

import re

from .lexicon import (
    AI_TITLE_WORDS,
    BACKEND_STACK,
    FRONTEND_STACK,
    FRONTEND_TITLE_WORDS,
    FULLSTACK_TITLE_WORDS,
    GENERIC_DEV_TITLE_WORDS,
    NON_TARGET_TITLE_WORDS,
    NON_TECH_TITLE_WORDS,
    ROLE_PENALTY_MILD,
    ROLE_PENALTY_STRONG,
)

ENGINEERING_TYPES = ("前端", "全栈", "Agent应用开发")

TYPE_FALLBACK = "其他"

# 分类器版本号，写进 stats.json 的 meta。
# 为什么要写：同一份数据，LLM 打标和规则打标产出的 byType 分布差很多
# （参考数据 LLM 给出 全栈 182 条，规则只认 14 条），不标明来源就没法解释差异。
CLASSIFIER_VERSION = "heuristic-title-v1"

# 独立的 "ai" 词（「AI编译器」「AI 平台」）。
# 不能用子串匹配——`"ai" in "training"` 是 True，会把一堆英文标题判成 AI 岗。
_STANDALONE_AI = re.compile(r"(?<![a-z])ai(?![a-z])")

# 「AI 词 + 岗位主名词」紧邻才算强信号。
# 这条规则解决的是一个真实歧义：
#   「计算机视觉算法实习生（移动端AIGC方向）」→ 算法是主名词，移动端是方向 → AI 岗
#   「前端研发工程师（视频生成 AI ）」→ 前端是主名词，AI 是方向     → 前端岗
# 两者都同时命中「移动端/AI」和「算法」，靠固定优先级必然错一个，
# 所以改用「紧邻主名词」判断谁是主名词。
_ROLE_NOUN = r"(?:工程师|开发|研发|专家|实习生|实习|岗|工程)"
_STRONG_AI_ROLE = re.compile(
    r"(?:算法|大模型|llm|agent|智能体|aigc|机器学习|深度学习|强化学习|多模态|推荐算法|nlp|ai)"
    r"[\s\-/]*"
    + _ROLE_NOUN
)


def _hit(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _hit_ai(text: str) -> bool:
    """AI 类信号：词表命中，或者出现独立的 ai 词。"""
    return _hit(text, AI_TITLE_WORDS) or bool(_STANDALONE_AI.search(text))


def _hit_ai_role(text: str) -> bool:
    """AI 词紧邻岗位主名词（「算法工程师」「agent 开发」）。"""
    return bool(_STRONG_AI_ROLE.search(text))


def stack_of(tags: list[str] | tuple[str, ...]) -> str:
    """按技能标签判断技术栈：both / frontend / backend / none。

    只看标签而不是看正文——正文里「与后端协作」这类协作描述会污染判断。
    """
    blob = " ".join(str(t) for t in tags).lower()
    if not blob:
        return "none"
    has_frontend = any(word in blob for word in FRONTEND_STACK)
    has_backend = any(word in blob for word in BACKEND_STACK)
    if has_frontend and has_backend:
        return "both"
    if has_frontend:
        return "frontend"
    if has_backend:
        return "backend"
    return "none"


def classify_type(
    title: str,
    description: str = "",
    tags: list[str] | tuple[str, ...] | None = None,
) -> str:
    """判定岗位类型：前端 / 全栈 / Agent应用开发 / 其他。

    `description` 参数保留是为了兼容旧调用方，**不参与主判定**；
    真正的兜底证据是 `tags`（归一化阶段抽出的规范技能名）。
    """
    title_low = (title or "").lower()
    if not title_low:
        return TYPE_FALLBACK

    # 1. 非研发岗先排除。放在最前面，因为「Agent 产品经理」「大模型运营」
    #    会同时命中后面的技术词，必须先拦掉。
    if _hit(title_low, NON_TECH_TITLE_WORDS):
        return TYPE_FALLBACK

    # 2. title 显式声明类型，顺序有讲究：
    #    全栈 → AI 主名词 → 前端 → 其余 AI 词。
    #    「AI 主名词」必须在「前端」之前，否则「算法工程师（移动端方向）」会被抢走；
    #    但它必须是**紧邻**判定，否则「前端工程师（AI 方向）」会被反抢。
    if _hit(title_low, FULLSTACK_TITLE_WORDS):
        return "全栈"
    if _hit_ai_role(title_low):
        return "Agent应用开发"
    if _hit(title_low, FRONTEND_TITLE_WORDS):
        return "前端"
    if _hit_ai(title_low):
        return "Agent应用开发"

    # 3. 技术岗但不在狩猎范围（测试/运维/DBA）。
    #    放在前端之后：「客户端测试开发工程师」仍应算前端。
    if _hit(title_low, NON_TARGET_TITLE_WORDS):
        return TYPE_FALLBACK

    # 4. 泛研发词 + 标签兜底推断
    if _hit(title_low, GENERIC_DEV_TITLE_WORDS):
        stack = stack_of(tags or ())
        if stack == "both":
            return "全栈"
        if stack == "frontend":
            return "前端"
        # 纯后端或判不出栈：本项目的目标方向是前端 + Agent，
        # 后端岗归「其他」而不是硬塞一个类别——硬塞会让前端/Agent 的池子失真。
        return TYPE_FALLBACK

    return TYPE_FALLBACK


def role_penalty(title: str) -> float:
    """岗位角色惩罚系数：1.0 不打折。

    0.55 = 非研发岗（产品/运营/销售…），0.78 = 管理或研究向
    （总监/负责人/架构师/研究员）。数值来自对同一批数据的分布调参——
    目标是让「技术词堆满但岗不对」的岗位排到研发岗后面。
    """
    title_low = (title or "").lower()
    if _hit(title_low, ROLE_PENALTY_STRONG):
        return 0.55
    if _hit(title_low, ROLE_PENALTY_MILD):
        return 0.78
    return 1.0


def is_engineering_role(title: str) -> bool:
    return role_penalty(title) >= 0.99
