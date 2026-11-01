"""JD 正文库：以 URL 为主键累积。

为什么单独做一层：正文是稀缺资源（要翻页、要限速），而分析是易耗操作
（换模型、调 prompt 都要重跑）。两者解耦后，**分析过的岗位永不重抓**，
跨周只增不减，中断也能断点续跑。

代价写在 README「已知限制」里：文件会随周数增长，需要定期裁剪。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class JdStore:
    """正文库。磁盘格式：``{url: {title, description, company, fetchedAt}}``。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, dict[str, Any]] = {}
        self._loaded = 0
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._items = payload.get("items", {}) if isinstance(payload, dict) else {}
            self._loaded = len(self._items)

    def merge(self, jobs: Iterable[dict[str, Any]]) -> int:
        """并入本批岗位，返回**新增**条数（已存在的只更新标题，不覆盖正文）。"""
        added = 0
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for job in jobs:
            url = job.get("url")
            if not url:
                continue
            existing = self._items.get(url)
            if existing is None:
                added += 1
                self._items[url] = {
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "description": job.get("description", ""),
                    "fetchedAt": stamp,
                }
            elif not existing.get("description") and job.get("description"):
                # 之前只有空正文，这次补上——仍然算「更新」，不算新增
                existing["description"] = job["description"]
                existing["fetchedAt"] = stamp
        return added

    def get(self, url: str) -> dict[str, Any] | None:
        return self._items.get(url)

    def coverage(self, jobs: Iterable[dict[str, Any]]) -> float:
        """正文覆盖率：有非空正文的岗位占比，用于 meta 健康度展示。"""
        total = 0
        filled = 0
        for job in jobs:
            total += 1
            entry = self._items.get(job.get("url", ""))
            if entry and entry.get("description"):
                filled += 1
        return round(filled / total, 4) if total else 0.0

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {
                "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "total": len(self._items),
                "loadedFromDisk": self._loaded,
                "authoritative": True,
            },
            "items": self._items,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def __len__(self) -> int:
        return len(self._items)
