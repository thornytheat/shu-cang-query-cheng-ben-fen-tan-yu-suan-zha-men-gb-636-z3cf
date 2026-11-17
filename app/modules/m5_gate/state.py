"""批次状态：序号生成、批次号与校验位、批次摘要。"""
from __future__ import annotations

from app import config, manifest


def next_batch(store, eval_date) -> dict:
    seq = store.next_seq(f"{eval_date:%Y%m%d}")
    batch_id, no, digit = manifest.batch_id(eval_date, seq)
    return {"batch_id": batch_id, "batch_no": no, "check_digit": digit, "seq": seq}


def summary(batch: dict, totals: dict, counts: dict) -> dict:
    return {
        "batch_id": batch["batch_id"],
        "batch_no": batch["batch_no"],
        "check_digit": batch["check_digit"],
        "check_ok": manifest.verify(batch["batch_no"], batch["check_digit"]),
        "batch_key": batch["batch_key"],
        "run_id": batch["run_id"],
        "source": batch["source"],
        "budget_source": batch["budget_source"],
        "status": batch["status"],
        "version": int(batch.get("version", 1)),
        "created_at": batch["created_at"],
        "finished_at": batch.get("finished_at"),
        "total_cents": int(batch.get("total_cents", 0)),
        "row_count": int(batch.get("row_count", 0)),
        "input_digest": batch["input_digest"],
        "budget_digest": batch.get("budget_digest", ""),
        "money_unit": config.MONEY_UNIT,
        "totals": totals,
        "counts": counts,
    }
