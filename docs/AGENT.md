# Agent 模块

> 「让岗位数据能被自然语言驱动」——四个确定性工具 + 一个可观测的 ReAct 循环 + 一个 MCP server。
> 全部只用 Python 标准库。

---

## 为什么不用 LangChain / LangGraph

两个理由，都不是「想炫技自己写」：

1. **可复现**：这个仓库的承诺是「clone 下来三条命令跑起来」。引入框架就意味着依赖、
   版本、TLS 栈、企业网络代理都可能成为阻塞点；而标准库里 `urllib` 就够了。
2. **可解释**：面试和工作里被问到的从来不是「你会不会调 `create_react_agent`」，
   而是「模型返回的参数不合法你怎么办」「工具失败怎么不炸」「多步循环怎么收敛」。
   这些逻辑必须在代码里能指出来——在框架里它们是被藏起来了。

真正的框架价值（checkpoint、human-in-the-loop、并行分支）在这个规模下是负担，
等有了多 Agent 协作需求再引入，届时替换掉 `loop.py` 一个文件即可。

---

## 结构

```
agent/
├── types.py       ToolSpec / StepTrace / AgentRun（类型集中定义，禁止函数内联）
├── tools.py       4 个工具 + JSON Schema + 白名单参数校验（唯一入口 call_tool）
├── llm.py         OpenAI 兼容端点客户端（urllib，无 SDK）
├── planner.py     离线确定性规划器（无 key 时的兜底，可单测）
├── loop.py        ReAct 循环（LLM 通道 / 离线通道，共用 trace 结构）
├── trace.py       轨迹持久化 + 聚合（前端监控面板的数据源）
└── mcp_server.py  stdio JSON-RPC 的 MCP server
```

### 四个工具（全部确定性、只读本地 JSON）

| 工具 | 作用 | 为什么需要它 |
|---|---|---|
| `search_jobs` | 城市/类型/最低分/关键词筛选，按分降序 | 80% 的问题是「帮我找」 |
| `get_job` | 单条岗位的完整分析与缺口 | 决策要落到「这条投不投」 |
| `market_stats` | 按城市/类型/集团聚合数量与均分 | 回答「哪片市场在扩」 |
| `skill_gaps` | 高匹配岗位的缺口技能频次 | 回答「接下来补什么」 |

工具是**确定性函数**，不联网、不调模型——所以能写单测（`tests/test_agent.py` 39 个用例），
也能在没有 key 的情况下让 demo 跑出真实结果。

### 双通道

```
                 ┌── llm 通道：模型自己决定调哪个工具（function calling）
question ────────┤
                 └── offline 通道：规则规划器给出确定序列
                          ↓
                    同一套 tools + 同一套 StepTrace
```

`mode="auto"` 时：有 `LLM_API_KEY` 走模型，没有就走规则；模型调用失败**不抛异常**，
降级为离线结果并标记 `status="degraded"`——Agent 的失败必须被面板看见，而不是消失在日志里。

---

## 可观测性：这一步是重点

每一次运行产出 `data/agent_runs.json`，前端 `AgentPanel` 直接消费：

| 指标 | 用途 |
|---|---|
| `toolSuccessRate` | 一眼看出参数校验是否在真实问题上打脸 |
| `p50StepLatencyMs` / `p95StepLatencyMs` | 尾延迟分布——平均值会骗人 |
| `avgDurationMs` | 单次问答成本 |
| `totalTokens` | 模型通道成本（离线通道恒为 0） |
| `steps[].latency_ms` | 每次工具调用的瀑布图 |
| `steps[].status/error` | 失败步骤红色高亮，参数错误带原文 |

聚合在后端算（`trace.aggregate`），前端只展示——监控页自己不能成为性能问题。

---

## 跑起来

```bash
# 单次问答（没有 key 会自动用离线规划器）
python -m agent ask "北京 55 分以上的 Agent 岗有哪些"

# 强制离线（演示、CI 用）
python -m agent ask "我该补什么" --offline

# 跑一组示例，落盘轨迹并同步到前端
python -m agent demo --offline
cd frontend && npm run dev        # 页面里会出现「Agent 运行监控」面板

# MCP server（stdio）——任何 MCP 客户端都能连
python -m agent mcp

# 单元测试
python -m unittest discover -s tests -v
```

配置全部走环境变量，不入库：

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | 没有就自动降级到离线规划器 |
| `LLM_BASE_URL` | OpenAI 兼容端点，默认 `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名 |

---

## MCP 侧

`python -m agent mcp` 启动后支持 `initialize` / `tools/list` / `tools/call` / `ping`，
协议版本 `2024-11-05`，stdio 传输（一行一条 JSON-RPC 消息）。

一个可用的客户端配置片段：

```json
{
  "mcpServers": {
    "job-radar": {
      "command": "python",
      "args": ["-m", "agent", "mcp"],
      "cwd": "<repo-path>"
    }
  }
}
```

工具级错误按 MCP 约定放在 `result.isError` 里（而不是 JSON-RPC 的 `error`），
这样客户端能把「参数不合法」当成可读结果交给模型自我纠正——这是有意的选择，
不是实现疏漏。

---

## 与目标 JD 的对应关系

目标岗位：**前端研发工程师-基础架构**（数据中心网络平台方向），杭州，社招。
要求原文与实现证据的映射：

| JD 要求（原文） | 实现证据 | 代码位置 |
|---|---|---|
| 熟练 HTML/CSS/JS/TS | 6 个 TSX 组件 + 类型唯一收口 + `tsc --noEmit` 进构建 | `frontend/src/` |
| React + 前端工程化与模块化 | React 18 + Vite；three.js 独立 chunk（677KB→158KB）；数据获取层与展示层分离 | `vite.config.ts`、`api/data.ts` |
| **监控经验（优先）** | Agent 运行轨迹全链路记录 + 成功率/尾延迟/失败步骤 | `agent/trace.py`、`AgentPanel.tsx` |
| **性能分析（优先）** | P50/P95 步耗时、单次运行瀑布图、构建体积拆分数据 | `trace.aggregate`、`AgentPanel` |
| **数据可视化（优先）** | KPI 卡、类型趋势、集团分布、3D 岗位星系、瀑布图 | `KpiCards`、`TypeTrend`、`JobGalaxy` |
| **云平台前端（优先）** | 数据契约驱动（三份 JSON 即可接入）、数据源与画像配置化 | `docs/DATA_CONTRACT.md`、`config/` |
| 平台产品及工具服务建设 | 4 个工具 + MCP server，能力可被外部客户端复用 | `agent/tools.py`、`mcp_server.py` |
| 与产品/后端配合 | 采集与分析双进程通过 JSON 契约解耦，可独立部署演进 | `pipeline/README.md` |
| 易用性/展示效果/性能/安全 | 空态降级、防缓存、拆包、工具参数白名单校验 | 多处 |

**说实话的缺口**（面试时别硬吹，主动说更可信）：

1. 没有真实**数据中心网络**领域知识（这是该岗位的业务域，无法靠项目补，只能靠面试前补概念）；
2. 没有 **Vue** 生产经验（JD 是「Vue 或 React」，React 侧满足）；
3. 没有**多人协作、大型平台**的工程经验（这是单人项目，规模上不去）。
