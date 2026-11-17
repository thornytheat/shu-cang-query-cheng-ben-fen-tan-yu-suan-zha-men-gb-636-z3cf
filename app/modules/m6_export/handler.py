"""M6 导出入口：CSV / JSON / 收据，导出内容与页面读同一批数据。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app import errors, web

from . import service

router = APIRouter(tags=["export"])


def _current(request: Request, batch_id: str | None) -> dict:
    store = request.app.state.store
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    if batch is None:
        raise errors.NotFound("还没有批次，请先导入账单", code="Q_NO_BATCH")
    return batch


@router.get("/api/export")
def get_export(request: Request, kind: str = "bill", batch_id: str | None = None):
    batch = _current(request, batch_id)
    payload = service.build(request.app.state.store, request.app.state.settings,
                            batch, kind)
    headers = {"Content-Disposition": f'attachment; filename="{payload["filename"]}"',
               "X-Batch-No": batch["batch_no"],
               "X-Check-Digit": batch["check_digit"]}
    return PlainTextResponse(payload["text"], media_type=payload["media"],
                             headers=headers)


@router.get("/api/export/index")
def get_export_index(request: Request, batch_id: str | None = None) -> dict:
    batch = _current(request, batch_id)
    store = request.app.state.store
    rows = []
    for item in service.listing(store):
        payload = service.build(store, request.app.state.settings, batch, item["kind"])
        rows.append({**item, "filename": payload["filename"],
                     "bytes": len(payload["text"].encode("utf-8")),
                     "sha256": service.digest_of(payload["text"])[:16]})
    return {"batch_id": batch["batch_id"], "batch_no": batch["batch_no"],
            "check_digit": batch["check_digit"], "kinds": rows}


@router.get("/export")
def export_page(request: Request, batch_id: str | None = None):
    batch = _current(request, batch_id)
    store = request.app.state.store
    rows = []
    for item in service.listing(store):
        payload = service.build(store, request.app.state.settings, batch, item["kind"])
        rows.append({**item, "filename": payload["filename"],
                     "bytes": len(payload["text"].encode("utf-8")),
                     "sha256": service.digest_of(payload["text"])[:16]})
    counters = store.count_rows(batch["batch_id"])
    return web.render(request, "export.html", page_key="export", batch=batch,
                      kinds=rows, counts=counters,
                      totals=store.load_totals(batch["batch_id"]))
