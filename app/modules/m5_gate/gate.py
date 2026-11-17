"""预算闸门：按 收益分/成本 比降序放行，累计不超过部门预算，其余截断。

- 预算区间为 `(0, budget]`：累计花费恰好等于预算时仍保留；
- 排序键 (ratio 降序, query_id 升序)，比价相同不依赖字典顺序；
- 部门缺预算 → 全部 NO_BUDGET，其成本计入截断侧，部门账仍然相等。
"""
from __future__ import annotations

from decimal import Decimal

from app import config

ZERO = Decimal(0)


def ratio_of(value_score, cost_cents: int) -> Decimal:
    if cost_cents <= 0:
        return Decimal(1000)
    return (Decimal(str(value_score)) * 100 / Decimal(int(cost_cents))
            ).quantize(Decimal("0.0001"))


def rank(items: list[dict]) -> list[dict]:
    enriched = []
    for item in items:
        enriched.append({**item, "ratio": ratio_of(item["value_score"],
                                                   item["cost_cents"])})
    enriched.sort(key=lambda item: (-item["ratio"], item["query_id"]))
    return enriched


def evaluate(items_by_dept: dict[str, list[dict]],
             budgets: dict[str, int]) -> dict:
    depts: list[dict] = []
    gates: list[dict] = []
    kept_total = 0
    blocked_total = 0
    for dept in sorted(items_by_dept):
        items = items_by_dept[dept]
        allocated = sum(int(item["cost_cents"]) for item in items)
        budget = budgets.get(dept)
        if budget is None:
            for index, item in enumerate(rank(items), start=1):
                gates.append({
                    "dept": dept, "query_id": item["query_id"],
                    "value_score": item["value_score"], "ratio": item["ratio"],
                    "cost_cents": int(item["cost_cents"]),
                    "cumulative_cents": allocated, "decision": "NO_BUDGET",
                    "rank_no": index,
                })
            depts.append({"dept": dept, "budget_cents": None,
                          "allocated_cents": allocated, "kept_cents": 0,
                          "blocked_cents": allocated, "gate_status": "NO_BUDGET"})
            blocked_total += allocated
            continue
        budget = int(budget)
        cumulative = 0
        kept = 0
        blocked = 0
        for index, item in enumerate(rank(items), start=1):
            cost = int(item["cost_cents"])
            if cumulative + cost <= budget:
                cumulative += cost
                kept += cost
                decision = "KEEP"
            else:
                blocked += cost
                decision = "TRUNCATE"
            gates.append({
                "dept": dept, "query_id": item["query_id"],
                "value_score": item["value_score"], "ratio": item["ratio"],
                "cost_cents": cost, "cumulative_cents": cumulative,
                "decision": decision, "rank_no": index,
            })
        status = "TRUNCATE" if blocked else "KEEP"
        depts.append({"dept": dept, "budget_cents": budget,
                      "allocated_cents": allocated, "kept_cents": kept,
                      "blocked_cents": blocked, "gate_status": status})
        kept_total += kept
        blocked_total += blocked
    depts.sort(key=lambda item: (-item["allocated_cents"], item["dept"]))
    return {
        "depts": depts, "gates": gates,
        "kept_cents": kept_total, "blocked_cents": blocked_total,
        "limits": {"free_concurrency": config.FREE_CONCURRENCY},
    }


def issues_for(depts: list[dict]) -> list[dict]:
    from app import reasons
    out = []
    for dept in depts:
        if dept["gate_status"] == "NO_BUDGET":
            out.append({
                "code": "Q_GATE_NO_BUDGET", "stage": reasons.stage("Q_GATE_NO_BUDGET"),
                "level": reasons.level("Q_GATE_NO_BUDGET"), "row_ref": "",
                "field": "dept", "raw_value": dept["dept"],
                "detail": "预算表里没有该部门，按截断处理",
            })
        elif dept["blocked_cents"] > 0:
            out.append({
                "code": "Q_GATE_TRUNCATED", "stage": reasons.stage("Q_GATE_TRUNCATED"),
                "level": reasons.level("Q_GATE_TRUNCATED"), "row_ref": "",
                "field": "budget", "raw_value": str(dept["budget_cents"]),
                "detail": f"{dept['dept']} 截断 {dept['blocked_cents']} 分",
            })
    return out
