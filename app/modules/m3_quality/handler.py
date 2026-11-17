"""M3 异常查询入口：原因码分布、隔离行、隔离区页面。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors, web

from . import service

router = APIRouter(tags=["quality"])


def _current(request: Request, batch_id: str | None) -> dict:
    store = request.app.state.store
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    return batch


@router.get("/api/quarantine")
def get_quarantine(request: Request, batch_id: str | None = None,
                   code: str | None = None) -> dict:
    batch = _current(request, batch_id)
    payload = service.load(request.app.state.store, batch["batch_id"])
    if code:
        payload["issues"] = [row for row in payload["issues"] if row["code"] == code]
    return {
        "batch_id": batch["batch_id"],
        "batch_no": batch["batch_no"],
        "summary": payload["summary"],
        "distribution": payload["distribution"],
        "row_account": payload["row_account"],
        "issues": payload["issues"],
    }


@router.get("/quarantine")
def quarantine_page(request: Request, batch_id: str | None = None,
                    code: str | None = None):
    batch = _current(request, batch_id)
    payload = service.load(request.app.state.store, batch["batch_id"])
    issues = payload["issues"]
    if code:
        issues = [row for row in issues if row["code"] == code]
    return web.render(request, "quarantine.html", page_key="quarantine",
                      batch=batch, issues=issues,
                      distribution=payload["distribution"],
                      summary=payload["summary"],
                      row_account=payload["row_account"],
                      active_code=code or "")
