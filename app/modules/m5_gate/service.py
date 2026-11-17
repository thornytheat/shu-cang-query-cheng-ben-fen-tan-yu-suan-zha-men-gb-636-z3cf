"""M5 编排：读预算表、跑闸门、重算部门账、落库并升版本。"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from app import config, errors, manifest, reasons
from app.modules.m1_ingest import decode
from app.modules.m2_billing import coerce

from . import batchkey, gate, state


def load_budgets(settings, name: str) -> dict:
    samples = settings.samples
    path = (samples / name).resolve()
    if not str(path).startswith(str(samples)):
        raise errors.BadRequest("预算表路径越界", code="E_BAD_REQUEST")
    if not path.is_file():
        raise errors.NotFound(f"预算表不存在：{name}", code="E_NOT_FOUND")
    data = path.read_bytes()
    bundle = decode.decode_bytes(data)
    reader = csv.reader(io.StringIO(bundle["text"], newline=""))
    budgets: dict[str, int] = {}
    issues: list[dict] = []
    rows = 0
    header: list[str] | None = None
    for row in reader:
        if not row or all(not cell.strip() for cell in row):
            continue
        if header is None:
            header = [cell.strip().lower() for cell in row]
            continue
        rows += 1
        dept = coerce.clean_text(row[0])
        raw_budget = row[1] if len(row) > 1 else ""
        try:
            value = coerce.parse_decimal(raw_budget, "budget_yuan",
                                         allow_sentinel=True)
        except coerce.CoerceError as exc:
            issues.append({"code": exc.code, "stage": "gate",
                           "level": reasons.level(exc.code), "row_ref": f"第 {rows + 1} 行",
                           "field": "budget_yuan", "raw_value": exc.raw,
                           "detail": "预算值不可解析，该部门按无预算处理"})
            continue
        if value is None or dept == "":
            issues.append({"code": "Q_GATE_NO_BUDGET", "stage": "gate",
                           "level": "warn", "row_ref": f"第 {rows + 1} 行",
                           "field": "budget_yuan", "raw_value": raw_budget,
                           "detail": "预算为空或部门缺失，跳过该行"})
            continue
        cents = coerce.to_cents(value)
        if dept in budgets and budgets[dept] != cents:
            issues.append({"code": "Q_DUP_CONFLICT", "stage": "gate",
                           "level": "error", "row_ref": f"第 {rows + 1} 行",
                           "field": "dept", "raw_value": dept,
                           "detail": "同一部门两条预算，保留第一条"})
            continue
        budgets[dept] = cents
    return {
        "budgets": budgets,
        "issues": issues,
        "rows": rows,
        "digest": manifest.digest_bytes(data),
        "encoding": bundle["encoding"],
    }


def items_by_dept(shares: list[dict], bills: list[dict]) -> dict[str, list[dict]]:
    scores = {bill["query_id"]: bill["value_score"] for bill in bills}
    out: dict[str, list[dict]] = {}
    for share in shares:
        if share["kind"] == "UNALLOCATED":
            continue
        out.setdefault(share["dept"], []).append({
            "query_id": share["query_id"],
            "cost_cents": int(share["share_cents"]),
            "value_score": scores.get(share["query_id"], Decimal(0)),
        })
    return out


def run_gate(shares: list[dict], bills: list[dict], budgets: dict[str, int]) -> dict:
    result = gate.evaluate(items_by_dept(shares, bills), budgets)
    result["issues"] = gate.issues_for(result["depts"])
    return result


def apply(store, *, batch_id: str, budgets: dict[str, int],
          expected_version: int | None) -> dict:
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次不存在：{batch_id}", code="Q_NO_BATCH")
    batchkey.require_version(batch["version"], expected_version)
    shares = store.load_shares(batch_id)
    bills = store.load_bills(batch_id)
    result = run_gate(shares, bills, budgets)
    scan_map = {row["dept"]: row["scan_gb"] for row in store.load_depts(batch_id)}
    depts = [dict(row, scan_gb=scan_map.get(row["dept"], Decimal(0)))
             for row in result["depts"]]
    kept = sum(item["kept_cents"] for item in depts)
    blocked = sum(item["blocked_cents"] for item in depts)
    allocated = sum(item["allocated_cents"] for item in depts)
    totals = store.load_totals(batch_id)
    totals.update({"amount_kept": kept, "amount_blocked": blocked})
    stage = {"stage": "gate", "in_count": allocated, "out_count": kept,
             "quarantine_count": blocked, "status": "ok", "elapsed_ms": 0,
             "note": f"重算版本 {int(batch['version']) + 1}"}
    version = store.save_gate(batch_id=batch_id, depts=depts, gates=result["gates"],
                              totals=totals, stage=stage,
                              exit_codes=result["issues"])
    store.capture_snapshot(batch_id, "gate")
    return {"batch_id": batch_id, "version": version, "depts": depts,
            "gates": result["gates"], "kept_cents": kept, "blocked_cents": blocked,
            "budgets": {k: v for k, v in budgets.items()}}


def listing(store) -> list[dict]:
    rows = []
    for batch in store.list_batches(50):
        totals = store.load_totals(batch["batch_id"])
        rows.append(state.summary(batch, totals, {}))
    return rows


def batch_summary(store, batch: dict) -> dict:
    return state.summary(batch, store.load_totals(batch["batch_id"]),
                         store.count_rows(batch["batch_id"]))
