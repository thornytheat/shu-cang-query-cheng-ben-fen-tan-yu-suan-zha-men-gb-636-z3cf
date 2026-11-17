"""对账四账：行账、金额账、部门账、尾差账。

每一条账都返回左值、右值与是否相等，页面与导出直接展示，差额非 0 会显式「不平」。
"""
from __future__ import annotations

from decimal import Decimal


def account(key: str, left, right, note: str = "") -> dict:
    return {"key": key, "left": left, "right": right,
            "ok": left == right, "diff": (left - right) if isinstance(left, int) else None,
            "note": note}


def row_account(counters: dict) -> dict:
    left = counters["rows_data"]
    right = (counters["rows_valid"] + counters["rows_dedup"]
             + counters["rows_quarantine"] + counters["rows_empty"])
    return account("行账", left, right, "数据行 = 有效 + 去重 + 隔离 + 空行")


def amount_account(total_cents: int, allocated_cents: int,
                   unallocated_cents: int) -> dict:
    return account("金额账", total_cents, allocated_cents + unallocated_cents,
                   "总额 = 分摊合计 + 未分摊")


def dept_account(allocated_cents: int, kept_cents: int, blocked_cents: int) -> dict:
    return account("部门账", allocated_cents, kept_cents + blocked_cents,
                   "分摊合计 = 保留合计 + 截断合计")


def residual_account(query_costs: dict[str, int],
                     shares_by_query: dict[str, int]) -> dict:
    worst = 0
    for query_id, cents in query_costs.items():
        diff = shares_by_query.get(query_id, 0) - cents
        if abs(diff) > abs(worst):
            worst = diff
    return account("尾差账", 0, worst, "每条共享查询的份额合计 = 该查询成本")


def all_accounts(*, counters: dict, total_cents: int, allocated_cents: int,
                 unallocated_cents: int, kept_cents: int, blocked_cents: int,
                 query_costs: dict[str, int],
                 shares_by_query: dict[str, int]) -> list[dict]:
    return [
        row_account(counters),
        amount_account(total_cents, allocated_cents, unallocated_cents),
        dept_account(allocated_cents, kept_cents, blocked_cents),
        residual_account(query_costs, shares_by_query),
    ]


def all_balanced(accounts: list[dict]) -> bool:
    return all(item["ok"] for item in accounts)


def share_total(values) -> int:
    return sum(int(v) for v in values)


def grams(value) -> Decimal:
    return Decimal(str(value))
