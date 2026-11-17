"""M5 预算闸门与批次：排序放行、预算端点、批次校验位、陈旧提交。"""
from __future__ import annotations

from decimal import Decimal

from app import config, manifest
from app.modules.m5_gate import batchkey, gate
from fixtures import expected as X


def _dept(store, batch_id: str, name: str) -> dict:
    return next(row for row in store.load_depts(batch_id) if row["dept"] == name)


def test_gate_keeps_highest_ratio_within_budget(dirty, store):
    dept = _dept(store, dirty["batch_id"], "数据平台")
    assert dept["budget_cents"] == X.DEPT_BUDGET_CENTS["数据平台"]
    assert dept["kept_cents"] == X.DEPT_KEPT_CENTS["数据平台"]
    assert dept["blocked_cents"] == X.DEPT_BLOCKED_CENTS["数据平台"]
    assert dept["gate_status"] == "TRUNCATE"


def test_gate_ranks_by_value_over_cost(dirty, store):
    rows = [row for row in store.load_gates(dirty["batch_id"])
            if row["dept"] == "数据平台"]
    assert [row["query_id"] for row in rows] == [
        "q1008", "q1015", "q1010", "q1001", "q1004"]
    assert [row["decision"] for row in rows] == [
        "KEEP", "KEEP", "KEEP", "TRUNCATE", "TRUNCATE"]
    assert rows[-1]["cumulative_cents"] == X.DEPT_KEPT_CENTS["数据平台"]


def test_gate_tie_ratio_falls_back_to_query_id():
    items = {"风控": [
        {"query_id": "b2", "cost_cents": 100, "value_score": Decimal(50)},
        {"query_id": "a1", "cost_cents": 200, "value_score": Decimal(100)},
    ]}
    result = gate.evaluate(items, {"风控": 1000})
    assert [row["query_id"] for row in result["gates"]] == ["a1", "b2"]


def test_department_without_budget_is_fully_blocked():
    items = {"平台组": [
        {"query_id": "x1", "cost_cents": 400, "value_score": Decimal(60)},
        {"query_id": "x2", "cost_cents": 120, "value_score": Decimal(20)},
    ]}
    result = gate.evaluate(items, {})
    dept = result["depts"][0]
    assert dept["gate_status"] == "NO_BUDGET"
    assert dept["kept_cents"] == 0
    assert dept["blocked_cents"] == 520
    assert [row["decision"] for row in result["gates"]] == ["NO_BUDGET", "NO_BUDGET"]
    assert gate.issues_for(result["depts"])[0]["code"] == "Q_GATE_NO_BUDGET"


def test_budget_boundary_keeps_exact_amount():
    items = {"营销": [
        {"query_id": "m1", "cost_cents": 120, "value_score": Decimal(40)},
        {"query_id": "m2", "cost_cents": 80, "value_score": Decimal(30)},
    ]}
    result = gate.evaluate(items, {"营销": 200})
    assert [row["decision"] for row in result["gates"]] == ["KEEP", "KEEP"]
    assert result["depts"][0]["kept_cents"] == 200
    assert result["depts"][0]["blocked_cents"] == 0


def test_batch_number_check_digit_and_tamper_detection():
    batch_id, no, digit = manifest.batch_id(config.Settings().eval_date, 7)
    assert no == "CS-20261115-0007"
    assert batch_id == f"{no}-{digit}"
    assert manifest.verify(no, digit) is True
    assert manifest.verify(no, str((int(digit) + 1) % 10)) is False
    assert batchkey.verify_check(no, digit) is True


def test_trap26_stale_version_is_rejected(client):
    """TRAP-26：旧版本的闸门提交必须被拒，不能覆盖新结论。"""
    import_resp = client.post("/api/import", json={"source": "bills_2026_11_dirty.csv"})
    assert import_resp.status_code == 200
    batch_id = import_resp.json()["batch_id"]
    first = client.post("/api/gates", json={
        "batch_id": batch_id, "expected_version": 1,
        "budgets": {"数据平台": "4.00", "风控": "50.00", "营销": "1.00"}})
    assert first.status_code == 200
    assert first.json()["version"] == 2
    stale = client.post("/api/gates", json={
        "batch_id": batch_id, "expected_version": 1,
        "budgets": {"数据平台": "4.00", "风控": "50.00", "营销": "1.00"}})
    assert stale.status_code == 409
    assert stale.json()["error"] == "Q_STALE_VERSION"
    assert stale.json()["detail"]["current_version"] == 2


def test_gate_recompute_keeps_dept_account_balanced(client):
    import_resp = client.post("/api/import", json={"source": "bills_2026_11_dirty.csv"})
    batch_id = import_resp.json()["batch_id"]
    response = client.post("/api/gates", json={
        "batch_id": batch_id, "expected_version": 1,
        "budgets": {"数据平台": "20.00", "风控": "50.00", "营销": "1.00"}})
    body = response.json()
    assert body["version"] == 2
    depts = {row["dept"]: row for row in body["depts"]}
    assert depts["数据平台"]["gate_status"] == "KEEP"
    assert depts["数据平台"]["kept_cents"] == X.DEPT_ALLOCATED_CENTS["数据平台"]
    kept = sum(row["kept_cents"] for row in body["depts"])
    blocked = sum(row["blocked_cents"] for row in body["depts"])
    allocated = sum(row["allocated_cents"] for row in body["depts"])
    assert kept + blocked == allocated


def test_dept_account_matches_gate_totals(dirty):
    totals = dirty["totals"]
    assert totals["amount_kept"] == X.KEPT_CENTS
    assert totals["amount_blocked"] == X.BLOCKED_CENTS
    assert (int(totals["amount_kept"]) + int(totals["amount_blocked"])
            == int(totals["amount_allocated"]))


def test_zero_budget_blocks_every_query():
    """预算区间 (0, budget] 的下界：预算为 0 时一条都放不进来，部门账仍相等。"""
    items = {"营销": [
        {"query_id": "m1", "cost_cents": 120, "value_score": Decimal(40)},
        {"query_id": "m2", "cost_cents": 80, "value_score": Decimal(30)},
    ]}
    result = gate.evaluate(items, {"营销": 0})
    dept = result["depts"][0]
    assert [row["decision"] for row in result["gates"]] == ["TRUNCATE", "TRUNCATE"]
    assert dept["kept_cents"] == 0
    assert dept["blocked_cents"] == 200
    assert dept["allocated_cents"] == dept["kept_cents"] + dept["blocked_cents"]
