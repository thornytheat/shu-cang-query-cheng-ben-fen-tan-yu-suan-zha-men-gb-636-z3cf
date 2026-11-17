"""质量指标：通过率、隔离行数、原因码分布。"""
from __future__ import annotations

from . import quarantine


def summarize(issues: list[dict], counters: dict) -> dict:
    rows_data = counters.get("rows_data", 0)
    valid = counters.get("rows_valid", 0)
    pass_rate = (valid * 100.0 / rows_data) if rows_data else 0.0
    levels = quarantine.by_level(issues)
    return {
        "rows_data": rows_data,
        "rows_valid": valid,
        "rows_quarantine": counters.get("rows_quarantine", 0),
        "rows_dedup": counters.get("rows_dedup", 0),
        "rows_empty": counters.get("rows_empty", 0),
        "blank_lines": counters.get("blank_lines", 0),
        "pass_rate": round(pass_rate, 1),
        "levels": levels,
        "code_count": len(quarantine.by_code(issues)),
        "top_code": (quarantine.by_code(issues)[0]["code"]
                     if quarantine.by_code(issues) else ""),
    }
