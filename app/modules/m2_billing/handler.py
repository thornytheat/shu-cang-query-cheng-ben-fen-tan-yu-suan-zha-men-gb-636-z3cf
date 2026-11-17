"""M2 计费规则入口：列契约、单价与规则表、单条试算。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import errors

from . import contract, coerce, service

router = APIRouter(tags=["billing"])


@router.get("/api/contract")
def get_contract() -> dict:
    return contract.contract_payload()


@router.post("/api/billing/preview")
async def post_preview(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise errors.BadRequest("请求体必须是 JSON 对象", code="E_BAD_REQUEST")
    fields = body.get("row") if isinstance(body.get("row"), dict) else body
    if not isinstance(fields, dict) or not fields:
        raise errors.BadRequest("缺少字段 row", code="E_BAD_REQUEST")
    try:
        priced = service.preview(fields)
    except coerce.CoerceError as exc:
        raise errors.BadRequest(exc.message, code=exc.code,
                                detail={"field": exc.field, "raw": exc.raw})
    return {
        "query_id": priced["query_id"],
        "dept": priced["dept"],
        "scan_gb": str(priced["scan_gb"]),
        "scan_unit": priced["scan_unit"],
        "duration_s": str(priced["duration_s"]),
        "cost_cents": priced["cost_cents"],
        "scan_cost_cents": priced["scan_cost_cents"],
        "hour_cost_cents": priced["hour_cost_cents"],
        "addon_cents": priced["addon_cents"],
        "rules": priced["rules"],
        "buckets": priced["buckets"],
    }

