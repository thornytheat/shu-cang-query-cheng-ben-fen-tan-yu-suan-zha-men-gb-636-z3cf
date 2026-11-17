"""M4 编排：独占查询归部门，共享查询按权重分摊，权重全零的进未分摊。"""
from __future__ import annotations

from app import reasons

from . import invariant, remainder, weights


def allocate(priced: list[dict]) -> dict:
    shares: list[dict] = []
    dept_totals: dict[str, int] = {}
    query_costs: dict[str, int] = {}
    shares_by_query: dict[str, int] = {}
    issues: list[dict] = []
    unallocated_cents = 0
    shared_query_count = 0

    for row in priced:
        query_id = row["query_id"]
        cost = int(row["cost_cents"])
        query_costs[query_id] = query_costs.get(query_id, 0) + cost
        parts = weights.merged_parts(weights.parts_of(row))
        if not parts:
            shares.append({"query_id": query_id, "dept": row["dept"], "kind": "OWN",
                           "weight": weights.own_weight(row), "share_cents": cost})
            dept_totals[row["dept"]] = dept_totals.get(row["dept"], 0) + cost
            shares_by_query[query_id] = shares_by_query.get(query_id, 0) + cost
            continue
        shared_query_count += 1
        total_weight = weights.total_weight(parts)
        if total_weight <= 0:
            unallocated_cents += cost
            shares.append({"query_id": query_id, "dept": "未分摊", "kind": "UNALLOCATED",
                           "weight": total_weight, "share_cents": cost})
            shares_by_query[query_id] = shares_by_query.get(query_id, 0) + cost
            issues.append({
                "code": "Q_UNALLOCATED",
                "stage": reasons.stage("Q_UNALLOCATED"),
                "level": reasons.level("Q_UNALLOCATED"),
                "row_ref": row.get("row_ref"),
                "field": "shared_with",
                "raw_value": query_id,
                "detail": "参与部门权重全为 0，成本计入未分摊",
            })
            continue
        cents = remainder.largest_remainder(cost, [p["weight"] for p in parts])
        if not remainder.check_conserved(cents, cost):
            raise AssertionError(f"{query_id} 分摊后合计不等于成本")
        for part, share in zip(parts, cents):
            shares.append({"query_id": query_id, "dept": part["dept"],
                           "kind": "SHARED", "weight": part["weight"],
                           "share_cents": share})
            dept_totals[part["dept"]] = dept_totals.get(part["dept"], 0) + share
            shares_by_query[query_id] = shares_by_query.get(query_id, 0) + share

    allocated_cents = sum(dept_totals.values())
    total_cents = sum(query_costs.values())
    return {
        "shares": shares,
        "dept_totals": dept_totals,
        "query_costs": query_costs,
        "shares_by_query": shares_by_query,
        "issues": issues,
        "unallocated_cents": unallocated_cents,
        "allocated_cents": allocated_cents,
        "total_cents": total_cents,
        "shared_query_count": shared_query_count,
        "amount_account": invariant.amount_account(
            total_cents, allocated_cents, unallocated_cents),
        "residual_account": invariant.residual_account(query_costs, shares_by_query),
    }


def dept_rows(dept_totals: dict[str, int], dept_scan: dict, budgets: dict) -> list[dict]:
    rows = []
    for dept, cents in dept_totals.items():
        budget = budgets.get(dept)
        rows.append({
            "dept": dept,
            "allocated_cents": cents,
            "budget_cents": None if budget is None else int(budget),
            "scan_gb": dept_scan.get(dept, 0),
        })
    rows.sort(key=lambda item: (-item["allocated_cents"], item["dept"]))
    return rows


def apply_gate(dept_rows_in: list[dict], gate_depts: list[dict]) -> list[dict]:
    """把闸门结论并进部门行；闸门没提到这个部门时按无预算整体截断。

    导入与申诉重算都走这里，两处的部门账因此只有一套做法。
    """
    decisions = {row["dept"]: row for row in gate_depts}
    merged = []
    for dept in dept_rows_in:
        row = dict(dept)
        decision = decisions.get(row["dept"])
        if decision is None:
            row.update(kept_cents=0, blocked_cents=int(row["allocated_cents"]),
                       gate_status="NO_BUDGET")
        else:
            row.update(kept_cents=int(decision["kept_cents"]),
                       blocked_cents=int(decision["blocked_cents"]),
                       gate_status=decision["gate_status"])
        merged.append(row)
    return merged
