"""质量规则：列数、截断、必填、数值、哨兵、重复账单与同号冲突。

判定顺序固定（文件顺序 → 去重 → 冲突），因此结果与输入行的书写顺序有关、
但与字典遍历顺序无关；重复账单保留文件中第一条，理由写在隔离详情里。
"""
from __future__ import annotations

from app import reasons
from app.modules.m2_billing import coerce, service as billing_service


def issue(code: str, *, row_ref=None, field=None, raw_value=None,
          detail: str = "") -> dict:
    entry = reasons.REASONS.get(code, {})
    return {
        "code": code,
        "stage": entry.get("stage", "quality"),
        "level": entry.get("level", "error"),
        "row_ref": row_ref,
        "field": field,
        "raw_value": raw_value,
        "detail": detail,
    }


def _signature(fields: list[str]) -> tuple[str, ...]:
    return tuple(coerce.clean_text(cell) for cell in fields)


def validate(probed: dict) -> dict:
    """逐行校验脏样例；坏行进隔离区，好行继续往下算。"""
    expected = probed["expected_columns"]
    mapping = probed["mapping"]
    trailing_newline = probed["trailing_newline"]
    records = probed["records"]
    data_records = probed["data_records"]
    last_line = data_records[-1]["line_no"] if data_records else -1

    issues: list[dict] = []
    counters = {"rows_data": len(data_records), "rows_empty": 0, "rows_dedup": 0,
                "rows_quarantine": 0, "rows_valid": 0, "blank_lines": 0}
    for record in records:
        if record["kind"] == "blank":
            counters["blank_lines"] += 1
            continue
        if record["kind"] == "header":
            continue
        row_ref = f"第 {record['line_no']} 行"
        if record.get("empty"):
            counters["rows_empty"] += 1
            issues.append(issue("Q_EMPTY_ROW", row_ref=row_ref,
                                detail=f"分段 {record['segment']} 字段全空"))
            continue
        fields = record["fields"]
        if len(fields) != expected:
            truncated = (record["line_no"] == last_line and not trailing_newline
                         and len(fields) < expected)
            code = "Q_TRUNCATED_ROW" if truncated else "Q_COLUMN_COUNT"
            counters["rows_quarantine"] += 1
            issues.append(issue(code, row_ref=row_ref, field="列数",
                                raw_value=str(len(fields)),
                                detail=f"期望 {expected} 列，实际 {len(fields)} 列"))
            continue
        try:
            row = billing_service.coerce_row(
                fields, mapping, row_ref=row_ref, line_no=record["line_no"],
                segment=record["segment"])
        except coerce.CoerceError as exc:
            counters["rows_quarantine"] += 1
            issues.append(issue(exc.code, row_ref=row_ref, field=exc.field,
                                raw_value=exc.raw, detail=exc.message))
            continue
        for flag in row["flags"]:
            issues.append(issue(flag, row_ref=row_ref,
                                field=("scan_size" if flag == "Q_BIG_INT"
                                       else "scan_size"),
                                raw_value=row["scan_raw"],
                                detail=("超过 2^53，按字符串保形"
                                        if flag == "Q_BIG_INT"
                                        else "扫描量为 0，按最低计费")))
        row["_signature"] = _signature(fields)
        record["_row"] = row

    # 去重：完全相同的行只保留第一条
    seen: dict[tuple[str, ...], dict] = {}
    unique: list[dict] = []
    for record in records:
        if record["kind"] != "data":
            continue
        row = record.get("_row")
        if row is None:
            continue
        signature = row["_signature"]
        if signature in seen:
            first = seen[signature]
            counters["rows_dedup"] += 1
            issues.append(issue("Q_DUP_IDENTICAL", row_ref=row["row_ref"],
                                field="query_id", raw_value=row["query_id"],
                                detail=f"与 {first['row_ref']} 完全相同，丢弃本条"))
            continue
        seen[signature] = row
        unique.append(row)

    # 冲突：同一 query_id 出现多个不同版本 → 全部隔离，不猜哪条为准
    by_query: dict[str, list[dict]] = {}
    for row in unique:
        by_query.setdefault(row["query_id"], []).append(row)
    conflicted: set[int] = set()
    for query_id, rows in by_query.items():
        if len(rows) < 2:
            continue
        refs = ", ".join(r["row_ref"] for r in rows)
        for row in rows:
            conflicted.add(id(row))
            counters["rows_quarantine"] += 1
            issues.append(issue(
                "Q_DUP_CONFLICT", row_ref=row["row_ref"], field="query_id",
                raw_value=query_id,
                detail=f"同一查询编号出现 {len(rows)} 个版本（{refs}），整组隔离"))

    valid = [row for row in unique if id(row) not in conflicted]
    for row in valid:
        row.pop("_signature", None)
    counters["rows_valid"] = len(valid)
    expected_rows = (counters["rows_valid"] + counters["rows_dedup"]
                     + counters["rows_quarantine"] + counters["rows_empty"])
    counters["rows_balanced"] = expected_rows == counters["rows_data"]
    return {"rows": valid, "issues": issues, "counters": counters}


def check_row_account(counters: dict) -> dict:
    """行账：数据行 = 有效 + 去重 + 隔离 + 空行。"""
    left = counters["rows_data"]
    right = (counters["rows_valid"] + counters["rows_dedup"]
             + counters["rows_quarantine"] + counters["rows_empty"])
    return {"key": "行账", "left": left, "right": right, "ok": left == right}
