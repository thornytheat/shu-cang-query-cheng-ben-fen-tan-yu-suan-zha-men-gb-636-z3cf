"""端到端：脏样例 → 隔离 → 计费 → 分摊 → 闸门 → 导出，以及幂等与失败输入。"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from fixtures import expected as X


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))


def test_dirty_sample_runs_from_import_to_export(client):
    response = client.post("/api/import", json={"source": "bills_2026_11_dirty.csv"})
    assert response.status_code == 200
    summary = response.json()
    assert summary["batch_no"].startswith("CS-20261115-")
    assert summary["check_ok"] is True
    assert summary["totals"]["rows_data"] == X.ROWS_DATA
    assert summary["totals"]["amount_total"] == X.TOTAL_CENTS
    assert summary["accounts"][0]["ok"] and summary["accounts"][1]["ok"]
    assert summary["accounts"][2]["ok"] and summary["accounts"][3]["ok"]

    batch_id = summary["batch_id"]
    allocations = client.get(f"/api/allocations?batch_id={batch_id}").json()
    assert allocations["allocated_cents"] + allocations["unallocated_cents"] \
        == allocations["total_cents"] == X.TOTAL_CENTS
    depts = {row["dept"]: row["allocated_cents"] for row in allocations["depts"]}
    assert depts == X.DEPT_ALLOCATED_CENTS

    exported = client.get(f"/api/export?kind=bill&batch_id={batch_id}")
    assert exported.status_code == 200
    rows = _rows(exported.text)
    total = sum(Decimal(row["cost_yuan"]) for row in rows)
    assert total == Decimal(X.TOTAL_CENTS) / 100
    assert exported.headers["x-check-digit"] == summary["check_digit"]


def test_rerun_same_source_returns_same_batch(client):
    first = client.post("/api/import", json={"source": "bills_2026_11_dirty.csv"}).json()
    second = client.post("/api/import", json={"source": "bills_2026_11_dirty.csv"}).json()
    assert second["reused"] is True
    assert second["batch_id"] == first["batch_id"]
    batches = client.get("/api/batches").json()["batches"]
    assert len(batches) == 1
    assert batches[0]["row_count"] == X.ROWS_VALID
    assert batches[0]["total_cents"] == X.TOTAL_CENTS


def test_gb18030_source_runs_end_to_end(client):
    response = client.post("/api/import", json={
        "source": "bills_2026_11_gb18030.csv", "charset": "utf-8"})
    assert response.status_code == 200
    summary = response.json()
    assert summary["totals"]["rows_valid"] == 6
    quarantine = client.get("/api/quarantine").json()
    codes = [row["code"] for row in quarantine["issues"]]
    assert "Q_CHARSET_FALLBACK" in codes
    allocations = client.get("/api/allocations").json()
    depts = {row["dept"] for row in allocations["depts"]}
    assert depts == {"数据平台", "风控", "营销"}


def test_failure_inputs_return_client_errors(client):
    """空库先看 404，再逐个喂坏输入；失败尝试不覆盖已有明细。"""
    assert client.get("/api/gates").status_code == 404
    assert client.get("/api/allocations").status_code == 404
    assert client.get("/api/export?kind=bill").status_code == 404
    assert client.post("/api/import", json={"source": "../../etc/passwd"}).status_code == 400
    assert client.post("/api/import", json={"source": "missing.csv"}).status_code == 404
    failed = client.post("/api/import", json={"source": "bills_blank.csv"})
    assert failed.status_code == 422
    assert failed.json()["error"] == "E_NO_HEADER"
    unsupported = client.get("/api/export?kind=nope")
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == "E_BAD_REQUEST"


def test_all_pages_render_after_import(client):
    summary = client.post("/api/import",
                          json={"source": "bills_2026_11_dirty.csv"}).json()
    for path in ("/", "/allocations", "/gates", "/quarantine", "/batches", "/export"):
        page = client.get(path)
        assert page.status_code == 200
        assert summary["batch_no"] in page.text
        assert "查询成本分摊清单" in page.text
    assert client.get("/api/contract").json()["money_unit"] == "元"
