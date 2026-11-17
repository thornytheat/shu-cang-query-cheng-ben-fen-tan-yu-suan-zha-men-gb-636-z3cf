"""CSV 导出：UTF-8 BOM + 原值 + 原因码 + 行号，保证人工复核能对齐回原文。

BOM 是为了让表格软件直接认出中文；本系统读回自己的导出时会剥离 BOM。
"""
from __future__ import annotations

import csv
import codecs
import io


def to_csv(fields: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(fields)
    for row in rows:
        writer.writerow(["" if row.get(name) is None else row.get(name)
                         for name in fields])
    return codecs.BOM_UTF8.decode("utf-8") + buffer.getvalue()


def row_count(text: str) -> int:
    return max(len(text.splitlines()) - 1, 0)
