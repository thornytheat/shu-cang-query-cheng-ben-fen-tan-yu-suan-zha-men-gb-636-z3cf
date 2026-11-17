"""M7 申诉与回滚入口：提交申诉、看重算差异、按版本回滚、看版本时间线。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors, web

from . import service

router = APIRouter(tags=["appeal"])


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 请求体不是 JSON
        raise errors.BadRequest("请求体必须是 JSON 对象", code="E_BAD_REQUEST")
    if not isinstance(body, dict) or not body:
        raise errors.BadRequest("请求体必须是 JSON 对象", code="E_BAD_REQUEST")
    return body


def _int_field(body: dict, name: str, *, required: bool = False) -> int | None:
    raw = body.get(name)
    if raw is None or raw == "":
        if required:
            raise errors.BadRequest(f"缺少 {name}", code="E_BAD_REQUEST")
        return None
    if isinstance(raw, bool):
        raise errors.BadRequest(f"{name} 必须是整数", code="E_BAD_REQUEST")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise errors.BadRequest(f"{name} 必须是整数", code="E_BAD_REQUEST")


@router.get("/api/appeals")
def get_appeals(request: Request, batch_id: str | None = None,
                left: int | None = None, right: int | None = None) -> dict:
    return service.payload(request.app.state.store, batch_id=batch_id,
                           left_version=left, right_version=right)


@router.post("/api/appeals")
async def post_appeals(request: Request) -> dict:
    body = await _body(request)
    entries = body.get("appeals")
    if entries is None:
        raise errors.BadRequest("缺少 appeals", code="E_BAD_REQUEST")
    result = service.apply(request.app.state.store, request.app.state.settings,
                           batch_id=body.get("batch_id"), entries=entries,
                           expected_version=_int_field(body, "expected_version"))
    return result


@router.post("/api/appeals/rollback")
async def post_rollback(request: Request) -> dict:
    body = await _body(request)
    to_version = _int_field(body, "to_version", required=True)
    return service.rollback_to(request.app.state.store, batch_id=body.get("batch_id"),
                               to_version=to_version,
                               expected_version=_int_field(body, "expected_version"))


@router.get("/appeals")
def appeals_page(request: Request, batch_id: str | None = None,
                 left: int | None = None, right: int | None = None):
    data = service.page_context(request.app.state.store,
                                request.app.state.settings, batch_id=batch_id,
                                left_version=left, right_version=right)
    return web.render(request, "appeals.html", page_key="appeal", **data)
