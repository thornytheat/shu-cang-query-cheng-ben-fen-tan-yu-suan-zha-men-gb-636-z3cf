"""运行清单与批次号：run_id、输入 sha256、规则版本、批次校验位。

批次号形如 CS-20261115-0003-7：前段日期、中段序号、末位 mod-11 校验位，
打印出来的清单可以人工核对校验位是否被改过。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import config


def new_run_id(prefix: str = "CSR") -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def batch_no(eval_date: dt.date, seq: int) -> str:
    return f"{config.BATCH_PREFIX}-{eval_date:%Y%m%d}-{seq:04d}"


def check_digit(no: str) -> str:
    """mod-11 校验位：数字位按位权 1,2,…(模 9 循环) 加权求和。"""
    digits = [ch for ch in no if ch.isdigit()]
    total = sum((idx % 9 + 1) * int(ch) for idx, ch in enumerate(digits))
    return config.CHECK_ALPHABET[total % config.CHECK_MOD]


def verify(no: str, digit: str) -> bool:
    return bool(no) and check_digit(no) == str(digit or "")


def batch_id(eval_date: dt.date, seq: int) -> tuple[str, str, str]:
    no = batch_no(eval_date, seq)
    digit = check_digit(no)
    return f"{no}-{digit}", no, digit


def build(*, run_id: str, batch_id_value: str, batch_no_value: str, check: str,
          batch_key: str, source: str, budget_source: str, input_digest: str,
          budget_digest: str, settings, params: dict, started_at: str,
          finished_at: str, elapsed_ms: int, stages: list[dict],
          counters: dict, budget_rows: int) -> dict:
    return {
        "run_id": run_id,
        "batch_id": batch_id_value,
        "batch_no": batch_no_value,
        "check_digit": check,
        "batch_key": batch_key,
        "source": source,
        "budget_source": budget_source,
        "rule_version": settings.rule_version,
        "eval_date": settings.eval_date.isoformat(),
        "input_digest": input_digest,
        "budget_digest": budget_digest,
        "budget_rows": budget_rows,
        "money": f"金额 Decimal，单位{config.MONEY_UNIT}，最小刻度 0.01",
        "scan": "扫描量折算 GB，Decimal 6 位",
        "timezone": "入库 UTC，展示 Asia/Shanghai(+08:00)",
        "params": params,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "stages": stages,
        "counters": counters,
        "replayable": "同 batch_key 重跑返回同一批次，不新增行、不改合计",
    }
