"""M6 导出：证据列、BOM、份额与金额对齐、运行清单、收据联。"""
from __future__ import annotations

import csv
import io
import json

from app import errors
from app.modules.m6_export import csv_out, service
from fixtures import expected as X


def _rows(text: str) -> list[dict]:
    body = text.lstrip("\ufeff")
    return list(csv.DictReader(io.StringIO(body)))


def test_bill_csv_has_evidence_columns(dirty, store, settings):
    payload = service.build(store, settings, store.get_batch(dirty["batch_id"]), "bill")
    rows = _rows(payload["text"])
    assert payload["filename"].endswith("-bill.csv")
    assert set(service.BILL_FIELDS) == set(rows[0].keys())
    assert len(rows) == X.ROWS_VALID
    first = rows[0]
    assert first["row_ref"] == "第 2 行"
    assert first["query_id"] == "q1001"
    assert first["scan_raw"] == "120.5"
    assert first["cost_yuan"] == "3.58"


def test_csv_carries_bom_and_crlf(dirty, store, settings):
    payload = service.build(store, settings, store.get_batch(dirty["batch_id"]), "bill")
    text = payload["text"]
    assert text.startswith("\ufeff")
    assert "\r\n" in csv_out.to_csv(["a"], [{"a": 1}])


def test_quarantine_csv_matches_isolated_rows(dirty, store, settings):
    payload = service.build(store, settings, store.get_batch(dirty["batch_id"]),
                            "quarantine")
    rows = _rows(payload["text"])
    codes = [row["code"] for row in rows]
    assert codes.count("Q_DUP_CONFLICT") == 2
    assert "Q_TRUNCATED_ROW" in codes
    assert "Q_SENTINEL" in codes
    assert all(row["code"] and row["level"] for row in rows)
    dup_rows = [row for row in rows if row["code"] == "Q_DUP_CONFLICT"]
    assert all(row["row_ref"].startswith("第 ") for row in dup_rows)
    assert any(row["level"] == "info" for row in rows)


def test_shares_csv_totals_equal_allocation(dirty, store, settings):
    payload = service.build(store, settings, store.get_batch(dirty["batch_id"]),
                            "shares")
    rows = _rows(payload["text"])
    total = sum(round(float(row["share_yuan"]) * 100) for row in rows)
    assert total == X.TOTAL_CENTS
    shared = [row for row in rows if row["kind"] == "SHARED"]
    assert len(shared) == 2
    assert {row["dept"] for row in shared} == {"数据平台", "风控"}


def test_gates_csv_lists_decisions(dirty, store, settings):
    payload = service.build(store, settings, store.get_batch(dirty["batch_id"]),
                            "gates")
    rows = _rows(payload["text"])
    decisions = [row["decision"] for row in rows]
    assert decisions.count("TRUNCATE") == 3
    assert decisions.count("KEEP") == 7
    assert rows[0]["cumulative_yuan"] == "0.01"


def test_manifest_json_has_run_id_and_input_digest(dirty, store, settings):
    payload = service.build(store, settings, store.get_batch(dirty["batch_id"]),
                            "manifest")
    data = json.loads(payload["text"])
    assert data["run_id"].startswith("CSR-")
    assert len(data["input_digest"]) == 64
    assert data["counters"]["rows_data"] == X.ROWS_DATA
    assert data["money"].startswith("金额 Decimal")
    assert data["timezone"].startswith("入库 UTC")


def test_receipt_has_batch_no_check_digit_and_perforation(dirty, store, settings):
    batch = store.get_batch(dirty["batch_id"])
    payload = service.build(store, settings, batch, "receipt")
    text = payload["text"]
    assert batch["batch_no"] in text
    assert f"校验位{' ' * 8}{batch['check_digit']}" in text
    assert "骑缝线" in text
    assert "73.15" in text


def test_unknown_export_kind_is_rejected(dirty, store, settings):
    batch = store.get_batch(dirty["batch_id"])
    try:
        service.build(store, settings, batch, "pdf")
    except errors.BadRequest as exc:
        assert exc.code == "E_BAD_REQUEST"
        assert "pdf" in exc.message
    else:
        raise AssertionError("应当拒绝不支持的导出类型")
