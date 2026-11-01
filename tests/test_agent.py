"""Agent 层测试：工具契约、循环、轨迹聚合、MCP 协议、LLM 降级。

只依赖标准库 unittest，不引入 pytest——保持「clone 下来就能跑」。
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest import mock

from agent import llm, loop, mcp_server, planner, trace
from agent.tools import call_tool, load_jobs, market_stats, search_jobs, skill_gaps
from agent.types import AgentRun, StepTrace


class ToolContractTest(unittest.TestCase):
    """工具必须对坏参数报错、对好参数给出可预测结果。"""

    def test_unknown_tool_rejected(self) -> None:
        with self.assertRaises(ValueError):
            call_tool("drop_database", {})

    def test_unknown_argument_rejected(self) -> None:
        with self.assertRaises(ValueError):
            call_tool("search_jobs", {"citys": "北京"})

    def test_enum_argument_rejected(self) -> None:
        with self.assertRaises(ValueError):
            search_jobs(job_type="产品经理")

    def test_missing_required_argument_rejected(self) -> None:
        with self.assertRaises(ValueError):
            call_tool("get_job", {})

    def test_limit_capped(self) -> None:
        result = search_jobs(limit=999)
        self.assertLessEqual(result["returned"], 50)

    def test_negative_limit_rejected(self) -> None:
        with self.assertRaises(ValueError):
            search_jobs(limit=0)


class SearchJobsTest(unittest.TestCase):
    def test_city_filter_is_exact_containment(self) -> None:
        result = search_jobs(city="北京", limit=5)
        self.assertGreater(result["total"], 0)
        self.assertTrue(all("北京" in item["city"] for item in result["items"]))

    def test_min_score_filter(self) -> None:
        result = search_jobs(min_score=70, limit=50)
        self.assertTrue(all((item["score"] or 0) >= 70 for item in result["items"]))

    def test_sorted_by_score_desc(self) -> None:
        items = search_jobs(min_score=55, limit=10)["items"]
        scores = [i["score"] for i in items]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_keyword_returns_nothing(self) -> None:
        result = search_jobs(keyword="绝对不存在的技能组合xyz", limit=5)
        self.assertEqual(result["total"], 0)


class DetailAndStatsTest(unittest.TestCase):
    def test_get_job_found(self) -> None:
        url = search_jobs(limit=1)["items"][0]["url"]
        result = call_tool("get_job", {"url": url})
        self.assertTrue(result["found"])
        self.assertEqual(result["job"]["url"], url)

    def test_get_job_not_found(self) -> None:
        result = call_tool("get_job", {"url": "https://jobs.example.com/none/0"})
        self.assertFalse(result["found"])

    def test_market_stats_buckets_cover_all(self) -> None:
        for field in ("city", "type", "group"):
            result = market_stats(group_by=field, top=30)
            self.assertEqual(sum(b["count"] for b in result["buckets"]), result["total"])

    def test_skill_gaps_sorted(self) -> None:
        gaps = skill_gaps(min_score=55, limit=10)["gaps"]
        counts = [g["jobs"] for g in gaps]
        self.assertEqual(counts, sorted(counts, reverse=True))


class PlannerTest(unittest.TestCase):
    def test_city_and_score_extracted(self) -> None:
        plan = planner.build_plan("北京 55 分以上的 Agent 岗有哪些")
        self.assertEqual(plan.slots.get("city"), "北京")
        self.assertEqual(plan.slots.get("min_score"), 55)
        self.assertEqual(plan.intent, "search")

    def test_stats_intent(self) -> None:
        plan = planner.build_plan("各城市岗位分布情况")
        self.assertEqual(plan.intent, "market_stats")
        self.assertEqual(plan.calls[0].args["group_by"], "city")

    def test_gap_intent(self) -> None:
        plan = planner.build_plan("我还缺什么，该补哪些")
        self.assertEqual(plan.intent, "skill_gaps")

    def test_url_routes_to_detail(self) -> None:
        url = search_jobs(limit=1)["items"][0]["url"]
        plan = planner.build_plan(f"看看这条 {url}")
        self.assertEqual(plan.intent, "job_detail")


class LoopTest(unittest.TestCase):
    def test_offline_run_records_trace(self) -> None:
        run = loop.run("北京 55 分以上的 Agent 岗", mode="offline")
        self.assertEqual(run.status, "ok")
        self.assertTrue(run.answer)
        self.assertGreaterEqual(len(run.steps), 2)
        self.assertEqual(run.steps[-1].kind, "final")
        self.assertTrue(all(s.latency_ms >= 0 for s in run.steps))

    def test_tool_error_becomes_trace_not_exception(self) -> None:
        run = loop.run("我缺什么", mode="offline")
        self.assertIn("缺口", run.answer)
        self.assertTrue(all(s.status in ("ok", "error") for s in run.steps))

    def test_llm_mode_falls_back_when_key_missing(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            run = loop.run("各城市分布", mode="auto")
        self.assertEqual(run.planner, "offline")

    def test_explicit_llm_mode_degrades_without_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            run = loop.run("各城市分布", mode="llm")
        self.assertEqual(run.status, "degraded")
        self.assertEqual(run.planner, "offline")


class TraceTest(unittest.TestCase):
    def _run(self, run_id: str, latencies: list[float], status: str = "ok") -> AgentRun:
        steps = [
            StepTrace(index=i + 1, kind="tool", name="search_jobs", status=status, latency_ms=lat)
            for i, lat in enumerate(latencies)
        ]
        return AgentRun(run_id=run_id, question="q", started_at="now", duration_ms=10.0, steps=steps)

    def test_aggregate_math(self) -> None:
        agg = trace.aggregate([self._run("a", [1.0, 3.0]).to_dict(), self._run("b", [5.0]).to_dict()])
        self.assertEqual(agg["toolCalls"], 3)
        self.assertEqual(agg["toolErrors"], 0)
        self.assertEqual(agg["p50StepLatencyMs"], 3.0)
        self.assertEqual(agg["p95StepLatencyMs"], 5.0)
        self.assertEqual(agg["avgDurationMs"], 10.0)

    def test_aggregate_counts_errors(self) -> None:
        agg = trace.aggregate([self._run("a", [1.0], status="error").to_dict()])
        self.assertEqual(agg["toolSuccessRate"], 0.0)

    def test_aggregate_empty_safe(self) -> None:
        agg = trace.aggregate([])
        self.assertEqual(agg["totalRuns"], 0)
        self.assertEqual(agg["toolCalls"], 0)

    def test_save_run_upserts_by_run_id(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.json"
            trace.save_run(self._run("same", [1.0]), path=path)
            payload = trace.save_run(self._run("same", [2.0, 3.0]), path=path)
            self.assertEqual(len(payload["runs"]), 1)
            self.assertEqual(payload["aggregate"]["toolCalls"], 2)

    def test_save_run_respects_max_runs(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.json"
            for i in range(5):
                trace.save_run(self._run(f"r{i}", [1.0]), path=path, max_runs=3)
            self.assertEqual(len(trace.load(path)["runs"]), 3)


class McpTest(unittest.TestCase):
    def test_initialize(self) -> None:
        resp = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(resp["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list(self) -> None:
        resp = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {"search_jobs", "get_job", "market_stats", "skill_gaps"})
        self.assertTrue(all("inputSchema" in t for t in resp["result"]["tools"]))

    def test_tools_call_ok(self) -> None:
        resp = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_jobs", "arguments": {"city": "北京", "limit": 2}},
            }
        )
        self.assertFalse(resp["result"]["isError"])
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertLessEqual(payload["returned"], 2)

    def test_tools_call_bad_argument_is_result_not_crash(self) -> None:
        resp = mcp_server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "search_jobs", "arguments": {"job_type": "产品"}},
            }
        )
        self.assertTrue(resp["result"]["isError"])

    def test_unknown_method(self) -> None:
        resp = mcp_server.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
        self.assertEqual(resp["error"]["code"], mcp_server.METHOD_NOT_FOUND)

    def test_notification_has_no_response(self) -> None:
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_serve_handles_invalid_json(self) -> None:
        stdin = io.StringIO("not json\n")
        stdout = io.StringIO()
        mcp_server.serve(stdin=stdin, stdout=stdout)
        payload = json.loads(stdout.getvalue().strip())
        self.assertEqual(payload["error"]["code"], mcp_server.PARSE_ERROR)


class LLMTest(unittest.TestCase):
    def test_config_none_without_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(llm.load_config())

    def test_chat_raises_unavailable_without_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(llm.LLMUnavailable):
                llm.chat([{"role": "user", "content": "hi"}])

    def test_normalize_response_with_tool_calls(self) -> None:
        body = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "search_jobs", "arguments": '{"city":"北京"}'}}
                        ],
                    }
                }
            ],
            "usage": {"total_tokens": 12},
        }
        result = llm.normalize_response(body)
        self.assertEqual(result["tool_calls"][0]["name"], "search_jobs")
        self.assertEqual(result["tool_calls"][0]["arguments"], {"city": "北京"})
        self.assertEqual(result["usage"]["total_tokens"], 12)

    def test_normalize_response_bad_json_arguments(self) -> None:
        body = {"choices": [{"message": {"tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{oops"}}]}}]}
        result = llm.normalize_response(body)
        self.assertIn("__invalid_json__", result["tool_calls"][0]["arguments"])


class DataIntegrityTest(unittest.TestCase):
    def test_jobs_have_required_fields(self) -> None:
        jobs = load_jobs()
        self.assertGreater(len(jobs), 0)
        for job in jobs[:50]:
            self.assertTrue(job["url"].startswith("http"))
            self.assertIn("title", job)


if __name__ == "__main__":
    unittest.main()
