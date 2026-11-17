"""模板渲染与共享视图上下文：过滤器、导航、当前批次。

页面只读 store，不做业务计算；金额按「分 → 元」一次性格式化，避免每页各写一套。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config, reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

NAV = (
    ("/", "账单接入", "import"),
    ("/allocations", "分摊结果", "alloc"),
    ("/gates", "闸门决策", "gate"),
    ("/appeals", "申诉回滚", "appeal"),
    ("/quarantine", "异常查询", "quarantine"),
    ("/batches", "批次清单", "batches"),
    ("/export", "导出", "export"),
)

STATUS_LABELS = {
    "done": "完成",
    "failed": "失败",
    "running": "运行中",
    "BILLED": "计费",
    "ZERO_SCAN": "零扫描",
    "UNALLOCATED": "未分摊",
    "KEEP": "保留",
    "TRUNCATE": "截断",
    "NO_BUDGET": "无预算",
    "OWN": "独占",
    "SHARED": "分摊",
    **config.APPEAL_STATUS,
    **config.VERSION_KINDS,
}


def yuan(cents) -> str:
    """整数分 → 千分位两位小数字符串。"""
    if cents is None or cents == "":
        return "—"
    try:
        value = Decimal(int(cents)) / Decimal(100)
    except (TypeError, ValueError):
        return "—"
    return f"{value:,.2f}"


def gb(value) -> str:
    if value is None or value == "":
        return "—"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{value:,.2f}"


def ratio(value) -> str:
    if value is None or value == "":
        return "—"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{value:.2f}"


def score(value) -> str:
    if value is None or value == "":
        return "—"
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return f"{value:.0f}"


def pct(part, whole) -> str:
    part, whole = int(part or 0), int(whole or 0)
    if whole <= 0:
        return "—"
    return f"{part * 100 / whole:.1f}%"


def to_local(value) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.UTC)
    return value.astimezone(config.CHINA_TZ)


def local_dt(value) -> str:
    local = to_local(value)
    return local.strftime("%Y-%m-%d %H:%M") if local else "—"


def local_hour(value) -> str:
    local = to_local(value)
    return local.strftime("%m-%d %H:%M") if local else "—"


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CHINA_TZ)


@dataclass
class Ctx:
    settings: config.Settings
    store: object

    def current_batch(self, batch_id: str | None = None) -> dict | None:
        if batch_id:
            return self.store.get_batch(batch_id)
        return self.store.latest_batch()


def setup(app) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = templates.env
    env.filters["yuan"] = yuan
    env.filters["gb"] = gb
    env.filters["ratio"] = ratio
    env.filters["score"] = score
    env.filters["localdt"] = local_dt
    env.filters["localhour"] = local_hour
    env.filters["pct"] = pct
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s or "—")
    env.filters["reason"] = reasons.label
    env.filters["level"] = lambda s: reasons.LEVEL_LABELS.get(s, s or "—")
    env.filters["dt_iso"] = lambda s: (s or "").replace("T", " ")[:19]
    env.globals.update(
        nav=NAV,
        stage_flow=config.STAGE_FLOW,
        stage_labels=config.STAGE_LABELS,
        stage_keys=config.STAGES,
        reason_catalog=reasons.catalog(),
        reason_label=reasons.label,
        money_unit=config.MONEY_UNIT,
        gate_decisions=config.GATE_DECISIONS,
        generated_at=now_local().strftime("%Y-%m-%d %H:%M:%S"),
        timezone_label="Asia/Shanghai (+08:00)",
    )
    app.state.templates = templates


def render(request: Request, name: str, **context):
    ctx: Ctx = request.app.state.ctx
    batch = context.pop("batch", None)
    if batch is None and "batch_id" in context:
        batch = ctx.current_batch(context.get("batch_id"))
    if batch is None and "batch" not in context:
        batch = ctx.current_batch()
    base = {
        "request": request,
        "ctx": ctx,
        "settings": ctx.settings,
        "batch": batch,
        "rule_version": ctx.settings.rule_version,
        "eval_date": ctx.settings.eval_date.isoformat(),
        "print_ts": now_local().strftime("%Y-%m-%d %H:%M"),
        "page_key": context.pop("page_key", ""),
    }
    if batch is not None:
        base["stages"] = ctx.store.load_stages(batch["batch_id"])
        base["totals"] = ctx.store.load_totals(batch["batch_id"])
    else:
        base["stages"] = []
        base["totals"] = {}
    base.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=base)


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx
