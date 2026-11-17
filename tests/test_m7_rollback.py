"""M7 回滚：按版本快照回到旧结论、差异对比、版本只增不减与失败输入。"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from app.modules.m7_appeal import rollback
from fixtures import expected as X


def _import(client) -> str:
    response = client.post("/api/import", json={"source": "bills_2026_11_dirty.csv"})
    assert response.status_code == 200
    return response.json()["batch_id"]


def _appeal(client, batch_id: str, target: str = "营销", expected_version: int = 1):
    response = client.post("/api/appeals", json={
        "batch_id": batch_id, "expected_version": expected_version,
        "appeals": [{"query_id": "q1001", "target_dept": target}]})
    assert response.status_code == 200
    return response.json()


def _rollback(client, batch_id: str, to_version: int, expected_version: int):
    return client.post("/api/appeals/rollback", json={
        "batch_id": batch_id, "to_version": to_version,
        "expected_version": expected_version})


def _depts(client) -> dict[str, int]:
    return {row["dept"]: row["allocated_cents"]
            for row in client.get("/api/allocations").json()["depts"]}


def test_rollback_restores_previous_version_state(client):
    batch_id = _import(client)
    _appeal(client, batch_id)
    assert _depts(client)["数据平台"] == (
        X.DEPT_ALLOCATED_CENTS["数据平台"] - X.COST_CENTS["q1001"])
    restored = _rollback(client, batch_id, to_version=1, expected_version=2)
    assert restored.status_code == 200
    body = restored.json()
    assert body["version"] == 3
    assert body["restored_from"] == 1
    assert _depts(client) == X.DEPT_ALLOCATED_CENTS
    gates = client.get("/api/gates").json()
    assert gates["total_cents"] == X.TOTAL_CENTS
    assert gates["kept_cents"] == X.KEPT_CENTS
    assert gates["blocked_cents"] == X.BLOCKED_CENTS


def test_rollback_restores_bill_costs(client):
    batch_id = _import(client)
    _appeal(client, batch_id)
    assert _rollback(client, batch_id, 1, 2).status_code == 200
    exported = client.get(f"/api/export?kind=bill&batch_id={batch_id}")
    rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
    total = sum(Decimal(row["cost_yuan"]) for row in rows)
    assert total == Decimal(X.TOTAL_CENTS) / 100
    bills = {row["query_id"]: row for row in rows}
    assert bills["q1001"]["dept"] == "数据平台"
    assert bills["q1001"]["cost_yuan"] == "3.58"


def test_rollback_to_current_version_is_rejected(client):
    batch_id = _import(client)
    response = _rollback(client, batch_id, to_version=1, expected_version=1)
    assert response.status_code == 409
    assert response.json()["error"] == "Q_ROLLBACK_SAME"


def test_rollback_to_missing_version_is_rejected(client):
    batch_id = _import(client)
    _appeal(client, batch_id)
    response = _rollback(client, batch_id, to_version=9, expected_version=2)
    assert response.status_code == 404
    assert response.json()["error"] == "Q_ROLLBACK_TARGET"
    assert response.json()["detail"]["versions"] == [1, 2]


def test_rollback_stale_expected_version_is_rejected(client):
    batch_id = _import(client)
    _appeal(client, batch_id)
    response = _rollback(client, batch_id, to_version=1, expected_version=1)
    assert response.status_code == 409
    assert response.json()["error"] == "Q_STALE_VERSION"
    assert response.json()["detail"]["current_version"] == 2


def test_rollback_diff_lists_per_department_deltas(client):
    batch_id = _import(client)
    _appeal(client, batch_id)
    diff = _rollback(client, batch_id, 1, 2).json()["diff"]
    deltas = {row["dept"]: row["allocated_delta"] for row in diff["depts"]}
    assert deltas == {"数据平台": X.COST_CENTS["q1001"],
                      "营销": -X.COST_CENTS["q1001"]}
    assert diff["allocated_delta"] == 0
    assert diff["moved_cents"] == 2 * X.COST_CENTS["q1001"]
    assert diff["balanced"] is True
    keys = {row["key"] for row in diff["totals"]}
    assert "amount_total" not in keys


def test_rollback_keeps_amount_account_balanced(client):
    """不变量（守恒）：回滚后的部门账与金额账仍然相等。"""
    batch_id = _import(client)
    _appeal(client, batch_id)
    body = _rollback(client, batch_id, 1, 2).json()
    accounts = {item["key"]: item for item in body["accounts"]}
    assert accounts["金额账"]["ok"] is True
    assert accounts["金额账"]["left"] == X.TOTAL_CENTS
    assert accounts["金额账"]["right"] == X.TOTAL_CENTS
    assert accounts["部门账"]["ok"] is True
    assert accounts["部门账"]["right"] == X.ALLOCATED_CENTS


def test_version_timeline_records_each_kind(client):
    batch_id = _import(client)
    _appeal(client, batch_id)
    _rollback(client, batch_id, 1, 2)
    payload = client.get("/api/appeals").json()
    assert [(item["version"], item["kind"]) for item in payload["versions"]] == [
        (1, "import"), (2, "appeal"), (3, "rollback")]
    assert payload["versions"][2]["kind_label"] == "回滚"
    applications = payload["appeals"]
    assert [item["code"] for item in applications] == [""]
    codes = [row["code"] for row in client.get("/api/quarantine").json()["issues"]]
    assert "Q_ROLLBACK_DONE" in codes


def test_rollback_api_rejects_bad_payload(client):
    batch_id = _import(client)
    assert client.post("/api/appeals/rollback", json={}).status_code == 400
    assert client.post("/api/appeals/rollback",
                       json={"batch_id": batch_id}).status_code == 400
    assert client.post("/api/appeals/rollback",
                       json={"batch_id": batch_id,
                             "to_version": "v1"}).status_code == 400
    assert client.get("/api/appeals?left=1&right=1").json()["diff"]["depts"] == []


def test_diff_of_identical_states_is_empty():
    state = {"depts": [{"dept": "风控", "allocated_cents": 100, "kept_cents": 80,
                        "blocked_cents": 20, "budget_cents": 500,
                        "gate_status": "KEEP"}],
             "totals": {"amount_total": 100, "amount_allocated": 100}}
    same = rollback.diff(state, state)
    assert same["depts"] == [] and same["totals"] == []
    assert same["moved_cents"] == 0 and same["balanced"] is True
    grown = {**state, "depts": [{**state["depts"][0], "allocated_cents": 160}],
             "totals": {"amount_total": 160, "amount_allocated": 160}}
    change = rollback.diff(state, grown)
    assert change["depts"][0]["allocated_delta"] == 60
    assert change["totals"] == [
        {"key": "amount_total", "before": 100, "after": 160, "delta": 60},
        {"key": "amount_allocated", "before": 100, "after": 160, "delta": 60}]
