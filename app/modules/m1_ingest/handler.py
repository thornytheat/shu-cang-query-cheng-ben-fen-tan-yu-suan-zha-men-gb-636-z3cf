"""M1 账单接入入口：导入样例、看逐行诊断、看本次批次结论。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors, web
from app.modules.m2_billing import contract
from app.modules.m3_quality import service as quality_service

from . import service

router = APIRouter(tags=["ingest"])


@router.post("/api/import")
async def post_import(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 请求体不是 JSON
        raise errors.BadRequest("请求体必须是 JSON 对象", code="E_BAD_REQUEST")
    if not isinstance(body, dict):
        raise errors.BadRequest("请求体必须是 JSON 对象", code="E_BAD_REQUEST")
    source = body.get("source")
    if not source:
        raise errors.BadRequest("缺少 source", code="E_BAD_REQUEST")
    budget_source = body.get("budget_source")
    charset = body.get("charset")
    summary = service.run_import(
        request.app.state.store, request.app.state.settings,
        source=str(source), budget_source=budget_source,
        charset=str(charset) if charset else None)
    return summary


@router.get("/")
def index_page(request: Request, batch_id: str | None = None):
    store = request.app.state.store
    settings = request.app.state.settings
    batch = store.get_batch(batch_id) if batch_id else store.latest_batch()
    context = service.load_page_context(store, settings, batch)
    quality = (quality_service.load(store, batch["batch_id"]) if batch else
               {"issues": [], "distribution": [], "summary": {}, "row_account": {}})
    depts = store.load_depts(batch["batch_id"]) if batch else []
    full_distribution = quality["distribution"]
    bar_max = max([row["count"] for row in full_distribution] or [1])
    return web.render(
        request, "index.html", page_key="import", batch=batch,
        samples=service.list_samples(settings),
        default_source=settings.default_source,
        contract=contract.contract_payload(),
        totals=context["totals"], accounts=context["accounts"],
        counters=context.get("counters", {}),
        distribution=full_distribution[:6], bar_max=bar_max,
        quality_summary=quality["summary"],
        depts=depts[:8],
        empty_state=(batch or {}).get("note", ""))
