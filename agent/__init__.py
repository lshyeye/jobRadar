"""JobRadar Agent：把岗位数据变成可被自然语言驱动的工具集。

设计取向与采集管线一致——**只用标准库**，不引入 LangChain / LangGraph：
一方面受限网络里装不上依赖，另一方面这个仓库要证明的是「知道 Agent 每一层在做什么」，
而不是「会调某个框架的 API」。

模块划分：

- ``tools``     工具实现（全部确定性，打在本地 JSON 上，可单测）
- ``llm``       OpenAI 兼容端点的最小客户端（urllib，无 SDK）
- ``planner``   离线确定性规划器（没有 key 时的兜底，保证 demo 可复现）
- ``loop``      ReAct 式工具调用循环 + 逐步 trace
- ``trace``     运行轨迹持久化与聚合（前端监控面板的数据源）
- ``mcp_server`` stdio JSON-RPC 的 MCP server，把同一批工具暴露给任意 MCP 客户端
"""

from .types import AgentRun, StepTrace, ToolSpec

__all__ = ["AgentRun", "StepTrace", "ToolSpec"]
