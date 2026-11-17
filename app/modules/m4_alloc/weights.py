"""共享查询分摊权重：部门时长(小时) × 部门扫描量(GB)。

权重只取决于参与部门的用量，与部门书写顺序无关；出参按部门名排序，
保证同样输入在任意行序下得到同一份份额表。
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m2_billing import units


def weight_of(hours, scan_gb) -> Decimal:
    return (Decimal(str(hours)) * Decimal(str(scan_gb))).quantize(Decimal("0.000001"))


def parts_of(row: dict) -> list[dict]:
    parts = []
    for part in row.get("shared") or []:
        parts.append({
            "dept": part["dept"],
            "hours": Decimal(str(part["hours"])),
            "scan_gb": Decimal(str(part["scan_gb"])),
            "weight": weight_of(part["hours"], part["scan_gb"]),
        })
    parts.sort(key=lambda item: item["dept"])
    return parts


def own_weight(row: dict) -> Decimal:
    return weight_of(units.hours_total(row["duration_s"]), row["scan_gb"])


def total_weight(parts: list[dict]) -> Decimal:
    total = Decimal(0)
    for part in parts:
        total += part["weight"]
    return total


def merged_parts(parts: list[dict]) -> list[dict]:
    """同一部门写了两条权重时合并，避免同一部门出现两条份额。"""
    merged: dict[str, dict] = {}
    for part in parts:
        current = merged.get(part["dept"])
        if current is None:
            merged[part["dept"]] = dict(part)
            continue
        current["hours"] += part["hours"]
        current["scan_gb"] += part["scan_gb"]
        current["weight"] = weight_of(current["hours"], current["scan_gb"])
    return [merged[key] for key in sorted(merged)]
