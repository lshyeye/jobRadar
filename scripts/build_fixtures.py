#!/usr/bin/env python3
"""从演示数据反向生成「原始接口快照」，供离线跑管线用。

为什么需要这层：真实采集需要连招聘站（要凭据、会被限流、部分站点已改版），
CI 和任何人都跑不了。所以把一份真实的采集结果**reshape 成原始接口的字段形状**，
存成 fixtures，管线就能对着它完整跑一遍——归一化、分类、打分、聚合全部真跑，
只是数据源换成了本地文件。

诚实说明两点：
1. 岗位、薪资、城市、标签全部来自真实采集（已脱敏）；
2. **JD 正文是按标签合成的占位文本**（原始正文含公司业务信息，不可公开），
   所以正文里会明确标注「演示用 JD」——它不是真实 JD，别拿它做结论。

用法::

    python scripts/build_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_STATS = ROOT / "data" / "stats.json"
FIXTURE_DIR = ROOT / "fixtures"


def synth_description(job: dict) -> str:
    """用标签合成占位 JD 正文。

    注意：正文里**刻意不写岗位类型**（不写「前端方向」这种字样）。
    否则分类器等于直接读到答案，离线跑出来的分类分布就没有验证意义了。
    """
    tags = "、".join(job.get("req") or []) or "通用研发能力"
    return (
        "【演示用 JD，非真实正文】岗位职责：参与产品研发与迭代，与产品、后端协作完成需求交付。"
        f"任职要求：熟悉 {tags}；具备 {job.get('city') or '一线城市'} 办公条件，3 年以上相关经验者优先。"
    )


def to_raw_row(job: dict) -> dict:
    """把 Job 反向映射成「原始接口」字段形状（字段名与 config 里的 fieldMap 对应）。"""
    return {
        "jobName": job["title"],
        "companyName": job["company"],
        "workCity": job["city"],
        "salaryMin": job["salaryMin"],
        "salaryMax": job["salaryMax"],
        "publishDate": job["postedDate"] or job["posted"],
        "jobUrl": job["url"],
        "jobDescription": synth_description(job),
    }


def main() -> int:
    stats = json.loads(SOURCE_STATS.read_text(encoding="utf-8"))
    jobs = stats.get("jobs", [])
    if not jobs:
        print("data/stats.json 里没有岗位，无法生成 fixture", file=sys.stderr)
        return 1

    by_source: dict[str, list[dict]] = {}
    for job in jobs:
        by_source.setdefault(job.get("source", "src_x"), []).append(to_raw_row(job))

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for old in FIXTURE_DIR.glob("*.json"):
        old.unlink()

    total = 0
    for source_id, rows in sorted(by_source.items()):
        # 按 200 条一页切，模拟接口分页
        for page, start in enumerate(range(0, len(rows), 200), start=1):
            chunk = rows[start : start + 200]
            path = FIXTURE_DIR / f"{source_id}_page{page}.json"
            path.write_text(
                json.dumps(
                    {
                        "source": source_id,
                        "page": page,
                        "pageSize": 200,
                        "total": len(rows),
                        "list": chunk,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            total += len(chunk)
        print(f"{source_id}: {len(rows)} 条")

    print(f"共写出 {total} 条原始记录到 {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
