"""采集管线：从招聘数据源到 stats.json / match_scores.json 的完整链路。

模块边界按「能不能单独测」来切：

- ``normalize``        纯函数，城市/薪资/日期/技能标签归一化——最该写单测的部分
- ``classify``         岗位分类 + 岗位角色校准
- ``adapters``         数据源适配器（fixture 离线 / http_json 在线），字段映射由 config 驱动
- ``jd_store``         JD 正文按 URL 主键累积，跨周只增不重抓
- ``heuristic_match``  规则打分（LLM 不可用时的降级通道）
- ``llm_match``        LLM 打分（OpenAI 兼容端点 + 输出结构校验 + 重试）
- ``aggregate``        统计聚合，产出符合 docs/DATA_CONTRACT.md 的 stats.json
- ``run``              编排器：collect → filter → classify → store → match → aggregate

仅标准库，无第三方依赖。
"""

from .types import MatchResult, PipelineOptions, SourceConfig

__all__ = ["MatchResult", "PipelineOptions", "SourceConfig"]
