"""计费编排：一条原始行 → 归一字段 → 按时段分桶 → 依次作用规则 → 单查询成本。

成本拆成扫描部分、时长部分、并发加价三块，各自量化到分后合计再量化一次，
分摊与闸门只认最终的「分」，因此合计与份额之间不会有二进制误差。
"""
from __future__ import annotations

from decimal import Decimal

from app import config

from . import contract, coerce, units


def _value(fields: list[str], mapping: dict[str, int], name: str) -> str:
    index = mapping.get(name)
    if index is None or index >= len(fields):
        return ""
    return fields[index]


def coerce_row(fields: list[str], mapping: dict[str, int], *,
               row_ref: str, line_no: int, segment: int) -> dict:
    """字段归一；任何字段不合法都抛 coerce.CoerceError（带原因码）。"""
    raw = {name: _value(fields, mapping, name) for name in contract.COLUMN_NAMES}
    query_id = coerce.clean_text(raw["query_id"])
    if query_id == "":
        raise coerce.CoerceError("Q_MISSING_FIELD", "query_id", "")
    dept = coerce.clean_text(raw["dept"])
    if dept == "" or coerce.is_sentinel(dept):
        raise coerce.CoerceError("Q_BAD_DEPT", "dept", raw["dept"])
    project = coerce.clean_text(raw["project"]) or "未填"
    started_at = coerce.parse_instant(raw["started_at"], "started_at")
    duration_s = coerce.parse_decimal(raw["duration_s"], "duration_s")
    if duration_s is None:
        raise coerce.CoerceError("Q_SENTINEL", "duration_s", raw["duration_s"])
    if duration_s <= 0:
        raise coerce.CoerceError("Q_BAD_NUMBER", "duration_s", raw["duration_s"])
    scan_size = coerce.parse_decimal(raw["scan_size"], "scan_size")
    if scan_size is None:
        raise coerce.CoerceError("Q_SENTINEL", "scan_size", raw["scan_size"])
    if scan_size < 0:
        raise coerce.CoerceError("Q_BAD_NUMBER", "scan_size", raw["scan_size"])
    unit_raw = coerce.clean_text(raw["scan_unit"])
    if unit_raw == "" or coerce.is_sentinel(unit_raw):
        unit = "GB"
    else:
        try:
            unit = units.normalize_unit(unit_raw)
        except units.UnitError as exc:
            raise coerce.CoerceError("Q_BAD_UNIT", "scan_unit", unit_raw) from exc
    concurrency = coerce.parse_int(raw["concurrency"], "concurrency", default=1)
    if concurrency < 0:
        raise coerce.CoerceError("Q_BAD_NUMBER", "concurrency", raw["concurrency"])
    value_score = coerce.parse_decimal(raw["value_score"], "value_score",
                                       allow_sentinel=True)
    if value_score is None:
        value_score = Decimal(0)
    shared = coerce.parse_shared(raw["shared_with"])

    scan_bytes = units.to_bytes(scan_size, unit)
    scan_gb = units.to_gb(scan_size, unit)
    raw["scan_bytes"] = units.big_int_text(scan_bytes)
    flags: list[str] = []
    if abs(scan_bytes) > Decimal(2) ** 53:
        flags.append("Q_BIG_INT")
    if scan_gb == 0:
        flags.append("Q_ZERO_SCAN")
    return {
        "query_id": query_id,
        "dept": dept,
        "project": project,
        "started_at": started_at.isoformat(),
        "duration_s": duration_s,
        "scan_gb": scan_gb,
        "scan_unit": unit,
        "scan_raw": coerce.clean_text(raw["scan_size"]),
        "scan_bytes": scan_bytes,
        "concurrency": concurrency,
        "value_score": value_score,
        "shared": shared,
        "flags": flags,
        "row_ref": row_ref,
        "line_no": line_no,
        "segment": segment,
        "raw": raw,
    }


def dept_scan_totals(rows: list[dict]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for row in rows:
        totals[row["dept"]] = totals.get(row["dept"], Decimal(0)) + row["scan_gb"]
    return totals


def _money(value: Decimal) -> int:
    return coerce.to_cents(value)


def price_one(row: dict, *, volume_hit: bool) -> dict:
    """单查询成本；规则按 (priority, id) 顺序作用，与注册顺序无关。"""
    rules: list[str] = []
    started = coerce.parse_instant(row["started_at"])
    buckets = units.split_hours(started, row["duration_s"])
    hour_cost = Decimal(0)
    bucket_rows = []
    for bucket, hours in buckets:
        rate = units.bucket_rate(bucket)
        cost = (rate * hours).quantize(config.PRICE_DECIMALS)
        hour_cost += cost
        bucket_rows.append({
            "bucket": bucket.isoformat(),
            "hours": str(hours),
            "rate": str(rate),
            "cost": str(cost),
            "night": units.is_night(bucket),
        })
    scan_cost = (row["scan_gb"] * config.PRICE_PER_GB).quantize(config.PRICE_DECIMALS)
    if volume_hit:
        scan_cost = (scan_cost * config.VOLUME_FACTOR).quantize(config.PRICE_DECIMALS)
        rules.append("R_VOLUME_DISCOUNT")
    if any(item["night"] for item in bucket_rows):
        rules.append("R_NIGHT_RATE")
    total_hours = units.hours_total(row["duration_s"])
    addon = Decimal(0)
    if row["concurrency"] > config.FREE_CONCURRENCY:
        extra = row["concurrency"] - config.FREE_CONCURRENCY
        addon = (Decimal(extra) * total_hours * config.CONC_SURCHARGE_PER_HOUR
                 ).quantize(config.PRICE_DECIMALS)
        rules.append("R_CONC_SURCHARGE")
    total = scan_cost + hour_cost + addon
    if total < config.MIN_CHARGE:
        total = config.MIN_CHARGE
        rules.append("R_MIN_CHARGE")
    ordered = [rule["id"] for rule in contract.ordered_rules()
               if rule["id"] in rules]
    status = "ZERO_SCAN" if row["scan_gb"] == 0 else "BILLED"
    return {
        **row,
        "cost_cents": _money(total),
        "scan_cost_cents": _money(scan_cost),
        "hour_cost_cents": _money(hour_cost),
        "addon_cents": _money(addon),
        "buckets": bucket_rows,
        "rules": ordered,
        "status": status,
        "hours": total_hours,
    }


def price_rows(rows: list[dict]) -> list[dict]:
    totals = dept_scan_totals(rows)
    threshold = config.VOLUME_THRESHOLD_GB
    return [price_one(row, volume_hit=totals[row["dept"]] >= threshold)
            for row in rows]


def preview(fields: dict) -> dict:
    """试算入口：单条字段 → 成本明细，不落库、不参与对账。"""
    mapping = {name: index for index, name in enumerate(contract.COLUMN_NAMES)}
    values = [coerce.clean_text(fields.get(name, "")) for name in contract.COLUMN_NAMES]
    row = coerce_row(values, mapping, row_ref="试算", line_no=1, segment=1)
    return price_one(row, volume_hit=False)
