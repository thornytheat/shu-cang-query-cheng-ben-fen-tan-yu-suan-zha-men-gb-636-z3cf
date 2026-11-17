"""M7 编排：申诉 → 改判 → 按规则重算 → 写新版本 → 差异对比与回滚。

重算复用既有模块，不另写一套算法：
- 改判后的行交回 m2 计费（单位、时长分桶、时段单价、规则叠加）；
- 分摊交回 m4（加权 + 最大余额法 + 守恒断言）；
- 闸门交回 m5（收益/成本排序 + 预算截断）。
本节只多做三件事：改判过的查询要复核时段（m7 buckets 交叉对账）、
每次结论写入新版本并留快照、回滚按旧快照写成一个新版本。

不变量在落库前检查：总额 = 分摊 + 未分摊；分摊 = 保留 + 截断；每条共享查询的
份额合计 = 该查询成本。任一条不成立就拒，不写半版数据。
"""
from __future__ import annotations

from decimal import Decimal

from app import errors, reasons
from app.modules.m2_billing import coerce, service as billing_service, units
from app.modules.m4_alloc import invariant, service as alloc_service
from app.modules.m5_gate import batchkey, service as gate_service

from . import appeal, buckets, rollback

COUNT_KEYS = ("rows_data", "rows_valid", "rows_quarantine", "rows_dedup", "rows_empty")


def batch_of(store, batch_id: str | None) -> dict:
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    return batch


def pricing_row(bill: dict) -> dict:
    """库里的账单行 → 计费入参；共享部门从原始字段重新解析。"""
    raw = bill.get("raw") or {}
    try:
        shared = coerce.parse_shared(raw.get("shared_with", ""))
    except coerce.CoerceError:
        shared = []
    return {
        "query_id": bill["query_id"], "dept": bill["dept"],
        "project": bill.get("project", ""), "started_at": bill["started_at"],
        "duration_s": Decimal(str(bill["duration_s"])),
        "scan_gb": Decimal(str(bill["scan_gb"])),
        "scan_unit": bill["scan_unit"], "scan_raw": bill["scan_raw"],
        "concurrency": int(bill["concurrency"]),
        "value_score": Decimal(str(bill["value_score"])),
        "shared": shared, "flags": list(bill.get("flags") or []),
        "row_ref": bill["row_ref"], "line_no": bill["line_no"],
        "segment": bill["segment"], "raw": raw,
    }


def apply_fix(row: dict, fix: dict) -> dict:
    """改判字段落到一行计费入参；扫描量按改判后的单位重新折算 GB。"""
    out = dict(row)
    if fix.get("dept"):
        out["dept"] = fix["dept"]
    if fix.get("started_at"):
        out["started_at"] = fix["started_at"]
    if fix.get("duration_s"):
        out["duration_s"] = Decimal(str(fix["duration_s"]))
    if fix.get("scan_size") and fix.get("scan_unit"):
        size = Decimal(str(fix["scan_size"]))
        unit = fix["scan_unit"]
        out["scan_gb"] = units.to_gb(size, unit)
        out["scan_unit"] = unit
        out["scan_raw"] = coerce.clean_text(fix["scan_size"])
    return out


def check_row(query_id: str, started_at, duration_s, hour_cost_cents: int) -> dict:
    """一条查询的时段复核：逐桶明细 + 与计费金额的对账结果。"""
    cut = buckets.rebucket(started_at, duration_s)
    check = buckets.reconcile(cut, duration_s, hour_cost_cents)
    return {
        "query_id": query_id, **check,
        "buckets": [{"local": item["local"], "hours": str(item["hours"]),
                     "phase": item["phase"], "rate": str(item["rate"]),
                     "cost": str(item["cost"])} for item in cut],
    }


def bucket_checks(priced: list[dict], corrections: dict) -> tuple[list[dict], list[dict]]:
    """改判过的查询逐条复核时段：时长合计与时长金额都要和计费对得上。"""
    checks: list[dict] = []
    issues: list[dict] = []
    for row in priced:
        if row["query_id"] not in corrections:
            continue
        check = check_row(row["query_id"], row["started_at"], row["duration_s"],
                          row["hour_cost_cents"])
        checks.append(check)
        if not check["ok"]:
            issues.append(reasons.evidence(
                "Q_APPEAL_BUCKET", row_ref=row["query_id"], field="started_at",
                raw_value=row["started_at"],
                detail=f"时长 {check['hours']} / 金额 {check['cents']} 分"
                       f" 与计费 {check['billed_cents']} 分不一致"))
    return checks, issues


def page_buckets(store, batch_id: str, version: int) -> list[dict]:
    """本版被改判的查询，用当前账单重切一遍时段（只读，给页面与接口复核）。"""
    targets = {row["query_id"] for row in store.load_appeals(batch_id)
               if row["version"] == int(version) and row["status"] == appeal.APPLIED
               and row["query_id"]}
    bills = {bill["query_id"]: bill for bill in store.load_bill_costs(batch_id)}
    rows = []
    for query_id in sorted(targets):
        bill = bills.get(query_id)
        if bill is None:
            continue
        rows.append(check_row(query_id, bill["started_at"], bill["duration_s"],
                              bill["hour_cost_cents"]))
    return rows


def bill_state(priced: list[dict]) -> list[dict]:
    """计费结果 → 版本里保存的账单成本列（回滚要靠它恢复账单）。"""
    return [{
        "query_id": row["query_id"], "dept": row["dept"],
        "started_at": row["started_at"], "duration_s": str(row["duration_s"]),
        "scan_gb": str(row["scan_gb"]), "scan_unit": row["scan_unit"],
        "scan_raw": row["scan_raw"], "status": row["status"],
        "cost_cents": int(row["cost_cents"]),
        "scan_cost_cents": int(row["scan_cost_cents"]),
        "hour_cost_cents": int(row["hour_cost_cents"]),
        "addon_cents": int(row["addon_cents"]),
        "rules": list(row["rules"]), "buckets": list(row["buckets"]),
    } for row in priced]


def recompute(store, settings, batch: dict, bills: list[dict], corrections: dict,
              issues: list[dict]) -> dict:
    """改判后整批重算：计费 → 分摊 → 闸门，并做守恒检查。"""
    budget_name = batch.get("budget_source") or settings.default_budget_source
    budget = gate_service.load_budgets(settings, budget_name)
    rows = [apply_fix(pricing_row(bill), corrections.get(bill["query_id"]) or {})
            for bill in bills]
    priced = billing_service.price_rows(rows)
    checks, bucket_issues = bucket_checks(priced, corrections)
    issues.extend(bucket_issues)
    if bucket_issues:
        raise errors.Unprocessable("改判后的时段重算与计费不一致，本次申诉不落地",
                                   code="Q_APPEAL_BUCKET",
                                   detail={"issues": bucket_issues})
    alloc = alloc_service.allocate(priced)
    issues.extend(alloc["issues"])
    gate_result = gate_service.run_gate(alloc["shares"], priced, budget["budgets"])
    issues.extend(gate_result["issues"])
    dept_scan = billing_service.dept_scan_totals(priced)
    depts = alloc_service.apply_gate(
        alloc_service.dept_rows(alloc["dept_totals"], dept_scan, budget["budgets"]),
        gate_result["depts"])
    kept = sum(int(row["kept_cents"]) for row in depts)
    blocked = sum(int(row["blocked_cents"]) for row in depts)
    totals = store.load_totals(batch["batch_id"])
    totals.update({
        "amount_total": alloc["total_cents"],
        "amount_allocated": alloc["allocated_cents"],
        "amount_unallocated": alloc["unallocated_cents"],
        "amount_kept": kept, "amount_blocked": blocked,
        "shared_query_count": alloc["shared_query_count"],
        "dept_count": len(depts),
    })
    counters = {key: int(totals.get(key, 0)) for key in COUNT_KEYS}
    accounts = invariant.all_accounts(
        counters=counters, total_cents=alloc["total_cents"],
        allocated_cents=alloc["allocated_cents"],
        unallocated_cents=alloc["unallocated_cents"],
        kept_cents=kept, blocked_cents=blocked,
        query_costs=alloc["query_costs"], shares_by_query=alloc["shares_by_query"])
    totals["residual_diff"] = accounts[3]["right"]
    if not invariant.all_balanced(accounts):
        raise errors.Unprocessable("改判后账目不平，本次申诉不落地",
                                   code="Q_APPEAL_IMBALANCE",
                                   detail={"accounts": accounts})
    return {"shares": alloc["shares"], "depts": depts, "gates": gate_result["gates"],
            "totals": totals, "accounts": accounts, "bills": bill_state(priced),
            "priced": priced, "checks": checks}


def _appeal_row(verdict: dict, version: int, batch_id: str) -> dict:
    index = int(verdict["index"])
    return {
        "appeal_id": verdict.get("appeal_id") or f"AP-{version}-{index:02d}",
        "query_id": verdict.get("query_id") or "",
        "dept": verdict.get("dept") or verdict.get("claim_dept") or "",
        "target_dept": verdict.get("target_dept") or "",
        "fix": verdict.get("fix") or {},
        "status": verdict.get("status") or appeal.REJECTED,
        "code": verdict.get("code") or "",
        "note": verdict.get("note") or "",
        "batch_id": batch_id,
    }


def appeal_view(row: dict) -> dict:
    return {**row, "label": reasons.label(row.get("code", "")),
            "level": reasons.level(row.get("code", ""))}


def apply(store, settings, *, batch_id: str | None, entries, expected_version=None) -> dict:
    batch = batch_of(store, batch_id)
    batchkey.require_version(batch["version"], expected_version)
    parsed = appeal.parse_entries(entries)
    issues: list[dict] = appeal.parse_issues(parsed)
    kept, dedupe_issues = appeal.dedupe(parsed)
    issues.extend(dedupe_issues)
    bills = store.load_bills(batch["batch_id"])
    corrections, apply_issues = appeal.corrected_rows(bills, kept)
    issues.extend(apply_issues)
    accepted = appeal.applied(kept)
    if not accepted:
        raise errors.Unprocessable("没有可采纳的申诉", code="Q_APPEAL_NONE",
                                   detail={"issues": issues})
    result = recompute(store, settings, batch, bills, corrections, issues)
    before = store.state_payload(batch["batch_id"])
    totals = dict(result["totals"])
    totals["appeal_count"] = int(before["totals"].get("appeal_count", 0)) + len(parsed)
    totals["appeal_applied"] = (int(before["totals"].get("appeal_applied", 0))
                                + len(accepted))
    next_version = int(batch["version"]) + 1
    rows = [_appeal_row(verdict, next_version, batch["batch_id"]) for verdict in parsed]
    rejected = sum(1 for verdict in parsed if verdict["status"] == appeal.REJECTED)
    stage = {"stage": "appeal", "in_count": len(parsed), "out_count": len(accepted),
             "quarantine_count": rejected, "status": "ok", "elapsed_ms": 0,
             "note": f"改判 {len(corrections)} 条查询"}
    version = store.save_version_state(
        batch_id=batch["batch_id"], expected_version=batch["version"],
        shares=result["shares"], depts=result["depts"], gates=result["gates"],
        totals=totals, stage=stage, quarantine=issues, snapshot_kind="appeal",
        bills=result["bills"], appeals=rows)
    after = store.state_payload(batch["batch_id"])
    return {
        "batch_id": batch["batch_id"], "batch_no": batch["batch_no"],
        "version": version,
        "applied": len(accepted),
        "merged": sum(1 for verdict in parsed if verdict["status"] == appeal.MERGED),
        "rejected": rejected,
        "corrections": [{"query_id": query_id, **fix}
                        for query_id, fix in sorted(corrections.items())],
        "issues": issues,
        "appeals": [appeal_view(row) for row in rows],
        "diff": rollback.diff(before, after),
        "accounts": result["accounts"],
        "totals": totals,
        "buckets": result["checks"],
    }


def rollback_to(store, *, batch_id: str | None, to_version: int,
                expected_version=None) -> dict:
    batch = batch_of(store, batch_id)
    batchkey.require_version(batch["version"], expected_version)
    snapshots = store.load_snapshots(batch["batch_id"])
    available = [item["version"] for item in snapshots]
    code = rollback.check_target(batch["version"], to_version, available)
    if code == "Q_ROLLBACK_SAME":
        raise errors.Conflict(f"当前已经是版本 {batch['version']}，没有可回滚的内容",
                              code=code,
                              detail={"current_version": int(batch["version"])})
    if code == "Q_ROLLBACK_TARGET":
        raise errors.NotFound(f"没有版本 {to_version} 的快照", code=code,
                              detail={"versions": available})
    state = store.load_snapshot(batch["batch_id"], int(to_version)) or {}
    before = store.state_payload(batch["batch_id"])
    totals = dict(state.get("totals") or {})
    totals["rollback_from"] = int(batch["version"])
    totals["rollback_to"] = int(to_version)
    evidence = [reasons.evidence(
        "Q_ROLLBACK_DONE", field="version", raw_value=str(to_version),
        detail=f"从版本 {batch['version']} 回到版本 {to_version}")]
    stage = {"stage": "appeal", "in_count": 1, "out_count": 1,
             "quarantine_count": 0, "status": "ok", "elapsed_ms": 0,
             "note": f"回滚到版本 {to_version}"}
    version = store.save_version_state(
        batch_id=batch["batch_id"], expected_version=batch["version"],
        shares=state.get("shares") or [], depts=state.get("depts") or [],
        gates=state.get("gates") or [], totals=totals, stage=stage,
        quarantine=evidence, snapshot_kind="rollback",
        bills=state.get("bills") or [])
    after = store.state_payload(batch["batch_id"])
    return {
        "batch_id": batch["batch_id"], "batch_no": batch["batch_no"],
        "version": version, "restored_from": int(to_version),
        "diff": rollback.diff(before, after),
        "accounts": rollback.accounts(after), "issues": evidence, "totals": totals,
    }


def payload(store, *, batch_id: str | None = None, left_version: int | None = None,
            right_version: int | None = None) -> dict:
    batch = batch_of(store, batch_id)
    snapshots = store.load_snapshots(batch["batch_id"])
    current = int(batch["version"])
    right = int(right_version) if right_version else current
    left = int(left_version) if left_version else max(current - 1, 0)

    def state_of(version: int) -> dict:
        if version == current:
            return store.state_payload(batch["batch_id"])
        return store.load_snapshot(batch["batch_id"], version) or {
            "depts": [], "totals": {}}

    if left <= 0 or left == right:
        change = {"depts": [], "totals": [], "moved_cents": 0,
                  "allocated_delta": 0, "balanced": True}
    else:
        change = rollback.diff(state_of(left), state_of(right))
    current_state = store.state_payload(batch["batch_id"])
    return {
        "batch_id": batch["batch_id"], "batch_no": batch["batch_no"],
        "check_digit": batch["check_digit"], "version": current,
        "appeals": [appeal_view(row) for row in store.load_appeals(batch["batch_id"])],
        "versions": [{**item, "kind_label": rollback.kind_label(item["kind"])}
                     for item in snapshots],
        "diff": change, "left_version": left, "right_version": right,
        "buckets": page_buckets(store, batch["batch_id"], current),
        "accounts": rollback.accounts(current_state),
        "totals": current_state["totals"],
        "depts": current_state["depts"],
    }


def page_context(store, settings, batch_id: str | None = None,
                 left_version: int | None = None,
                 right_version: int | None = None) -> dict:
    batch = batch_of(store, batch_id)
    data = payload(store, batch_id=batch["batch_id"], left_version=left_version,
                   right_version=right_version)
    bills = store.load_bill_costs(batch["batch_id"])
    data.update({
        "batch": batch,
        "queries": [{"query_id": bill["query_id"], "dept": bill["dept"],
                     "cost_cents": int(bill["cost_cents"])}
                    for bill in sorted(bills, key=lambda row: row["query_id"])],
        "rollback_targets": [item for item in data["versions"]
                             if item["version"] < data["version"]],
    })
    return data
