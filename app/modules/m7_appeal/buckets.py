"""申诉重算的时段复核：把改判后的时间再按小时桶切一遍，与计费结果对账。

为什么要再切一遍：改判会改开始时间或时长，跨零点的查询一旦整段按起始时段单价
计价，夜间那几小时就少算了。这里保留一条独立的分桶路径（左闭右开、按本地小时
判时段），只用来交叉核对计费的时长合计与金额；两处对不上就不落地申诉。

时段边界含起点、不含终点：本地 22:00 起按夜价，06:00 起按日价。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config
from app.modules.m2_billing import coerce, units

DECIMALS = config.PRICE_DECIMALS
NIGHT = "night"
DAY = "day"


def phase_of(local_hour: int) -> str:
    night = local_hour >= config.NIGHT_START_HOUR or local_hour < config.NIGHT_END_HOUR
    return NIGHT if night else DAY


def rate_of(phase: str) -> Decimal:
    return config.NIGHT_RATE_PER_HOUR if phase == NIGHT else config.DAY_RATE_PER_HOUR


def rebucket(started_at, duration_s) -> list[dict]:
    """切分结果每项：桶起点(UTC) + 本地时段 + 小时数 + 单价 + 金额。"""
    start = started_at if isinstance(started_at, dt.datetime) else coerce.parse_instant(
        started_at)
    duration = Decimal(str(duration_s))
    if duration <= 0:
        return []
    start = start.astimezone(config.UTC)
    bucket = units.hour_floor(start)
    offset = Decimal(str(int((start - bucket).total_seconds())))
    remaining = duration
    first = True
    rows: list[dict] = []
    while remaining > 0:
        capacity = Decimal(3600) - (offset if first else Decimal(0))
        take = capacity if capacity < remaining else remaining
        hours = (take / Decimal(3600)).quantize(DECIMALS)
        local = bucket.astimezone(config.CHINA_TZ)
        phase = phase_of(units.local_hour_of(bucket))
        rate = rate_of(phase)
        rows.append({
            "bucket": bucket.isoformat(),
            "local": local.strftime("%m-%d %H:%M"),
            "local_hour": local.hour,
            "hours": hours,
            "phase": phase,
            "rate": rate,
            "cost": (rate * hours).quantize(DECIMALS),
        })
        remaining -= take
        bucket = bucket + dt.timedelta(hours=1)
        first = False
    return rows


def hours_sum(rows: list[dict]) -> Decimal:
    return sum((row["hours"] for row in rows), Decimal(0))


def cost_sum(rows: list[dict]) -> Decimal:
    return sum((row["cost"] for row in rows), Decimal(0))


def conserved(rows: list[dict], duration_s) -> bool:
    """桶内小时合计必须等于换算后的时长：漏一秒都算漏算。"""
    return hours_sum(rows) == units.hours_total(duration_s)


def crosses_day(rows: list[dict]) -> bool:
    """跨本地零点：夜里那两个小时桶分属两个日期。"""
    days = {dt.datetime.fromisoformat(row["bucket"]).astimezone(
        config.CHINA_TZ).date() for row in rows}
    return len(days) > 1


def boundary_marks(rows: list[dict]) -> list[str]:
    """落在两个时段边界上的桶起点，页面据此说明端点归属。"""
    marks = []
    for row in rows:
        if row["local_hour"] == config.NIGHT_START_HOUR:
            marks.append(f"{config.NIGHT_START_HOUR:02d}:00 起夜价")
        elif row["local_hour"] == config.NIGHT_END_HOUR:
            marks.append(f"{config.NIGHT_END_HOUR:02d}:00 起日价")
    return marks


def reconcile(rows: list[dict], duration_s, hour_cost_cents: int) -> dict:
    """与计费模块对账：时长合计 + 时长金额都要一致。"""
    expected = coerce.to_cents(cost_sum(rows))
    return {
        "ok": conserved(rows, duration_s) and expected == int(hour_cost_cents),
        "hours_ok": conserved(rows, duration_s),
        "hours": str(hours_sum(rows)),
        "cents_ok": expected == int(hour_cost_cents),
        "cents": expected,
        "billed_cents": int(hour_cost_cents),
        "crosses_day": crosses_day(rows),
        "marks": boundary_marks(rows),
    }
