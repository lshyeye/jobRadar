"""数据源适配器。

两种适配器，都返回**原始记录**（未归一化），由 ``normalize`` 统一处理：

- ``fixture``：读本地 JSON 快照。离线可复现，CI 与 demo 都用它。
- ``json_list``：请求列表接口并翻页。字段映射写在 config 的 ``fieldMap`` 里，
  换一个招聘站只要改配置，不改代码。

设计约束：适配器只负责「拿到原始记录」，不做归一化、不做过滤、不打分。
职责混在一起的话，换数据源就得重写业务逻辑。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .types import SourceConfig

MAX_PAGES = 20


class AdapterError(RuntimeError):
    """数据源不可用。上层据此把该源记进 failedSources，而不是让整条管线崩掉。"""


def load_sources(path: Path) -> list[SourceConfig]:
    """读 sources 配置。配置格式见 config/sources.example.json。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = [SourceConfig.from_dict(item) for item in payload.get("sources", [])]
    return [s for s in sources if s.enabled]


def fetch_raw(source: SourceConfig, fixture_dir: Path) -> list[dict[str, Any]]:
    """按适配器类型取原始记录。"""
    if source.adapter == "fixture":
        return _fetch_fixture(source, fixture_dir)
    if source.adapter == "json_list":
        return _fetch_json_list(source)
    raise AdapterError(f"未知适配器：{source.adapter}")


def _fetch_fixture(source: SourceConfig, fixture_dir: Path) -> list[dict[str, Any]]:
    """读 fixtures/<sourceId>*.json，按文件名排序后拼接。

    每个文件形如 ``{"source": "src_a", "page": 1, "list": [...]}``。
    """
    files = sorted(fixture_dir.glob(f"{source.id}*.json"))
    if not files:
        raise AdapterError(f"fixture 目录下没有 {source.id} 的快照：{fixture_dir}")
    rows: list[dict[str, Any]] = []
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(payload.get("list", []))
    return rows


def _fetch_json_list(source: SourceConfig) -> list[dict[str, Any]]:
    """请求列表接口并翻页。

    ``listUrl`` 里用 ``{page}`` 占位；``pageSize`` 决定每页条数；
    本页返回条数为 0 或达到 MAX_PAGES 时停止。
    """
    template = source.config.get("listUrl")
    if not template:
        raise AdapterError(f"数据源 {source.id} 缺少 listUrl")
    page_size = int(source.config.get("pageSize", 50))
    timeout = int(source.config.get("timeoutMs", 15000)) / 1000
    headers = {"User-Agent": source.config.get("userAgent", "job-radar-pipeline/1.0")}
    headers.update(source.config.get("headers") or {})

    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        url = template.replace("{page}", str(page)).replace("{pageSize}", str(page_size))
        try:
            request = urllib.request.Request(url, headers=headers, method=source.config.get("method", "GET"))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise AdapterError(f"数据源 {source.id} 第 {page} 页请求失败：{exc}") from exc

        batch = _pick_list(payload, source.config.get("listPath"))
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def _pick_list(payload: Any, list_path: str | None) -> list[dict[str, Any]]:
    """从响应里取出岗位数组。``listPath`` 形如 ``data.job_list``。"""
    node: Any = payload
    if list_path:
        for key in list_path.split("."):
            if not isinstance(node, dict) or key not in node:
                return []
            node = node[key]
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        for key in ("list", "items", "data", "results"):
            value = node.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []
