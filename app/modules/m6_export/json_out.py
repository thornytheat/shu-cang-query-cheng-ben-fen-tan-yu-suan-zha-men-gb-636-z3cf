"""JSON 安全序列化：Decimal → 字符串，大整数逐位输出，禁止科学计数法。"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal


def safe(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, int) and abs(value) >= 2 ** 53:
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def dumps(payload) -> str:
    return json.dumps(safe(payload), ensure_ascii=False, indent=2,
                      sort_keys=False)
