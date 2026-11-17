"""M3 编排：跑一遍质量规则，给出隔离结论与原因码分布。"""
from __future__ import annotations

from . import metrics, quarantine, rules


def run(probed: dict) -> dict:
    result = rules.validate(probed)
    result["summary"] = metrics.summarize(result["issues"], result["counters"])
    result["distribution"] = quarantine.by_code(result["issues"])
    result["row_account"] = rules.check_row_account(result["counters"])
    return result


def load(store, batch_id: str) -> dict:
    issues = store.load_quarantine(batch_id)
    totals = store.load_totals(batch_id)
    counters = {
        "rows_data": int(totals.get("rows_data", 0)),
        "rows_valid": int(totals.get("rows_valid", 0)),
        "rows_quarantine": int(totals.get("rows_quarantine", 0)),
        "rows_dedup": int(totals.get("rows_dedup", 0)),
        "rows_empty": int(totals.get("rows_empty", 0)),
        "blank_lines": int(totals.get("blank_lines", 0)),
    }
    return {
        "issues": issues,
        "distribution": quarantine.by_code(issues),
        "summary": metrics.summarize(issues, counters),
        "row_account": rules.check_row_account(counters),
    }
