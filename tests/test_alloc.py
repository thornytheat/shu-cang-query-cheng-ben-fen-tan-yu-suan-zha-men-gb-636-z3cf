"""M4 分摊：权重、最大余额法、未分摊与四账中的金额账。"""
from __future__ import annotations

from decimal import Decimal

from app.modules.m4_alloc import invariant, remainder, service, weights
from fixtures import expected as X


def _shares(store, batch_id: str) -> list[dict]:
    return store.load_shares(batch_id)


def test_own_query_lands_on_its_department(dirty, store):
    rows = [row for row in _shares(store, dirty["batch_id"])
            if row["query_id"] == "q1001"]
    assert len(rows) == 1
    assert rows[0]["dept"] == "数据平台"
    assert rows[0]["kind"] == "OWN"
    assert rows[0]["share_cents"] == X.COST_CENTS["q1001"]


def test_shared_query_weighted_by_hours_times_scan(dirty, store):
    rows = {row["dept"]: row for row in _shares(store, dirty["batch_id"])
            if row["query_id"] == "q1010"}
    assert rows["数据平台"]["weight"] == Decimal("30.000000")
    assert rows["风控"]["weight"] == Decimal("20.000000")
    assert rows["数据平台"]["share_cents"] == X.SHARE_Q1010["数据平台"]
    assert rows["风控"]["share_cents"] == X.SHARE_Q1010["风控"]


def test_trap24_largest_remainder_conserves_query_cost(dirty, store):
    """TRAP-24：份额按最大余额法分配，分完仍等于该查询成本。"""
    shares = [row for row in _shares(store, dirty["batch_id"])
              if row["query_id"] == "q1010"]
    assert sum(row["share_cents"] for row in shares) == X.COST_CENTS["q1010"]
    assert remainder.check_conserved([row["share_cents"] for row in shares],
                                     X.COST_CENTS["q1010"]) is True


def test_trap24_naive_rounding_breaks_conservation():
    """TRAP-24 的反例：逐份四舍五入合计只有 99 分，最大余额法给出 100 分。"""
    naive = [int((Decimal(100) / 3).to_integral_value(rounding="ROUND_HALF_UP"))] * 3
    assert sum(naive) == 99
    exact = remainder.largest_remainder(100, [Decimal(1), Decimal(1), Decimal(1)])
    assert sum(exact) == 100
    assert exact == [34, 33, 33]


def test_remainder_tie_is_broken_by_input_order():
    tied = remainder.largest_remainder(301, [Decimal("30"), Decimal("20")])
    assert tied == [181, 120]
    assert remainder.largest_remainder(301, [Decimal("20"), Decimal("30")]) == [120, 181]
    with_parts = weights.merged_parts([
        {"dept": "风控", "hours": Decimal("0.5"), "scan_gb": Decimal("40"),
         "weight": Decimal("20")},
        {"dept": "数据平台", "hours": Decimal("0.5"), "scan_gb": Decimal("60"),
         "weight": Decimal("30")},
    ])
    assert [part["dept"] for part in with_parts] == ["数据平台", "风控"]


def test_zero_weight_query_goes_to_unallocated(dirty, store):
    rows = [row for row in _shares(store, dirty["batch_id"])
            if row["query_id"] == "q1011"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "UNALLOCATED"
    assert rows[0]["dept"] == "未分摊"
    assert rows[0]["share_cents"] == X.UNALLOCATED_CENTS
    codes = [row["code"] for row in store.load_quarantine(dirty["batch_id"])]
    assert "Q_UNALLOCATED" in codes


def test_amount_account_equals_allocated_plus_unallocated(dirty):
    totals = dirty["totals"]
    assert totals["amount_allocated"] == X.ALLOCATED_CENTS
    assert totals["amount_unallocated"] == X.UNALLOCATED_CENTS
    account = invariant.amount_account(int(totals["amount_total"]),
                                       int(totals["amount_allocated"]),
                                       int(totals["amount_unallocated"]))
    assert account["ok"] is True
    assert account["diff"] == 0


def test_department_totals_match_hand_computed_values(dirty, store):
    depts = {row["dept"]: row for row in store.load_depts(dirty["batch_id"])}
    assert {name: row["allocated_cents"] for name, row in depts.items()} == \
        X.DEPT_ALLOCATED_CENTS
    assert sum(row["allocated_cents"] for row in depts.values()) == X.ALLOCATED_CENTS


def test_every_query_shares_sum_to_its_cost(dirty, store):
    shares = _shares(store, dirty["batch_id"])
    per_query: dict[str, int] = {}
    for row in shares:
        per_query[row["query_id"]] = per_query.get(row["query_id"], 0) + row["share_cents"]
    for query_id, cents in X.COST_CENTS.items():
        assert per_query[query_id] == cents
    account = invariant.residual_account(X.COST_CENTS, per_query)
    assert account["ok"] is True


def test_allocation_is_independent_of_row_order():
    rows = [
        {"query_id": "s1", "dept": "数据平台", "cost_cents": 301, "duration_s": "1800",
         "scan_gb": Decimal("100"), "shared": [
             {"dept": "风控", "hours": Decimal("0.5"), "scan_gb": Decimal("40")},
             {"dept": "数据平台", "hours": Decimal("0.5"), "scan_gb": Decimal("60")}]},
        {"query_id": "s2", "dept": "营销", "cost_cents": 133, "duration_s": "600",
         "scan_gb": Decimal("50"), "shared": []},
    ]
    first = service.allocate(rows)
    second = service.allocate(list(reversed(rows)))
    assert first["dept_totals"] == second["dept_totals"]
    assert sorted((row["query_id"], row["dept"], row["share_cents"])
                  for row in first["shares"]) == \
        sorted((row["query_id"], row["dept"], row["share_cents"])
               for row in second["shares"])
    assert first["total_cents"] == 434


def test_partial_zero_weight_gets_zero_share_and_total_holds():
    """同一共享查询里某部门权重为 0：份额是 0，但仍占一行，合计不变。"""
    assert remainder.largest_remainder(301, [Decimal(0), Decimal("20")]) == [0, 301]
    rows = [{"query_id": "s3", "dept": "数据平台", "cost_cents": 301,
             "duration_s": "1800", "scan_gb": Decimal("100"),
             "shared": [
                 {"dept": "风控", "hours": Decimal("0"), "scan_gb": Decimal("40")},
                 {"dept": "营销", "hours": Decimal("0.5"), "scan_gb": Decimal("60")}]}]
    result = service.allocate(rows)
    shares = {row["dept"]: row["share_cents"] for row in result["shares"]}
    assert shares == {"风控": 0, "营销": 301}
    assert sum(shares.values()) == 301
    assert result["unallocated_cents"] == 0
