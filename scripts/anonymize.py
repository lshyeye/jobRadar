#!/usr/bin/env python3
"""数据脱敏 / 通用化脚本。

把「某个求职者的真实采集产物」改造成「可公开的通用演示数据」：

1. 公司名 → 公司A/B/C/D；数据源键 → src_a/src_b/...
2. 岗位链接 → ``https://jobs.example.com/<source>/<hash>``（确定性哈希，
   保证 stats 与 match_scores 两侧的 jobId 仍然对得上）
3. 标题里的产品名 / 业务线 / 游戏 IP → 通用业务域（可灵 → 视频生成）
4. 个人画像痕迹（内部系统名）→ 通用技术名词
5. 城市字段归一化（Beijing / 北京市 / 北京 → 北京）

映射表全部外置在 ``config/pii_map.local.json``（已 gitignore），本脚本不含任何真实名称。
仅依赖标准库。

用法::

    python scripts/anonymize.py --map config/pii_map.local.json --dry-run   # 只报告
    python scripts/anonymize.py --map config/pii_map.local.json             # 落盘
    python scripts/anonymize.py --map config/pii_map.local.json --verify    # 扫描残留
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    ROOT / "data" / "stats.json",
    ROOT / "data" / "match_scores.json",
    ROOT / "data" / "_picks.json",
    ROOT / "frontend" / "public" / "data" / "stats.json",
    ROOT / "frontend" / "public" / "data" / "match_scores.json",
]

TEXT_FIELDS = (
    "title",
    "advice",
    "hardReqs",
    "bonusReqs",
    "matchedSkills",
    "missingSkills",
    "miss",
)


def load_map(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_tokens(raw: Iterable[list[str]], extra: Iterable[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """按长度倒序，避免「微信」先吃掉「微信支付」。

    公司名与城市名会自动并入文本替换表——只改 company / city 字段是不够的，
    标题、投递建议、要求条目里同样会出现真实公司名。
    """
    pairs = [(str(a), str(b)) for a, b in raw]
    pairs += [p for p in extra if p[0] and p[0] != p[1]]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def derive_tokens(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """配置里的 token 表 + 公司名 + 城市异体名 → 一份统一的文本替换表。"""
    extra: list[tuple[str, str]] = []
    extra += list(cfg.get("companies", {}).items())
    extra += [(k, v) for k, v in cfg.get("cities", {}).items() if k != v]
    return build_tokens(cfg.get("tokens", []), extra)


def anonymize_url(url: str, url_cfg: dict[str, Any], sources: dict[str, str]) -> str:
    """确定性改写：同一原始链接永远得到同一假链接，且保留来源分组。"""
    if not url:
        return ""
    host = url_cfg.get("default_host", "jobs.example.com")
    scheme = url_cfg.get("scheme", "https")
    if url.startswith(f"{scheme}://{host}/"):
        return url  # 幂等：重复执行不会二次哈希
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    src = "src_x"
    for real, alias in sources.items():
        if real.lower() in url.lower():
            src = alias
            break
    return f"{scheme}://{host}/{src}/{digest}"


def strip_ip(text: str, ip_cfg: dict[str, str] | None) -> str:
    if not ip_cfg or not text:
        return text
    return re.sub(ip_cfg["pattern"], ip_cfg["replacement"], text)


def scrub_text(text: str, tokens: list[tuple[str, str]], exact: list[tuple[str, str]]) -> str:
    """先整词替换，再逐 token 替换，最后清理因删除产生的多余分隔符。

    替换后出现的叠词（如 IP 名移除后留下的「游戏游戏」）由 token 表里的
    叠词收敛规则处理，无需在此硬编码。
    """
    if not text:
        return text
    out = text
    for src, dst in exact:
        if out == src:
            return dst
    for src, dst in tokens:
        if src in out:
            out = out.replace(src, dst)
    # 岗位req编码（如 ZP2604166120）可能被反查到原始招聘系统，统一抹掉
    out = re.sub(r"\b[A-Z]{2,4}\d{6,}\b", "", out)
    out = re.sub(r"[-—_]{2,}", "-", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = out.strip(" -—·")
    return out


def scrub_list(values: list[str], tokens: list[tuple[str, str]], exact: list[tuple[str, str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        nv = scrub_text(v, tokens, exact)
        if nv and nv not in seen:
            seen.add(nv)
            out.append(nv)
    return out


def process_stats(data: dict[str, Any], cfg: dict[str, Any], report: dict[str, int]) -> dict[str, Any]:
    tokens = derive_tokens(cfg)
    exact = [(a, b) for a, b in cfg.get("exact_replace", [])]
    companies: dict[str, str] = cfg.get("companies", {})
    sources: dict[str, str] = cfg.get("sources", {})
    cities: dict[str, str] = cfg.get("cities", {})
    url_cfg = cfg.get("url", {})
    ip_cfg = cfg.get("ip_regex")

    for job in data.get("jobs", []):
        old_company = job.get("company", "")
        job["company"] = companies.get(old_company, old_company)
        if job["company"] != old_company:
            report["company"] += 1

        old_src = job.get("source", "")
        job["source"] = sources.get(old_src, old_src)
        if job["source"] != old_src:
            report["source"] += 1

        old_url = job.get("url", "")
        job["url"] = anonymize_url(old_url, url_cfg, sources)
        if job["url"] != old_url:
            report["url"] += 1

        old_city = job.get("city", "")
        job["city"] = cities.get(old_city, old_city)
        if job["city"] != old_city:
            report["city"] += 1

        old_title = job.get("title", "")
        fixed = strip_ip(old_title, ip_cfg)
        fixed = scrub_text(fixed, tokens, exact)
        job["title"] = fixed
        if fixed != old_title:
            report["title"] += 1

        if isinstance(job.get("req"), list):
            job["req"] = scrub_list(job["req"], tokens, exact)

    meta = data.get("meta", {})
    if "scenario" in cfg and "scenario" in meta:
        meta["scenario"] = cfg["scenario"]
        report["meta"] += 1
    return data


def process_scores(data: list[dict[str, Any]], cfg: dict[str, Any], report: dict[str, int]) -> list[dict[str, Any]]:
    tokens = derive_tokens(cfg)
    exact = [(a, b) for a, b in cfg.get("exact_replace", [])]
    sources: dict[str, str] = cfg.get("sources", {})
    url_cfg = cfg.get("url", {})
    ip_cfg = cfg.get("ip_regex")

    for row in data:
        old_id = row.get("jobId", "")
        row["jobId"] = anonymize_url(old_id, url_cfg, sources)
        if row["jobId"] != old_id:
            report["jobId"] += 1
        for field in ("hardReqs", "bonusReqs", "matchedSkills", "missingSkills"):
            if isinstance(row.get(field), list):
                row[field] = scrub_list(row[field], tokens, exact)
        if "advice" in row:
            before = row["advice"]
            row["advice"] = strip_ip(scrub_text(before, tokens, exact), ip_cfg)
            if row["advice"] != before:
                report["advice"] += 1
    return data


def process_picks(data: list[dict[str, Any]], cfg: dict[str, Any], report: dict[str, int]) -> list[dict[str, Any]]:
    tokens = derive_tokens(cfg)
    exact = [(a, b) for a, b in cfg.get("exact_replace", [])]
    companies: dict[str, str] = cfg.get("companies", {})
    cities: dict[str, str] = cfg.get("cities", {})
    sources: dict[str, str] = cfg.get("sources", {})
    url_cfg = cfg.get("url", {})
    ip_cfg = cfg.get("ip_regex")

    for row in data:
        if "company" in row:
            row["company"] = companies.get(row["company"], row["company"])
        if "city" in row:
            row["city"] = cities.get(row["city"], row["city"])
        if "url" in row:
            row["url"] = anonymize_url(row["url"], url_cfg, sources)
        if "title" in row:
            row["title"] = strip_ip(scrub_text(row["title"], tokens, exact), ip_cfg)
        if "advice" in row:
            row["advice"] = scrub_text(row["advice"], tokens, exact)
        if isinstance(row.get("miss"), list):
            row["miss"] = scrub_list(row["miss"], tokens, exact)
    return data


def deep_scrub(
    node: Any,
    tokens: list[tuple[str, str]],
    exact: list[tuple[str, str]],
    ip_cfg: dict[str, str] | None,
) -> Any:
    """兜底深扫：逐字段清洗所有字符串。

    结构化处理只能覆盖已知字段，而统计摘要（summary）这类自由文本里同样会
    出现真实公司名与城市异体名，必须在最后一层统一兜住。
    """
    if isinstance(node, str):
        return strip_ip(scrub_text(node, tokens, exact), ip_cfg)
    if isinstance(node, dict):
        return {k: deep_scrub(v, tokens, exact, ip_cfg) for k, v in node.items()}
    if isinstance(node, list):
        return [deep_scrub(v, tokens, exact, ip_cfg) for v in node]
    return node


def iter_strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from iter_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_strings(v)


def verify(cfg: dict[str, Any]) -> int:
    """扫描残留：任何真实名称出现在产物里都算失败。"""
    needles: list[str] = []
    needles += list(cfg.get("companies", {}).keys())
    needles += [k for k in cfg.get("sources", {}).keys() if not k.isalpha() or len(k) > 3]
    needles += [a for a, _ in cfg.get("tokens", [])]
    needles += [a for a, _ in cfg.get("exact_replace", [])]
    needles += [k for k, v in cfg.get("cities", {}).items() if k != v]

    hits: dict[str, int] = {}
    for path in TARGETS:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        blob = "\n".join(iter_strings(payload))
        for n in needles:
            if not n or len(n) < 2:
                continue
            c = blob.count(n)
            if c:
                hits[f"{path.name}::{n}"] = c
    if hits:
        print("[残留] 仍有真实名称未脱敏：")
        for k, v in sorted(hits.items()):
            print(f"  {v:5d}  {k}")
        return 1
    print("[OK] 未发现残留的真实名称。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="JobRadar 数据脱敏脚本")
    ap.add_argument("--map", default="config/pii_map.local.json", help="映射表路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写盘")
    ap.add_argument("--verify", action="store_true", help="扫描残留真实名称")
    args = ap.parse_args()

    cfg = load_map((ROOT / args.map) if not Path(args.map).is_absolute() else Path(args.map))

    if args.verify:
        return verify(cfg)

    report = {k: 0 for k in ("company", "source", "url", "city", "title", "jobId", "advice", "meta")}
    for path in TARGETS:
        if not path.exists():
            print(f"[skip] {path.relative_to(ROOT)} 不存在")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "stats.json":
            payload = process_stats(payload, cfg, report)
        elif path.name == "match_scores.json":
            payload = process_scores(payload, cfg, report)
        elif path.name == "_picks.json":
            payload = process_picks(payload, cfg, report)
        if not args.dry_run:
            payload = deep_scrub(
                payload,
                derive_tokens(cfg),
                [(a, b) for a, b in cfg.get("exact_replace", [])],
                cfg.get("ip_regex"),
            )
        if args.dry_run:
            print(f"[dry] {path.relative_to(ROOT)} 待处理")
            continue
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[ok]  {path.relative_to(ROOT)}")

    print("\n字段改写计数：")
    for k, v in report.items():
        if v:
            print(f"  {k:8s} {v}")
    if args.dry_run:
        print("\n(dry-run，未写入任何文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
