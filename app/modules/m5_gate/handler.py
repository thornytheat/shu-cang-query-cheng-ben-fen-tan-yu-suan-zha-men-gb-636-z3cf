"""M5 预算闸门与批次入口：重算闸门、看决策、看批次清单。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors, web
from app.modules.m2_billing import coerce

from . import batchkey, service

router = APIRouter(tags=["gate"])


def _parse_budgets(raw: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for dept, value in raw.items():
        try:
            parsed = coerce.parse_decimal(value, "budget_yuan", allow_sentinel=True)
        except coerce.CoerceError as exc:
            raise errors.BadRequest(exc.message, code=exc.code,
                                    detail={"field": "budgets", "raw": exc.raw})
        if parsed is None:
            continue
        out[str(dept)] = coerce.to_cents(parsed)
    return out


@router.post("/api/gates")
async def post_gates(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) or not body:
        raise errors.BadRequest("请求体必须是 JSON 对象", code="E_BAD_REQUEST")
    store = request.app.state.store
    batch_id = body.get("batch_id")
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    expected = body.get("expected_version")
    if expected is not None:
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            raise errors.BadRequest("expected_version 必须是整数",
                                    code="E_BAD_REQUEST")
    budgets_raw = body.get("budgets")
    if budgets_raw is None:
        budgets = {d["dept"]: int(d["budget_cents"]) for d in store.load_depts(
            batch["batch_id"]) if d["budget_cents"] is not None}
    elif isinstance(budgets_raw, dict):
        budgets = _parse_budgets(budgets_raw)
    else:
        raise errors.BadRequest("budgets 必须是对象", code="E_BAD_REQUEST")
    result = service.apply(store, batch_id=batch["batch_id"], budgets=budgets,
                           expected_version=expected)
    return {
        "batch_id": result["batch_id"],
        "version": result["version"],
        "kept_cents": result["kept_cents"],
        "blocked_cents": result["blocked_cents"],
        "depts": result["depts"],
    }


@router.get("/api/gates")
def get_gates(request: Request, batch_id: str | None = None) -> dict:
    store = request.app.state.store
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    totals = store.load_totals(batch["batch_id"])
    return {
        "batch_id": batch["batch_id"],
        "batch_no": batch["batch_no"],
        "version": int(batch["version"]),
        "total_cents": int(totals.get("amount_total", 0)),
        "allocated_cents": int(totals.get("amount_allocated", 0)),
        "kept_cents": int(totals.get("amount_kept", 0)),
        "blocked_cents": int(totals.get("amount_blocked", 0)),
        "depts": store.load_depts(batch["batch_id"]),
        "gates": store.load_gates(batch["batch_id"]),
    }


@router.get("/api/batches")
def get_batches(request: Request) -> dict:
    store = request.app.state.store
    return {"batches": service.listing(store)}


@router.get("/gates")
def gates_page(request: Request, batch_id: str | None = None):
    store = request.app.state.store
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    totals = store.load_totals(batch["batch_id"])
    depts = store.load_depts(batch["batch_id"])
    gates = store.load_gates(batch["batch_id"])
    blocked = [row for row in gates if row["decision"] != "KEEP"]
    return web.render(request, "gates.html", page_key="gate", batch=batch,
                      depts=depts, gates=gates, blocked=blocked,
                      version=int(batch["version"]),
                      total_cents=int(totals.get("amount_total", 0)),
                      allocated_cents=int(totals.get("amount_allocated", 0)),
                      kept_cents=int(totals.get("amount_kept", 0)),
                      blocked_cents=int(totals.get("amount_blocked", 0)),
                      budgets={d["dept"]: d["budget_cents"] for d in depts})


@router.get("/batches")
def batches_page(request: Request):
    store = request.app.state.store
    rows = service.listing(store)
    current = store.latest_batch()
    return web.render(request, "batches.html", page_key="batches", batch=current,
                      batches=rows, verify=batchkey.verify_check)
