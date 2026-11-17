"""M7 申诉：改判归属、按时段重算、重复申诉去重、冲突定序与失败输入。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app import config
from app.modules.m2_billing import coerce
from app.modules.m7_appeal import appeal, buckets
from fixtures import expected as X

IMPORT = {"source": "bills_2026_11_dirty.csv"}


def _import(client) -> str:
    response = client.post("/api/import", json=IMPORT)
    assert response.status_code == 200
    return response.json()["batch_id"]


def _appeal(client, entries, *, batch_id=None, expected_version=1):
    payload = {"appeals": entries, "expected_version": expected_version}
    if batch_id:
        payload["batch_id"] = batch_id
    return client.post("/api/appeals", json=payload)


def _depts(client, batch_id=None) -> dict[str, dict]:
    url = "/api/allocations" + (f"?batch_id={batch_id}" if batch_id else "")
    rows = client.get(url).json()["depts"]
    return {row["dept"]: row for row in rows}


def _share_depts(client, query_id: str) -> dict[str, int]:
    rows = client.get("/api/allocations").json()["shares"]
    return {row["dept"]: row["share_cents"] for row in rows
            if row["query_id"] == query_id}


def test_appeal_moves_own_query_to_target_department(client):
    batch_id = _import(client)
    before = _depts(client, batch_id)
    assert before["数据平台"]["allocated_cents"] == X.DEPT_ALLOCATED_CENTS["数据平台"]

    response = _appeal(client, [{"query_id": "q1001", "dept": "数据平台",
                                 "target_dept": "营销"}], batch_id=batch_id)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 2
    assert body["applied"] == 1 and body["rejected"] == 0
    assert _share_depts(client, "q1001") == {"营销": X.COST_CENTS["q1001"]}
    after = _depts(client, batch_id)
    assert after["数据平台"]["allocated_cents"] == (
        X.DEPT_ALLOCATED_CENTS["数据平台"] - X.COST_CENTS["q1001"])
    assert after["营销"]["allocated_cents"] == (
        X.DEPT_ALLOCATED_CENTS["营销"] + X.COST_CENTS["q1001"])
    assert after["风控"]["allocated_cents"] == X.DEPT_ALLOCATED_CENTS["风控"]


def test_appeal_reprice_follows_department_volume_threshold(client):
    """改判把 120.5 GB 带进已达 1024 GB 的部门，扫描部分开始打折，成本随之变化。"""
    batch_id = _import(client)
    body = _appeal(client, [{"query_id": "q1001", "target_dept": "风控"}],
                   batch_id=batch_id).json()
    assert body["applied"] == 1
    assert _share_depts(client, "q1001") == {"风控": 325}
    assert int(body["totals"]["amount_total"]) == X.TOTAL_CENTS - 358 + 325
    assert (int(body["totals"]["amount_allocated"])
            + int(body["totals"]["amount_unallocated"])
            == int(body["totals"]["amount_total"]))


def test_trap27_appeal_reprice_uses_each_hour_bucket(client):
    """TRAP-27：改判到 21:30 起 2 小时，前半小时日价、后 1.5 小时夜价。"""
    batch_id = _import(client)
    response = _appeal(client, [{
        "query_id": "q1001", "target_dept": "数据平台",
        "fix": {"started_at": "2026-11-05T21:30:00+08:00", "duration_s": "7200"},
    }], batch_id=batch_id)
    assert response.status_code == 200
    body = response.json()
    check = body["buckets"][0]
    assert check["query_id"] == "q1001"
    assert check["hours"] == "2.000000"
    assert check["cents"] == 53
    assert check["billed_cents"] == 53
    assert [item["phase"] for item in check["buckets"]] == ["day", "night", "night"]
    naive_whole_at_start = coerce.to_cents(
        Decimal("2") * config.DAY_RATE_PER_HOUR)
    assert naive_whole_at_start == 84
    assert naive_whole_at_start != check["cents"]


def test_trap27_hours_after_midnight_are_not_dropped(client):
    """TRAP-27 的另一种朴素写法：只算起始小时，零点后的 1 小时被丢掉。"""
    batch_id = _import(client)
    response = _appeal(client, [{
        "query_id": "q1004", "target_dept": "数据平台",
        "fix": {"started_at": "2026-11-05T23:30:00+08:00", "duration_s": "5400"},
    }], batch_id=batch_id)
    assert response.status_code == 200
    check = response.json()["buckets"][0]
    assert check["hours"] == "1.500000"
    assert check["crosses_day"] is True
    assert check["cents"] == 32
    assert check["billed_cents"] == 32
    kept_only_first_hour = coerce.to_cents(
        Decimal("0.5") * config.NIGHT_RATE_PER_HOUR)
    assert kept_only_first_hour == 11
    assert kept_only_first_hour != check["cents"]


def test_bucket_endpoints_include_start_and_exclude_end():
    """22:00 起按夜价（含起点），06:00 起按日价（不含夜间终点）。"""
    night = buckets.rebucket(coerce.parse_instant("2026-11-05T22:00:00+08:00"),
                            Decimal("3600"))
    assert len(night) == 1
    assert night[0]["phase"] == "night"
    assert buckets.cost_sum(night) == Decimal("0.210000")
    assert buckets.boundary_marks(night) == ["22:00 起夜价"]

    day = buckets.rebucket(coerce.parse_instant("2026-11-05T06:00:00+08:00"),
                           Decimal("3600"))
    assert len(day) == 1
    assert day[0]["phase"] == "day"
    assert buckets.boundary_marks(day) == ["06:00 起日价"]

    mixed = buckets.rebucket(coerce.parse_instant("2026-11-05T05:30:00+08:00"),
                             Decimal("7200"))
    assert [item["phase"] for item in mixed] == ["night", "day", "day"]
    assert buckets.conserved(mixed, Decimal("7200")) is True
    assert buckets.cost_sum(mixed) == Decimal("0.735000")


def test_trap28_rejudgement_keeps_total_conserved(client):
    """TRAP-28：改判部门后总额不变；只加不减会把总额算重一遍。"""
    batch_id = _import(client)
    response = _appeal(client, [{"query_id": "q1001", "target_dept": "营销"}],
                       batch_id=batch_id)
    assert response.status_code == 200
    totals = response.json()["totals"]
    assert int(totals["amount_total"]) == X.TOTAL_CENTS
    assert int(totals["amount_allocated"]) == X.ALLOCATED_CENTS
    assert int(totals["amount_unallocated"]) == X.UNALLOCATED_CENTS
    assert (int(totals["amount_allocated"]) + int(totals["amount_unallocated"])
            == int(totals["amount_total"]))
    assert (int(totals["amount_kept"]) + int(totals["amount_blocked"])
            == int(totals["amount_allocated"]))
    depts = _depts(client, batch_id)
    assert sum(row["allocated_cents"] for row in depts.values()) == X.ALLOCATED_CENTS
    naive = dict((name, row["allocated_cents"]) for name, row in depts.items())
    naive["营销"] = naive["营销"] + X.COST_CENTS["q1001"]
    assert sum(naive.values()) == X.ALLOCATED_CENTS + X.COST_CENTS["q1001"]


def test_duplicate_appeals_on_same_query_are_merged_once(client):
    batch_id = _import(client)
    body = _appeal(client, [{"query_id": "q1001", "target_dept": "营销"},
                            {"query_id": "q1001", "target_dept": "营销"}],
                   batch_id=batch_id).json()
    assert body["applied"] == 1
    assert body["merged"] == 1
    assert [row["code"] for row in body["appeals"]] == ["", "Q_APPEAL_MERGED"]
    assert any(item["code"] == "Q_APPEAL_MERGED" for item in body["issues"])
    depts = _depts(client, batch_id)
    assert depts["营销"]["allocated_cents"] == (
        X.DEPT_ALLOCATED_CENTS["营销"] + X.COST_CENTS["q1001"])


def test_conflicting_appeals_keep_the_first_one(client):
    batch_id = _import(client)
    body = _appeal(client, [{"query_id": "q1001", "target_dept": "营销"},
                            {"query_id": "q1001", "target_dept": "风控"}],
                   batch_id=batch_id).json()
    assert body["applied"] == 1
    assert body["rejected"] == 1
    assert [row["code"] for row in body["appeals"]] == ["", "Q_APPEAL_CONFLICT"]
    assert _share_depts(client, "q1001") == {"营销": X.COST_CENTS["q1001"]}
    assert _depts(client, batch_id)["风控"]["allocated_cents"] == \
        X.DEPT_ALLOCATED_CENTS["风控"]


def test_appeal_with_unknown_query_is_rejected(client):
    batch_id = _import(client)
    response = _appeal(client, [{"query_id": "q9999", "target_dept": "风控"}],
                       batch_id=batch_id)
    assert response.status_code == 422
    assert response.json()["error"] == "Q_APPEAL_NONE"
    assert response.json()["detail"]["issues"][0]["code"] == "Q_APPEAL_NO_QUERY"
    assert client.get("/api/gates").json()["version"] == 1


def test_appeal_rejects_unregistered_fix_field(client):
    batch_id = _import(client)
    response = _appeal(client, [{"query_id": "q1001", "target_dept": "风控",
                                 "fix": {"concurrency": "4"}}], batch_id=batch_id)
    assert response.status_code == 422
    codes = [item["code"] for item in response.json()["detail"]["issues"]]
    assert codes == ["Q_APPEAL_BAD_FIX"]


def test_appeal_api_rejects_bad_payload_and_stale_version(client):
    batch_id = _import(client)
    assert client.post("/api/appeals", json={}).status_code == 400
    assert client.post("/api/appeals", json={"appeals": []}).status_code == 400
    assert client.post("/api/appeals", json={"appeals": "q1"}).status_code == 400
    assert client.post("/api/appeals", json={"appeals": [{}]}).status_code == 422
    first = _appeal(client, [{"query_id": "q1001", "target_dept": "风控"}],
                    batch_id=batch_id)
    assert first.json()["version"] == 2
    stale = _appeal(client, [{"query_id": "q1001", "target_dept": "营销"}],
                    batch_id=batch_id, expected_version=1)
    assert stale.status_code == 409
    assert stale.json()["error"] == "Q_STALE_VERSION"
    assert stale.json()["detail"]["current_version"] == 2


def test_appeal_parse_rejects_bad_shape_entries():
    parsed = appeal.parse_entries([{"query_id": "q1"},
                                   {"target_dept": "风控"},
                                   "q1",
                                   {"query_id": "q1", "target_dept": "风控",
                                    "fix": {"scan_unit": "GB"}}])
    assert [row["status"] for row in parsed] == [appeal.REJECTED] * 4
    assert [row["code"] for row in parsed] == ["Q_APPEAL_BAD_FIX"] * 4


def test_replaying_same_appeal_keeps_dept_totals(client):
    """不变量（幂等）：同一条申诉再提交一次，部门分摊不变，只升版本。"""
    batch_id = _import(client)
    entry = {"query_id": "q1001", "target_dept": "营销"}
    first = _appeal(client, [entry], batch_id=batch_id)
    assert first.status_code == 200
    first_depts = _depts(client, batch_id)
    second = _appeal(client, [entry], batch_id=batch_id, expected_version=2)
    assert second.status_code == 200
    assert second.json()["version"] == 3
    second_depts = _depts(client, batch_id)
    assert {name: row["allocated_cents"] for name, row in second_depts.items()} == {
        name: row["allocated_cents"] for name, row in first_depts.items()}


def test_appeals_page_renders_versions_accounts_and_buckets(client):
    batch_id = _import(client)
    _appeal(client, [{"query_id": "q1004", "target_dept": "数据平台",
                      "fix": {"started_at": "2026-11-05T23:30:00+08:00",
                              "duration_s": "5400"}}], batch_id=batch_id)
    page = client.get("/appeals")
    assert page.status_code == 200
    body = page.text
    assert "申诉回滚" in body
    assert "v1" in body and "v2" in body
    assert "金额账" in body and "部门账" in body
    assert "AP-2-01" in body
    assert "时段复核" in body and "q1004" in body
    assert "23:00 night" in body
