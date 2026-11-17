"""隔离区视图：按原因码聚合、按级别汇总、导出用的证据行。"""
from __future__ import annotations

from app import reasons

EXPORT_FIELDS = ("code", "stage", "level", "row_ref", "field", "raw_value", "detail")


def by_code(issues: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for entry in issues:
        code = entry["code"]
        bucket = buckets.setdefault(code, {
            "code": code,
            "label": reasons.label(code),
            "level": reasons.level(code),
            "level_label": reasons.LEVEL_LABELS.get(reasons.level(code), ""),
            "stage": reasons.stage(code),
            "count": 0,
        })
        bucket["count"] += 1
    return sorted(buckets.values(), key=lambda b: (b["stage"], -b["count"], b["code"]))


def by_level(issues: list[dict]) -> dict:
    out = {"error": 0, "warn": 0, "info": 0}
    for entry in issues:
        out[entry["level"]] = out.get(entry["level"], 0) + 1
    return out


def isolated(issues: list[dict]) -> list[dict]:
    return [entry for entry in issues if entry["level"] == "error"]


def for_export(issues: list[dict]) -> list[dict]:
    rows = []
    for entry in issues:
        rows.append({field: ("" if entry.get(field) is None else entry.get(field))
                     for field in EXPORT_FIELDS})
    return rows
