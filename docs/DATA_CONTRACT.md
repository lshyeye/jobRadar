# 数据契约（Data Contract）

看板与采集管线**不共享代码**，只共享这三份 JSON。只要你的采集器能产出下面的结构，
就可以直接替换本项目的数据源——不需要改一行前端代码。

```
采集器（任意语言 / 任意数据源）
        │
├──► stats.json          岗位流 + 统计（必需）
├──► match_scores.json   匹配度分析（可选，缺失时前端自动降级）
├──► _picks.json         优先投递清单导出（可选，给推送/周报用）
└──► agent_runs.json     Agent 运行轨迹（可选，缺失时监控面板不渲染）
```

放置位置：仓库 `data/`（源）→ 构建前同步到 `frontend/public/data/`（前端可直接 fetch）。

---

## 1. `stats.json`（必需）

```jsonc
{
  "meta": {
    "period_start": "2026-10-24",     // 本期统计起点 YYYY-MM-DD
    "period_end": "2026-10-30",       // 本期统计终点
    "last_week_start": "2026-10-17",  // 环比基准期
    "last_week_end": "2026-10-23",
    "generated_at": "2026-10-30 16:01",
    "source": "live",                 // live | sample | mock
    "scenario": "示例画像 · 前端 / 全栈",  // 展示在页头的场景说明
    "recentFilter": {                 // 近期过滤的审计信息
      "window_days": 92,
      "drop_undated": false,
      "kept": 914,
      "dropped_old": 37,
      "dropped_undated": 16
    }
  },
  "jobs": [ /* Job[]，见下表 */ ],
  "byType":  { "前端": 78, "全栈": 182, "Agent应用开发": 447, "其他": 207 },
  "byGroup": { "BAT": 198, "其他互联网": 716 },
  "trend": {
    "前端": { "last": 73, "this": 78, "delta": 5, "pct": 6.8 }
  },
  "lastWeek": { "前端": 73 },
  "topRequirements": [ { "name": "大模型", "count": 335 } ],
  "requirementSummary": { "Agent应用开发": "本周共 447 个…" },
  "failedSources": [ "src_b" ]
}
```

### Job 对象

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `company` | string | ✅ | 公司名（对外展示前必须脱敏） |
| `group` | enum | ✅ | `BAT` \| `TMD` \| `大外企` \| `国企` \| `其他互联网` |
| `title` | string | ✅ | 岗位标题 |
| `type` | enum | ✅ | `前端` \| `全栈` \| `Agent应用开发` \| `其他` |
| `city` | string | ✅ | 归一化后的城市名（**同一城市只允许一种写法**） |
| `salaryMin` / `salaryMax` | number | ✅ | 单位 K/月；`salaryMax = 0` 表示面议 |
| `posted` | string | ✅ | 原始发布时间文本，可空 |
| `postedDate` | string | ✅ | 归一化 `YYYY-MM-DD`，用于排序；可空 |
| `req` | string[] | ✅ | 从 JD 抽取的技能标签 |
| `url` | string | ✅ | **同时充当主键**：`match_scores.jobId` 与它一一对应 |
| `source` | string | ✅ | 数据源标识（脱敏后为 `src_a` 这类别名） |

> ⚠️ `url` 是事实上的主键。同一岗位多平台重复发布会导致重复入榜，去重是已知待办。

---

## 2. `match_scores.json`（可选）

数组，每项对应一个 `Job.url`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `jobId` | string | 等于 `Job.url` |
| `matchScore` | number | 0–100 |
| `hardReqs` | string[] | JD 里的硬性要求 |
| `bonusReqs` | string[] | 加分项 |
| `matchedSkills` | string[] | 画像命中项 |
| `missingSkills` | string[] | 画像缺口 |
| `advice` | string | 一句话投递建议 |
| `analyzedAt` | string | `YYYY-MM-DD HH:mm` |
| `model` | string | **来源标识**：`heuristic-*` 前缀 = 规则通道；其余 = LLM 通道 |

### 为什么 `model` 前缀很重要

规则分与 LLM 分是**两套不可比的刻度**（规则分普遍虚高）。前端据此做两件事：

1. 详情抽屉标注「规则匹配分析（离线）」还是「LLM 匹配度分析」，不把规则分说成 LLM 分；
2. 优先投递清单**只采信一种刻度**——LLM 结果达到 5 条以上就只用 LLM 分排序，
   避免关键词命中的岗位靠虚高分挤掉真正值得投的岗位（见 `TopPicks.tsx`）。

如果你的实现只有一种通道，恒返回同一种 `model` 前缀即可。

---

## 3. `_picks.json`（可选）

给微信推送 / 周报导出的精简清单：

```jsonc
[
  {
    "score": 86,
    "company": "公司C",
    "title": "AI Agent 全栈工程师",
    "city": "北京",
    "posted": "2026-10-09",
    "url": "https://jobs.example.com/src_c/8c5d65d841",
    "advice": "值得投：……",
    "miss": ["音视频领域知识", "生产级稳定性治理经验"]
  }
]
```

---

## 4. `agent_runs.json`（可选）

Agent 运行轨迹，前端「Agent 运行监控」面板的数据源。由 `agent/trace.py` 产出，
**聚合指标在后端算好**，前端不做二次统计。

```jsonc
{
  "meta": { "generatedAt": "2026-11-01 18:10:00", "maxRuns": 50, "schemaVersion": 1 },
  "runs": [
    {
      "run_id": "run_20261101_181000_a1b2c3",
      "question": "北京 55 分以上的 Agent 岗有哪些",
      "started_at": "2026-11-01 18:10:00",
      "duration_ms": 4.42,
      "status": "ok",              // ok | degraded | max_steps
      "planner": "offline",        // offline | llm
      "answer": "筛到 14 条……",
      "steps": [
        {
          "index": 1,
          "kind": "tool",          // tool | final
          "name": "search_jobs",
          "args": { "city": "北京", "min_score": 55 },
          "status": "ok",          // ok | error
          "latency_ms": 0.16,
          "summary": "命中 14 条，返回 8 条",
          "result_count": 8,
          "error": null
        }
      ],
      "usage": { "promptTokens": 0, "completionTokens": 0, "totalTokens": 0 }
    }
  ],
  "aggregate": {
    "totalRuns": 4, "okRuns": 4, "degradedRuns": 0,
    "toolCalls": 5, "toolErrors": 0, "toolSuccessRate": 1.0,
    "avgDurationMs": 5.89, "p50StepLatencyMs": 0.24, "p95StepLatencyMs": 7.39,
    "totalTokens": 0,
    "byTool": [{ "name": "market_stats", "calls": 2, "errors": 0, "avgLatencyMs": 0.24 }]
  }
}
```

缺失时面板整体不渲染（不显示空壳）。

---

## 5. 接入自己的数据源

1. 复制 `config/sources.example.json` 为 `sources.json`，填 `listUrl` / `fieldMap`；
2. 采集器按 `fieldMap` 把原始字段映射成本契约的 `Job`；
3. 产出 `data/stats.json`，复制到 `frontend/public/data/`；
4. `cd frontend && npm run dev` 即用。

前端不关心数据来自哪个招聘站、用什么语言采集、有没有 LLM——只认这三份 JSON。
