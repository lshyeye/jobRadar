# pipeline/ · 采集器

这个目录**故意留空**，只放这份说明。原因如实写在这里：

采集器目前运行在与本仓库无关的另一台机器上（挂在定时任务里），
为了不中断已经在跑的数据流，v1.0 没有搬动它。

代价很明确：**仓库不完整、不可一键复现**——这是已知短板，
已列入 README 的 Roadmap（「采集器收进仓库 + 离线 fixture」）。

---

## 采集器需要做什么

只需要一件事：**产出符合 [`docs/DATA_CONTRACT.md`](../docs/DATA_CONTRACT.md) 的 `stats.json`**。

```
config/sources.json ──► collect ──► 归一化 ──► 近期过滤(92d) ──► classify
                                                                   │
                                              jd_store（正文按 URL 累积）◄──┘
                                                                   │
                                    llm_match / heuristic_match ◄──┘
                                                                   │
                                              stats.json + match_scores.json
```

| 阶段 | 职责 | 独立可测 |
|------|------|---------|
| `collect` | 按 `sources.json` 拉列表，`fieldMap` 映射成 `Job` | ✅ 输入 fixture，输出 JSON |
| `normalize` | 城市/薪资/日期归一化（**同一城市只允许一种写法**） | ✅ 纯函数，最该写单测 |
| `recent_filter` | 92 天窗口，丢弃超期与无日期 | ✅ |
| `classify` | 岗位分类：前端 / 全栈 / Agent 应用开发 / 其他 | ✅ 结果不可盲信，见 README 决策 4 |
| `jd_store` | 正文按 URL 主键累积合并 | ✅ 增量幂等 |
| `llm_match` | 逐条读 JD 原文 → 硬性要求/加分项/缺口/建议 | ✅ 输出必须过 schema 校验 |
| `heuristic_match` | 词表命中 + 角色校准（LLM 不可用时接管） | ✅ 与上者同一契约 |
| `aggregate` | 统计、环比、摘要 | ✅ |

## 接入自己的数据源

1. 复制 `../config/sources.example.json` → `sources.json`，填 `listUrl` 与 `fieldMap`；
2. 用任意语言实现上表里你需要的那几步（Python 标准库足够，无需第三方依赖）；
3. 产出 `stats.json` 放进 `../data/`，并在构建前同步到 `../frontend/public/data/`。

只要 JSON 结构对得上，采集器用什么语言、跑在哪、一天跑几次，看板都不关心。

## 需要的凭据

统一走环境变量，不要写进代码或配置提交：

| 变量 | 用途 |
|------|------|
| `LLM_API_KEY` | OpenAI 兼容端点的 key |
| `LLM_BASE_URL` | 端点地址（默认按厂商填） |
| `LLM_MODEL` | 模型名 |
| `SRC_*_COOKIE` | 需要登录态的数据源凭据（可选） |
