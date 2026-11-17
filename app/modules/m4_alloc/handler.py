"""M4 分摊入口：按部门看份额与依据，附四账中的金额账与尾差账。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors, web

from . import service

router = APIRouter(tags=["alloc"])


def _current(request: Request, batch_id: str | None) -> dict:
    store = request.app.state.store
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    return batch


def _payload(store, batch_id: str) -> dict:
    shares = store.load_shares(batch_id)
    depts = store.load_depts(batch_id)
    totals = store.load_totals(batch_id)
    return {"shares": shares, "depts": depts, "totals": totals}


@router.get("/api/allocations")
def get_allocations(request: Request, batch_id: str | None = None) -> dict:
    batch = _current(request, batch_id)
    payload = _payload(request.app.state.store, batch["batch_id"])
    totals = payload["totals"]
    return {
        "batch_id": batch["batch_id"],
        "batch_no": batch["batch_no"],
        "check_digit": batch["check_digit"],
        "total_cents": int(totals.get("amount_total", 0)),
        "allocated_cents": int(totals.get("amount_allocated", 0)),
        "unallocated_cents": int(totals.get("amount_unallocated", 0)),
        "shared_query_count": int(totals.get("shared_query_count", 0)),
        "depts": payload["depts"],
        "shares": payload["shares"],
        "amount_account": {
            "left": int(totals.get("amount_total", 0)),
            "right": int(totals.get("amount_allocated", 0))
            + int(totals.get("amount_unallocated", 0)),
            "ok": int(totals.get("amount_total", 0))
            == int(totals.get("amount_allocated", 0))
            + int(totals.get("amount_unallocated", 0)),
        },
    }


@router.get("/allocations")
def allocations_page(request: Request, batch_id: str | None = None):
    batch = _current(request, batch_id)
    payload = _payload(request.app.state.store, batch["batch_id"])
    totals = payload["totals"]
    dept_rows = payload["depts"]
    unallocated = [s for s in payload["shares"] if s["kind"] == "UNALLOCATED"]
    shared = [s for s in payload["shares"] if s["kind"] == "SHARED"]
    return web.render(request, "allocations.html", page_key="alloc",
                      batch=batch, depts=dept_rows, shares=payload["shares"],
                      shared=shared, unallocated=unallocated,
                      total_cents=int(totals.get("amount_total", 0)),
                      allocated_cents=int(totals.get("amount_allocated", 0)),
                      unallocated_cents=int(totals.get("amount_unallocated", 0)),
                      shared_query_count=int(totals.get("shared_query_count", 0)))
