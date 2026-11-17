"""M1 编排：读文件 → 采信 → 质量 → 计费 → 分摊 → 闸门 → 导出清单，一次跑完。

同 batch_key 的重复导入直接复用已有批次：不新增行、不改合计，只回一个 reused 标记。
"""
from __future__ import annotations

import datetime as dt
import time

from app import config, errors, manifest, reasons
from app.modules.m3_quality import service as quality_service
from app.modules.m2_billing import service as billing_service
from app.modules.m4_alloc import invariant, service as alloc_service
from app.modules.m5_gate import batchkey, service as gate_service, state as gate_state

from . import decode, reader, sniff

ALLOWED_SUFFIXES = (".csv", ".txt")


def resolve_sample(settings, name: str):
    samples = settings.samples
    if not name or not isinstance(name, str):
        raise errors.BadRequest("缺少 source", code="E_BAD_REQUEST")
    clean = name.strip()
    if "/" in clean or "\\" in clean or clean.startswith("."):
        raise errors.BadRequest("来源只能是 sample_data 下的文件名，不能带路径",
                                code="E_BAD_REQUEST")
    path = (samples / clean).resolve()
    if samples not in path.parents and path.parent != samples:
        raise errors.BadRequest("来源路径越界", code="E_BAD_REQUEST")
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise errors.BadRequest(f"只支持 {'/'.join(ALLOWED_SUFFIXES)}",
                                code="E_BAD_REQUEST")
    if not path.is_file():
        raise errors.NotFound(f"样例不存在：{clean}", code="E_NOT_FOUND")
    return path


def list_samples(settings) -> list[dict]:
    samples = settings.samples
    if not samples.is_dir():
        return []
    rows = []
    for path in sorted(samples.glob("*.csv")):
        rows.append({"name": path.name, "bytes": path.stat().st_size})
    return rows


def _now() -> str:
    return dt.datetime.now(config.UTC).isoformat(timespec="seconds")


def _issue(code: str, *, row_ref=None, field=None, raw_value=None,
           detail: str = "") -> dict:
    return reasons.evidence(code, row_ref=row_ref, field=field, raw_value=raw_value,
                            detail=detail)


def empty_state(counts: dict, blank_lines: int) -> str:
    """空态三态分开：仅表头 / 全空行 / 正常。"""
    if counts["rows_data"] == 0:
        return "ONLY_HEADER" if blank_lines == 0 else "NO_ROWS"
    if counts["rows_valid"] == 0 and counts["rows_empty"] == counts["rows_data"]:
        return "ALL_EMPTY"
    return ""


def run_import(store, settings, *, source: str, budget_source: str | None = None,
               charset: str | None = None) -> dict:
    started_at = _now()
    clock = time.perf_counter()
    path = resolve_sample(settings, source)
    data = path.read_bytes()
    if not data:
        raise errors.Unprocessable("样例是空文件（0 字节）", code="E_EMPTY_FILE")
    budget_name = budget_source or settings.default_budget_source
    budget = gate_service.load_budgets(settings, budget_name)

    input_digest = manifest.digest_bytes(data)
    key = batchkey.batch_key(source=path.name, input_digest=input_digest,
                             budget_digest=budget["digest"],
                             rule_version=settings.rule_version,
                             eval_date=settings.eval_date.isoformat())
    existing = store.find_done_by_key(key)
    if existing is not None:
        summary = gate_state.summary(existing, store.load_totals(existing["batch_id"]),
                                     store.count_rows(existing["batch_id"]))
        summary["reused"] = True
        return summary

    ids = gate_state.next_batch(store, settings.eval_date)
    batch = {
        "batch_id": ids["batch_id"], "batch_no": ids["batch_no"],
        "check_digit": ids["check_digit"], "batch_key": key,
        "run_id": manifest.new_run_id(), "source": path.name,
        "budget_source": budget_name, "status": "done", "version": 1,
        "created_at": started_at, "finished_at": None,
        "input_digest": input_digest, "budget_digest": budget["digest"],
        "total_cents": 0, "row_count": 0, "note": "",
    }

    # 1 采信
    t0 = time.perf_counter()
    try:
        decoded = decode.decode_bytes(data, charset)
    except decode.DecodeFailure as exc:
        store.save_attempt(batch=batch, stages=[{
            "stage": "ingest", "in_count": 0, "out_count": 0,
            "quarantine_count": 1, "status": "failed", "elapsed_ms": 0,
            "note": exc.message}], note=exc.message)
        raise errors.Unprocessable(exc.message, code="Q_DECODE_FAILED",
                                   detail={"line_no": exc.line_no})
    issues: list[dict] = []
    if decoded["fallback"]:
        issues.append(_issue("Q_CHARSET_FALLBACK", field="encoding",
                             raw_value=decoded["declared"],
                             detail=f"按 {decoded['encoding']} 解码"))
    if decoded["bom_stripped"]:
        issues.append(_issue("Q_BOM_STRIPPED", field="encoding",
                             raw_value="UTF-8 BOM"))
    parsed = reader.read_records(decoded["text"])
    try:
        probed = sniff.probe(parsed)
    except ValueError as exc:
        store.save_attempt(batch=batch, stages=[{
            "stage": "ingest", "in_count": 0, "out_count": 0,
            "quarantine_count": 1, "status": "failed", "elapsed_ms": 0,
            "note": str(exc)}], note=str(exc))
        raise errors.Unprocessable(str(exc), code="E_NO_HEADER")
    if probed["missing_columns"]:
        detail = "缺少列：" + "、".join(probed["missing_columns"])
        store.save_attempt(batch=batch, stages=[{
            "stage": "ingest", "in_count": probed["data_count"], "out_count": 0,
            "quarantine_count": probed["data_count"], "status": "failed",
            "elapsed_ms": 0, "note": detail}], note=detail)
        raise errors.Unprocessable(detail, code="Q_MISSING_FIELD",
                                   detail={"missing": probed["missing_columns"]})
    if probed["duplicate_headers"]:
        issues.append(_issue("Q_DUP_HEADER", field="header",
                             raw_value=str(probed["duplicate_headers"]),
                             detail=f"共 {probed['segments']} 段"))
    ingest_ms = int((time.perf_counter() - t0) * 1000)

    # 2 质量 + 3 计费
    t1 = time.perf_counter()
    quality = quality_service.run(probed)
    issues.extend(quality["issues"])
    counters = quality["counters"]
    quality_ms = int((time.perf_counter() - t1) * 1000)
    t2 = time.perf_counter()
    priced = billing_service.price_rows(quality["rows"])
    for row in priced:
        if row["status"] == "ZERO_SCAN":
            row["flags"] = list(row["flags"])
    billing_ms = int((time.perf_counter() - t2) * 1000)

    # 4 分摊
    t3 = time.perf_counter()
    alloc = alloc_service.allocate(priced)
    issues.extend(alloc["issues"])
    dept_scan = billing_service.dept_scan_totals(priced)
    alloc_ms = int((time.perf_counter() - t3) * 1000)

    # 5 闸门
    t4 = time.perf_counter()
    issues.extend(budget["issues"])
    gate_result = gate_service.run_gate(alloc["shares"], priced, budget["budgets"])
    issues.extend(gate_result["issues"])
    depts = alloc_service.dept_rows(alloc["dept_totals"], dept_scan,
                                    budget["budgets"])
    depts = alloc_service.apply_gate(depts, gate_result["depts"])
    gate_ms = int((time.perf_counter() - t4) * 1000)

    kept_cents = sum(item["kept_cents"] for item in depts)
    blocked_cents = sum(item["blocked_cents"] for item in depts)
    totals = {
        "rows_data": counters["rows_data"], "rows_valid": counters["rows_valid"],
        "rows_quarantine": counters["rows_quarantine"],
        "rows_dedup": counters["rows_dedup"], "rows_empty": counters["rows_empty"],
        "blank_lines": counters["blank_lines"],
        "amount_total": alloc["total_cents"],
        "amount_allocated": alloc["allocated_cents"],
        "amount_unallocated": alloc["unallocated_cents"],
        "amount_kept": kept_cents, "amount_blocked": blocked_cents,
        "shared_query_count": alloc["shared_query_count"],
        "dept_count": len(depts), "segment_count": probed["segments"],
        "duplicate_headers": probed["duplicate_headers"],
        "residual_diff": 0,
    }
    accounts = invariant.all_accounts(
        counters=counters, total_cents=alloc["total_cents"],
        allocated_cents=alloc["allocated_cents"],
        unallocated_cents=alloc["unallocated_cents"],
        kept_cents=kept_cents, blocked_cents=blocked_cents,
        query_costs=alloc["query_costs"], shares_by_query=alloc["shares_by_query"])
    totals["residual_diff"] = accounts[3]["right"]

    finished_at = _now()
    elapsed_ms = int((time.perf_counter() - clock) * 1000)
    stages = [
        {"stage": "ingest", "in_count": len(probed["records"]),
         "out_count": probed["data_count"], "quarantine_count": 0,
         "status": "ok", "elapsed_ms": ingest_ms,
         "note": f"{decoded['encoding']} / 分隔符 {probed['delimiter']!r} / "
                 f"{probed['segments']} 段"},
        {"stage": "quality", "in_count": counters["rows_data"],
         "out_count": counters["rows_valid"],
         "quarantine_count": counters["rows_quarantine"], "status": "ok",
         "elapsed_ms": quality_ms, "note": f"{len(quality['distribution'])} 种原因码"},
        {"stage": "billing", "in_count": counters["rows_valid"],
         "out_count": len(priced), "quarantine_count": 0, "status": "ok",
         "elapsed_ms": billing_ms, "note": "按小时桶 + 时段单价"},
        {"stage": "alloc", "in_count": len(priced), "out_count": len(depts),
         "quarantine_count": 1 if alloc["unallocated_cents"] else 0,
         "status": "ok", "elapsed_ms": alloc_ms,
         "note": f"共享查询 {alloc['shared_query_count']} 条"},
        {"stage": "gate", "in_count": alloc["allocated_cents"],
         "out_count": kept_cents, "quarantine_count": blocked_cents,
         "status": "ok", "elapsed_ms": gate_ms, "note": "金额单位：分"},
        {"stage": "export", "in_count": len(issues), "out_count": len(issues),
         "quarantine_count": 0, "status": "ok", "elapsed_ms": 0,
         "note": "证据行可导出"},
    ]
    batch["finished_at"] = finished_at
    batch["total_cents"] = alloc["total_cents"]
    batch["row_count"] = counters["rows_valid"]
    batch["note"] = empty_state(counters, counters["blank_lines"])

    run_manifest = manifest.build(
        run_id=batch["run_id"], batch_id_value=batch["batch_id"],
        batch_no_value=batch["batch_no"], check=batch["check_digit"],
        batch_key=key, source=path.name, budget_source=budget_name,
        input_digest=input_digest, budget_digest=budget["digest"],
        settings=settings, params={"charset": charset or "auto",
                                   "source_bytes": len(data)},
        started_at=started_at, finished_at=finished_at, elapsed_ms=elapsed_ms,
        stages=stages,
        counters={k: totals[k] for k in ("rows_data", "rows_valid",
                                         "rows_quarantine", "rows_dedup",
                                         "amount_total")},
        budget_rows=budget["rows"])

    store.save_run(batch=batch, bills=priced, quarantine=issues,
                   shares=alloc["shares"], depts=depts, gates=gate_result["gates"],
                   stages=stages, totals=totals, manifest=run_manifest)
    store.capture_snapshot(batch["batch_id"], "import")
    summary = gate_state.summary(batch, totals, store.count_rows(batch["batch_id"]))
    summary["reused"] = False
    summary["empty_state"] = batch["note"]
    summary["accounts"] = accounts
    summary["elapsed_ms"] = elapsed_ms
    return summary


def load_page_context(store, settings, batch: dict | None) -> dict:
    if batch is None:
        return {"totals": {}, "accounts": [], "stages": [], "samples": [],
                "contract": None}
    totals = store.load_totals(batch["batch_id"])
    counters = {
        "rows_data": int(totals.get("rows_data", 0)),
        "rows_valid": int(totals.get("rows_valid", 0)),
        "rows_quarantine": int(totals.get("rows_quarantine", 0)),
        "rows_dedup": int(totals.get("rows_dedup", 0)),
        "rows_empty": int(totals.get("rows_empty", 0)),
        "blank_lines": int(totals.get("blank_lines", 0)),
    }
    accounts = invariant.all_accounts(
        counters=counters,
        total_cents=int(totals.get("amount_total", 0)),
        allocated_cents=int(totals.get("amount_allocated", 0)),
        unallocated_cents=int(totals.get("amount_unallocated", 0)),
        kept_cents=int(totals.get("amount_kept", 0)),
        blocked_cents=int(totals.get("amount_blocked", 0)),
        query_costs={}, shares_by_query={})
    accounts[3] = {"key": "尾差账", "left": 0,
                   "right": int(totals.get("residual_diff", 0)),
                   "ok": int(totals.get("residual_diff", 0)) == 0,
                   "note": "每条共享查询的份额合计 = 该查询成本"}
    return {"totals": totals, "accounts": accounts, "counters": counters}
