"""采集管线的数据结构。类型集中定义，禁止在函数内部内联定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SourceConfig:
    """一个数据源的配置。``field_map`` 决定原始字段如何映射成 Job。"""

    id: str
    name: str
    adapter: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def field_map(self) -> dict[str, str]:
        return self.config.get("fieldMap", {}) or {}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            adapter=data.get("adapter", "fixture"),
            enabled=bool(data.get("enabled", True)),
            config=data.get("config", {}) or {},
        )


@dataclass
class PipelineOptions:
    """一次管线运行的参数。"""

    offline: bool = False
    limit: int | None = None
    out_dir: Path = ROOT / "data" / "_offline"
    sources_file: Path = ROOT / "config" / "sources.example.json"
    profile_file: Path = ROOT / "config" / "profile.example.json"
    fixture_dir: Path = ROOT / "fixtures"
    window_days: int = 92
    no_llm: bool = False
    sync_public: bool = False


@dataclass
class MatchResult:
    """一条岗位的匹配分析结果。

    规则通道与 LLM 通道输出**完全相同的结构**，前端零改动，
    靠 ``model`` 字段区分来源（``heuristic-*`` 前缀 = 规则通道）。
    """

    jobId: str
    matchScore: int
    hardReqs: list[str] = field(default_factory=list)
    bonusReqs: list[str] = field(default_factory=list)
    matchedSkills: list[str] = field(default_factory=list)
    missingSkills: list[str] = field(default_factory=list)
    advice: str = ""
    analyzedAt: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.jobId,
            "matchScore": self.matchScore,
            "hardReqs": self.hardReqs,
            "bonusReqs": self.bonusReqs,
            "matchedSkills": self.matchedSkills,
            "missingSkills": self.missingSkills,
            "advice": self.advice,
            "analyzedAt": self.analyzedAt,
            "model": self.model,
        }
