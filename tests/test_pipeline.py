"""管线测试：归一化、分类、打分、适配器、聚合。

只依赖标准库 unittest，不引入 pytest——保持「clone 下来就能跑」。
    python -m unittest discover -s tests -v

这里的用例几乎每一条都对应一个**踩过的坑**，而不是为了凑覆盖率：

- 同一城市三种写法（Beijing / 北京市 / 北京）让前端筛选失真
- 「Agent 产品经理」被判成开发岗并拿到 87 分
- 「大模型」当分类主信号，把 913 条里 417 条吞进 Agent 应用开发
- JD 只提 1 个技能时命中 1 个就 95 分（证据量不足被当成匹配度高）
- JD 一个标签都没抽到时，建议文案打出「技能要求基本命中」
- 画像手写的 `Prompt Engineering` / `Vite` / `SQL` 与词表规范名对不上，静默失配
- `dealBreakers` 写在配置里但没有任何代码读它
- 硬性要求里塞的是技能名（TypeScript / Java），前端渲染在「硬性要求」标题下
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pipeline import heuristic_match, llm_match, normalize
from pipeline.adapters import AdapterError, _fetch_fixture, fetch_raw, load_sources
from pipeline.aggregate import build_stats
from pipeline.classify import CLASSIFIER_VERSION, classify_type, role_penalty, stack_of
from pipeline.jd_store import JdStore
from pipeline.normalize import (
    canonical_skill,
    extract_requirements,
    extract_skills,
    normalize_city,
    normalize_job,
    parse_date,
    parse_salary,
    parse_salary_text,
)
from pipeline.run import classify_all, filter_recent
from pipeline.types import PipelineOptions, SourceConfig

PROFILE = {
    "label": "测试画像",
    "years": 3,
    "targetRoles": ["Agent 应用开发", "全栈", "前端"],
    "targetCities": ["北京", "上海"],
    "skills": {
        "expert": ["TypeScript", "React", "Node.js"],
        "proficient": ["Python", "LangGraph", "MCP"],
        "familiar": ["Java", "Docker"],
    },
    "dealBreakers": ["实习", "外包驻场"],
    "weights": {"skillMatch": 0.45, "direction": 0.25, "city": 0.15, "seniority": 0.15},
}

SOURCE = SourceConfig(
    id="src_t",
    name="测试源",
    adapter="fixture",
    enabled=True,
    config={
        "fieldMap": {
            "title": "jobName",
            "company": "companyName",
            "city": "workCity",
            "salaryMin": "salaryMin",
            "salaryMax": "salaryMax",
            "postedDate": "publishDate",
            "url": "jobUrl",
            "description": "jobDescription",
        }
    },
)


def make_job(**overrides: object) -> dict:
    job = {
        "company": "公司A",
        "group": "BAT",
        "title": "前端开发工程师",
        "type": "前端",
        "city": "北京",
        "salaryMin": 0,
        "salaryMax": 0,
        "posted": "2026-10-30",
        "postedDate": "2026-10-30",
        "req": ["TypeScript", "React"],
        "url": "https://jobs.example.com/src_t/abc",
        "source": "src_t",
        "description": "任职要求：熟悉 TypeScript 与 React；有 3 年以上前端经验者优先。",
    }
    job.update(overrides)
    return job


class CityNormalizeTest(unittest.TestCase):
    """同一城市三种写法会让前端 `city.includes()` 筛选失真。"""

    def test_variants_collapse_to_one_canonical(self) -> None:
        for raw in ("Beijing", "北京市", "北京"):
            self.assertEqual(normalize_city(raw), "北京")

    def test_city_with_suffix_keeps_first_known(self) -> None:
        self.assertEqual(normalize_city("北京·海淀"), "北京")
        self.assertEqual(normalize_city("深圳/北京"), "深圳")

    def test_unknown_city_returned_as_is(self) -> None:
        # 不猜、不映射：把不认识的写成空串会让前端少一个可筛选项
        self.assertEqual(normalize_city("克拉玛依"), "克拉玛依")

    def test_empty_city(self) -> None:
        self.assertEqual(normalize_city(None), "")
        self.assertEqual(normalize_city("   "), "")


class SalaryAndDateTest(unittest.TestCase):
    def test_salary_swaps_when_reversed(self) -> None:
        self.assertEqual(parse_salary(40, 20), (20, 40))

    def test_salary_single_bound_fills_both(self) -> None:
        self.assertEqual(parse_salary(None, 30), (30, 30))

    def test_salary_text_fallback(self) -> None:
        self.assertEqual(parse_salary_text("20-40K"), (20, 40))
        self.assertEqual(parse_salary_text("30K 起"), (30, 30))
        self.assertEqual(parse_salary_text("面议"), (0, 0))

    def test_date_full_and_short(self) -> None:
        self.assertEqual(parse_date("2026-10-30")[1], "2026-10-30")
        self.assertEqual(parse_date("2026/10/30")[1], "2026-10-30")

    def test_short_date_in_future_means_last_year(self) -> None:
        # 「12-30」在今天（10-30）之后 → 只能是去年的岗位，不能当成今年
        _, normalized = parse_date("12-30", today=date(2026, 10, 30))
        self.assertEqual(normalized, "2025-12-30")

    def test_invalid_date_returns_empty_normalized(self) -> None:
        self.assertEqual(parse_date("2026-02-31")[1], "")


class SkillExtractTest(unittest.TestCase):
    def test_extracts_canonical_names(self) -> None:
        skills = extract_skills("前端开发", "熟悉 React 和 TypeScript，了解 Kubernetes")
        self.assertIn("React", skills)
        self.assertIn("TypeScript", skills)
        self.assertIn("Kubernetes", skills)

    def test_profile_skill_names_resolve_to_canonical(self) -> None:
        """画像由人手写，词表是规范名——不做归一就会静默失配（实测 13 项里 3 项从未命中）。"""
        cases = {
            "Prompt Engineering": "Prompt工程",
            "Vite": "前端工程化",
            "SQL": "数据库",
            "TypeScript": "TypeScript",
        }
        for handwritten, canonical in cases.items():
            self.assertEqual(canonical_skill(handwritten), canonical)

    def test_unknown_skill_passes_through(self) -> None:
        self.assertEqual(canonical_skill("量子计算"), "量子计算")


class RequirementExtractTest(unittest.TestCase):
    def test_splits_hard_and_bonus(self) -> None:
        text = "任职要求：熟悉 React、TypeScript，3 年以上前端经验。有 Agent 项目经验者优先。"
        hard, bonus = extract_requirements(text)
        self.assertTrue(any("熟悉 React" in s for s in hard))
        self.assertTrue(any("优先" in s for s in bonus))

    def test_empty_description_returns_empty(self) -> None:
        self.assertEqual(extract_requirements(""), ([], []))

    def test_short_fragments_ignored(self) -> None:
        # 过短的碎片不是要求句，塞进「硬性要求」只会变成噪音
        hard, _ = extract_requirements("熟悉 Java。好。可以。")
        self.assertFalse(any(len(s) < 8 for s in hard))


class ClassifyTest(unittest.TestCase):
    """分类只认 title，标签只在 title 是泛研发词时兜底。"""

    def test_non_tech_role_beats_tech_keywords(self) -> None:
        # 这是踩过的坑：满篇技术词的岗位被当成开发岗，还给了 87 分
        for title in ("AI Agent 产品经理", "大模型运营专员", "游戏动画师", "3D特效师"):
            self.assertEqual(classify_type(title, "熟悉 Agent、LLM、React", ["Agent", "LLM"]), "其他")

    def test_explicit_types(self) -> None:
        self.assertEqual(classify_type("支付业务-全栈开发工程师"), "全栈")
        self.assertEqual(classify_type("前端开发工程师"), "前端")
        self.assertEqual(classify_type("大模型算法工程师"), "Agent应用开发")

    def test_ai_role_noun_beats_direction_word(self) -> None:
        # 「算法」是主名词，「移动端」是方向 → 应是 AI 岗
        self.assertEqual(
            classify_type("计算机视觉算法实习生（移动端AIGC方向）"), "Agent应用开发"
        )

    def test_direction_word_does_not_steal_frontend_role(self) -> None:
        # 「前端」是主名词，「视频生成 AI」是方向 → 应是前端岗
        self.assertEqual(classify_type("前端研发工程师（视频生成 AI ）"), "前端")

    def test_test_dev_is_not_fullstack(self) -> None:
        # 泛研发词 + 前后端标签都命中，不能因此判成全栈
        self.assertEqual(classify_type("测试开发实习生", "", ["TypeScript", "Java"]), "其他")

    def test_client_side_is_frontend(self) -> None:
        for title in ("移动端开发工程师", "Android客户端开发工程师", "客户端开发（战斗）"):
            self.assertEqual(classify_type(title), "前端")

    def test_generic_dev_infers_stack_from_tags(self) -> None:
        self.assertEqual(classify_type("软件开发", "", ["React", "Java"]), "全栈")
        self.assertEqual(classify_type("软件开发", "", ["React"]), "前端")
        # 纯后端：目标方向是前端 + Agent，后端岗归「其他」而不是硬塞类别
        self.assertEqual(classify_type("软件开发", "", ["Java", "MySQL"]), "其他")

    def test_standalone_ai_word_not_substring(self) -> None:
        # "ai" in "training" 是 True，不能用子串匹配
        self.assertEqual(classify_type("AI编译器开发工程师"), "Agent应用开发")
        self.assertEqual(classify_type("Training Platform Engineer"), "其他")

    def test_stack_of(self) -> None:
        self.assertEqual(stack_of(["React", "Java"]), "both")
        self.assertEqual(stack_of(["React"]), "frontend")
        self.assertEqual(stack_of(["Java"]), "backend")
        self.assertEqual(stack_of([]), "none")

    def test_classifier_version_is_exposed(self) -> None:
        self.assertTrue(CLASSIFIER_VERSION)


class RolePenaltyTest(unittest.TestCase):
    def test_non_tech_penalized(self) -> None:
        self.assertLess(role_penalty("海外发行经理"), 0.6)
        self.assertLess(role_penalty("3D特效师"), 0.6)

    def test_management_mildly_penalized(self) -> None:
        self.assertAlmostEqual(role_penalty("大模型算法专家"), 0.78)

    def test_engineering_not_penalized(self) -> None:
        self.assertEqual(role_penalty("前端开发工程师"), 1.0)


class ScoreJobTest(unittest.TestCase):
    def test_output_shape_matches_contract(self) -> None:
        result = heuristic_match.score_job(make_job(), PROFILE).to_dict()
        for key in (
            "jobId", "matchScore", "hardReqs", "bonusReqs",
            "matchedSkills", "missingSkills", "advice", "analyzedAt", "model",
        ):
            self.assertIn(key, result)
        self.assertTrue(result["model"].startswith("heuristic"))

    def test_perfect_coverage_is_not_pinned_to_95(self) -> None:
        """JD 只提 1 个技能且命中 → 不能拿满分（证据量不足不是匹配度高）。"""
        job = make_job(req=["React"], description="任职要求：熟悉 React。")
        result = heuristic_match.score_job(job, PROFILE)
        self.assertLess(result.matchScore, 90)

    def test_more_evidence_scores_higher(self) -> None:
        few = heuristic_match.score_job(make_job(req=["React"]), PROFILE).matchScore
        many = heuristic_match.score_job(
            make_job(req=["React", "TypeScript", "Node.js"]), PROFILE
        ).matchScore
        self.assertGreater(many, few)

    def test_no_tags_does_not_claim_full_match(self) -> None:
        """JD 抽不到标签时，missing 也是空——不能因此打出「技能要求基本命中」。"""
        job = make_job(req=[], description="")
        result = heuristic_match.score_job(job, PROFILE)
        self.assertNotIn("基本命中", result.advice)
        self.assertIn("信息不足", result.advice)

    def test_hard_reqs_are_sentences_not_skill_names(self) -> None:
        """硬性要求里放正文要求句，不能放技能名（会被渲染在「硬性要求」标题下）。"""
        result = heuristic_match.score_job(make_job(), PROFILE)
        for item in result.hardReqs:
            self.assertNotIn(item, ("React", "TypeScript", "Node.js"))

    def test_deal_breaker_caps_score(self) -> None:
        job = make_job(title="前端开发实习生", type="前端")
        result = heuristic_match.score_job(job, PROFILE)
        self.assertLessEqual(result.matchScore, heuristic_match.DEAL_BREAKER_CAP)
        self.assertIn("一票否决", result.advice)

    def test_deal_breaker_only_matches_title(self) -> None:
        # 正文里出现「外包」不代表这岗位是外包（「非外包」也很常见）
        job = make_job(description="本岗位为非外包驻场，任职要求：熟悉 React。")
        result = heuristic_match.score_job(job, PROFILE)
        self.assertNotIn("一票否决", result.advice)

    def test_score_is_bounded(self) -> None:
        for job in (
            make_job(),
            make_job(type="其他", city="拉萨", req=[]),
            make_job(type="前端", city="北京", req=["React", "TypeScript", "Node.js"]),
        ):
            score = heuristic_match.score_job(job, PROFILE).matchScore
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 95)


class NormalizeJobTest(unittest.TestCase):
    def test_maps_raw_fields(self) -> None:
        raw = {
            "jobName": "前端开发工程师",
            "companyName": "公司A",
            "workCity": "Beijing",
            "salaryMin": 20,
            "salaryMax": 40,
            "publishDate": "2026-10-30",
            "jobUrl": "https://jobs.example.com/src_t/x",
            "jobDescription": "任职要求：熟悉 React。",
        }
        job = normalize_job(raw, SOURCE, today=date(2026, 10, 30))
        assert job is not None
        self.assertEqual(job["city"], "北京")
        self.assertEqual((job["salaryMin"], job["salaryMax"]), (20, 40))
        self.assertEqual(job["postedDate"], "2026-10-30")
        self.assertIn("React", job["req"])

    def test_missing_title_or_url_returns_none(self) -> None:
        self.assertIsNone(normalize_job({"jobUrl": "https://x/y"}, SOURCE))
        self.assertIsNone(normalize_job({"jobName": "前端"}, SOURCE))

    def test_classify_all_fills_type(self) -> None:
        job = normalize_job(
            {"jobName": "前端开发工程师", "jobUrl": "https://jobs.example.com/a/b"},
            SOURCE,
        )
        assert job is not None
        classify_all([job])
        self.assertEqual(job["type"], "前端")


class FilterRecentTest(unittest.TestCase):
    def test_window_filter(self) -> None:
        old = make_job(postedDate="2026-01-01")
        new = make_job(postedDate="2026-10-25")
        kept, dropped_old, dropped_undated = filter_recent(
            [old, new], 30, today=date(2026, 10, 30)
        )
        self.assertEqual([j["postedDate"] for j in kept], ["2026-10-25"])
        self.assertEqual(dropped_old, 1)
        self.assertEqual(dropped_undated, 0)

    def test_undated_kept(self) -> None:
        """没给日期的岗位默认保留——宁可多一条，也别漏掉一个可能合适的。"""
        kept, _, _ = filter_recent([make_job(postedDate="")], 7, today=date(2026, 10, 30))
        self.assertEqual(len(kept), 1)


class AdapterTest(unittest.TestCase):
    def test_load_sources_skips_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"id": "a", "adapter": "fixture", "enabled": True},
                            {"id": "b", "adapter": "fixture", "enabled": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            sources = load_sources(path)
            self.assertEqual([s.id for s in sources], ["a"])

    def test_missing_fixture_raises_adapter_error(self) -> None:
        """单源不可用要变成 AdapterError，让上层记 failedSources 而不是整条崩掉。"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AdapterError):
                _fetch_fixture(SOURCE, Path(tmp))

    def test_unknown_adapter_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = SourceConfig(id="x", name="x", adapter="graphql")
            with self.assertRaises(AdapterError):
                fetch_raw(source, Path(tmp))

    def test_fixture_reads_all_matching_pages_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for page in (1, 2):
                (directory / f"src_t_page{page}.json").write_text(
                    json.dumps({"list": [{"jobName": f"p{page}"}]}), encoding="utf-8"
                )
            rows = _fetch_fixture(SOURCE, directory)
            self.assertEqual([r["jobName"] for r in rows], ["p1", "p2"])


class JdStoreTest(unittest.TestCase):
    def test_merge_counts_only_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JdStore(Path(tmp) / "jd.json")
            jobs = [make_job(url="https://jobs.example.com/a/1"), make_job(url="https://jobs.example.com/a/2")]
            self.assertEqual(store.merge(jobs), 2)
            # 同样的输入再 merge 一次，新增应为 0（幂等）
            self.assertEqual(store.merge(jobs), 0)
            self.assertEqual(store.coverage(jobs), 1.0)


class AggregateTest(unittest.TestCase):
    def _options(self) -> PipelineOptions:
        return PipelineOptions(
            sources_file=Path("unused.json"),
            profile_file=Path("unused.json"),
            out_dir=Path("unused"),
            fixture_dir=Path("unused"),
            offline=True,
        )

    def test_by_type_covers_whole_window_trend_covers_week(self) -> None:
        """byType 是整窗口，lastWeek 是「上周」这 7 天——两者语义不同，别混用。"""
        today = date(2026, 10, 30)
        jobs = [
            make_job(type="前端", postedDate="2026-10-29"),  # 本周
            make_job(type="前端", postedDate="2026-10-20"),  # 上周
            make_job(type="前端", postedDate="2026-10-01"),  # 窗口内、两周之外
            make_job(type="其他", postedDate="2026-10-29"),
        ]
        stats = build_stats(jobs, self._options(), [], today=today)
        self.assertEqual(stats["byType"]["前端"], 3)  # 整个窗口
        self.assertEqual(stats["lastWeek"]["前端"], 1)  # 只有上周
        self.assertEqual(stats["trend"]["前端"]["this"], 1)  # 只有本周

    def test_scenario_and_classifier_come_from_caller(self) -> None:
        """不能硬编码场景名——换自定义画像就会显示错的文案。"""
        stats = build_stats(
            [make_job()],
            self._options(),
            [],
            profile_label="我的画像",
            classifier=CLASSIFIER_VERSION,
        )
        self.assertEqual(stats["meta"]["scenario"], "我的画像")
        self.assertEqual(stats["meta"]["classifier"], CLASSIFIER_VERSION)

    def test_dropping_description_from_public_payload(self) -> None:
        # 正文只进 jd_details.json，不进 stats.json——否则演示数据体积翻倍
        stats = build_stats([make_job()], self._options(), [])
        self.assertNotIn("description", stats["jobs"][0])

    def test_summary_omits_salary_when_unavailable(self) -> None:
        stats = build_stats([make_job(salaryMax=0)], self._options(), [])
        self.assertNotIn("平均薪资上限", stats["requirementSummary"]["前端"])

    def test_summary_renders_salary_when_available(self) -> None:
        stats = build_stats([make_job(salaryMax=40)], self._options(), [])
        self.assertIn("40.0K", stats["requirementSummary"]["前端"])

    def test_contract_keys_present(self) -> None:
        stats = build_stats([make_job()], self._options(), ["src_x"])
        for key in (
            "meta", "lastWeek", "jobs", "byType", "byGroup",
            "trend", "topRequirements", "requirementSummary", "failedSources",
        ):
            self.assertIn(key, stats)
        self.assertEqual(stats["failedSources"], ["src_x"])


class LlmFallbackTest(unittest.TestCase):
    """LLM 通道不可用时必须自动降级到规则通道，并且**在 model 字段里说清楚**。"""

    def test_invalid_payload_rejected(self) -> None:
        with self.assertRaises(ValueError):
            llm_match.validate_payload({"matchScore": "很高"})
        with self.assertRaises(ValueError):
            llm_match.validate_payload({"matchScore": 200})

    def test_valid_payload_accepted(self) -> None:
        payload = llm_match.validate_payload(
            {
                "matchScore": 66,
                "hardReqs": ["3 年以上"],
                "bonusReqs": [],
                "matchedSkills": ["React"],
                "missingSkills": ["Rust"],
                "advice": "可投。",
            }
        )
        self.assertEqual(payload["matchScore"], 66)

    def test_tolerant_json_extraction(self) -> None:
        # 模型经常把 JSON 包在 ```json 里，前后还带解释文字
        raw = '这是结果：\n```json\n{"matchScore": 50}\n```\n以上。'
        self.assertEqual(llm_match._parse_json(raw)["matchScore"], 50)

    def test_unavailable_llm_degrades_to_heuristic(self) -> None:
        jobs = [make_job()]
        from unittest import mock

        with mock.patch.object(
            llm_match, "chat", side_effect=llm_match.LLMUnavailable("端点不可用")
        ):
            results = llm_match.score_all(jobs, PROFILE)
        self.assertEqual(len(results), 1)
        # 降级必须留痕，否则前端会把规则分当成 LLM 分展示
        self.assertIn("llm-fallback", results[0].model)


if __name__ == "__main__":
    unittest.main()
