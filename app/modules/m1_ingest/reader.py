"""流式记录读取：用 csv 模块解析，保留物理行号与分段号。

- 引号内换行不会被拆成两条记录；
- 空行与「字段全空」的记录分开统计；
- 分段导出时的重复表头被识别成表头，不计入数据行。
"""
from __future__ import annotations

import csv
import io

from . import sniff


def read_records(text: str) -> dict:
    delimiter = sniff.detect_delimiter(text)
    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, delimiter=delimiter, quotechar='"',
                        doublequote=True, strict=False)
    records: list[dict] = []
    blank_lines = 0
    header: list[str] = []
    header_key: tuple[str, ...] | None = None
    segment = 0
    duplicate_headers = 0
    for row in reader:
        line_no = reader.line_num
        if not row:
            blank_lines += 1
            records.append({"line_no": line_no, "segment": max(segment, 1),
                            "fields": [], "kind": "blank"})
            continue
        key = tuple(cell.strip().replace("\ufeff", "") for cell in row)
        if not header:
            header = list(row)
            header_key = key
            segment = 1
            records.append({"line_no": line_no, "segment": segment,
                            "fields": list(row), "kind": "header"})
            continue
        if key == header_key:
            segment += 1
            duplicate_headers += 1
            records.append({"line_no": line_no, "segment": segment,
                            "fields": list(row), "kind": "header"})
            continue
        if all(cell.strip() == "" for cell in row):
            records.append({"line_no": line_no, "segment": segment,
                            "fields": list(row), "kind": "data", "empty": True})
            continue
        records.append({"line_no": line_no, "segment": segment,
                        "fields": list(row), "kind": "data"})
    return {
        "delimiter": delimiter,
        "header": header,
        "records": records,
        "segments": segment,
        "duplicate_headers": duplicate_headers,
        "blank_lines": blank_lines,
        "trailing_newline": text.endswith("\n"),
    }
