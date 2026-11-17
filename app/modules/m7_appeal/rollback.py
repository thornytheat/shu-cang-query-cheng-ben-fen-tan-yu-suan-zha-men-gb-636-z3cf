"""回滚规则：目标检查、两份状态的差异、回滚后的账目核对。

纯计算：入参是两份状态（快照或库里当前状态）与已存在的快照版本号，
出参是部门/合计的差异与能不能回滚的判断；真正的写库在 service 与 store。

约定：回滚不是把历史改掉，而是按目标版本的状态写成一个新版本，版本号只增不减。
"""
from __future__ import annotations

from app import config
from app.modules.m4_alloc import invariant

# 版本差异只比金额账相关的合计项，计数类合计变了不算钱变。
MONEY_TOTALS = ("amount_total", "amount_allocated", "amount_unallocated",
                "amount_kept", "amount_blocked")


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def dept_map(state: dict) -> dict[str, dict]:
    return {str(row["dept"]): row for row in (state.get("depts") or [])}


def totals_map(state: dict) -> dict[str, str]:
    return {str(key): str(value) for key, value in (state.get("totals") or {}).items()}


def diff(left: dict, right: dict) -> dict:
    """left → right 的差异：部门分摊/保留的分级变化与合计变化，金额单位是分。"""
    before, after = dept_map(left), dept_map(right)
    depts = []
    for dept in sorted(set(before) | set(after)):
        old = before.get(dept) or {}
        new = after.get(dept) or {}
        row = {
            "dept": dept,
            "allocated_before": _int(old.get("allocated_cents")),
            "allocated_after": _int(new.get("allocated_cents")),
            "kept_before": _int(old.get("kept_cents")),
            "kept_after": _int(new.get("kept_cents")),
            "budget_after": new.get("budget_cents"),
            "status_after": new.get("gate_status", ""),
        }
        row["allocated_delta"] = row["allocated_after"] - row["allocated_before"]
        row["kept_delta"] = row["kept_after"] - row["kept_before"]
        if row["allocated_delta"] or row["kept_delta"]:
            depts.append(row)
    old_totals, new_totals = totals_map(left), totals_map(right)
    totals = []
    for key in [key for key in MONEY_TOTALS
                if key in old_totals or key in new_totals]:
        old_value = _int(old_totals.get(key))
        new_value = _int(new_totals.get(key))
        if old_value != new_value:
            totals.append({"key": key, "before": old_value, "after": new_value,
                           "delta": new_value - old_value})
    allocated_after = sum(_int(row.get("allocated_cents"))
                          for row in after.values())
    return {
        "depts": depts,
        "totals": totals,
        "moved_cents": sum(abs(row["allocated_delta"]) for row in depts),
        "allocated_delta": sum(row["allocated_delta"] for row in depts),
        "balanced": allocated_after == _int(new_totals.get("amount_allocated")),
    }


def check_target(current_version: int, to_version: int, available: list[int]) -> str:
    """返回原因码；空串表示这条回滚可以做。"""
    if int(to_version) == int(current_version):
        return "Q_ROLLBACK_SAME"
    if int(to_version) not in {int(item) for item in available}:
        return "Q_ROLLBACK_TARGET"
    return ""


def accounts(state: dict) -> list[dict]:
    """回滚后的金额账与部门账；行账归质量模块，这里不重复算。"""
    totals = totals_map(state)
    total = _int(totals.get("amount_total"))
    allocated = _int(totals.get("amount_allocated"))
    unallocated = _int(totals.get("amount_unallocated"))
    kept = _int(totals.get("amount_kept"))
    blocked = _int(totals.get("amount_blocked"))
    return [invariant.amount_account(total, allocated, unallocated),
            invariant.dept_account(allocated, kept, blocked)]


def kind_label(kind: str) -> str:
    return config.VERSION_KINDS.get(kind, kind or "—")
