"""结构探测：分隔符、表头、列映射、列数、分段与末行完整性。

只做「结构」判断，不解释字段值；字段级归一在 m2 计费模块完成。
"""
from __future__ import annotations

from app.modules.m2_billing import contract

CANDIDATE_DELIMITERS = (",", ";", "\t", "|")


def detect_delimiter(text: str) -> str:
    for line in text.splitlines()[:5]:
        if not line.strip():
            continue
        counts = {sep: line.count(sep) for sep in CANDIDATE_DELIMITERS}
        best = max(counts, key=lambda sep: counts[sep])
        return best if counts[best] > 0 else ","
    return ","


def header_key(fields: list[str]) -> tuple[str, ...]:
    return tuple(str(cell).strip().replace("\ufeff", "") for cell in fields)


def probe(bundle: dict) -> dict:
    """把 reader 的结构结果补上列映射与列数契约。"""
    records = bundle["records"]
    header = bundle["header"]
    if not header:
        raise ValueError("文件没有表头")
    mapping, unknown = contract.map_header(header)
    missing = [name for name in contract.REQUIRED if name not in mapping]
    data_records = [r for r in records if r["kind"] == "data"]
    return {
        **bundle,
        "mapping": mapping,
        "unknown_columns": unknown,
        "missing_columns": missing,
        "expected_columns": len(header),
        "data_records": data_records,
        "data_count": len(data_records),
    }
