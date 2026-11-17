"""M3 质量：重复账单、哨兵、单位、时间、零扫描与行账守恒。"""
from __future__ import annotations

from app.modules.m3_quality import metrics, quarantine, rules, service
from fixtures import expected as X


def _codes(store, batch_id: str) -> list[str]:
    return [row["code"] for row in store.load_quarantine(batch_id)]


def _by_code(store, batch_id: str, code: str) -> list[dict]:
    return [row for row in store.load_quarantine(batch_id) if row["code"] == code]


def test_trap14_duplicate_query_conflict_isolates_both(dirty, store):
    """TRAP-14：同查询编号两个不同金额，整组隔离而不是后写覆盖。"""
    rows = _by_code(store, dirty["batch_id"], "Q_DUP_CONFLICT")
    assert sorted(row["row_ref"] for row in rows) == ["第 6 行", "第 7 行"]
    assert {row["raw_value"] for row in rows} == {"q1005"}
    valid_ids = {bill["query_id"] for bill in store.load_bills(dirty["batch_id"])}
    assert "q1005" not in valid_ids
    assert dirty["totals"]["rows_quarantine"] == X.ROWS_QUARANTINE


def test_identical_duplicate_row_is_deduped_not_quarantined(dirty, store):
    rows = _by_code(store, dirty["batch_id"], "Q_DUP_IDENTICAL")
    assert len(rows) == 1
    assert rows[0]["row_ref"] == "第 20 行"
    assert dirty["totals"]["rows_dedup"] == X.ROWS_DEDUP
    assert dirty["totals"]["rows_quarantine"] == X.ROWS_QUARANTINE


def test_trap07_sentinel_amount_is_rejected_not_zeroed(dirty, store):
    """TRAP-07：`N/A` 哨兵不能当 0 参与合计，整行进隔离区。"""
    rows = _by_code(store, dirty["batch_id"], "Q_SENTINEL")
    assert [row["row_ref"] for row in rows] == ["第 9 行"]
    assert rows[0]["raw_value"] == "N/A"
    total = dirty["totals"]["amount_total"]
    assert total == X.TOTAL_CENTS
    assert total > 0


def test_unknown_unit_reports_reason_code(dirty, store):
    rows = _by_code(store, dirty["batch_id"], "Q_BAD_UNIT")
    assert [row["row_ref"] for row in rows] == ["第 18 行"]
    assert rows[0]["raw_value"] == "XB"
    assert rows[0]["level"] == "error"


def test_bad_timestamp_reports_reason_code(dirty, store):
    rows = _by_code(store, dirty["batch_id"], "Q_BAD_TIME")
    assert [row["row_ref"] for row in rows] == ["第 17 行"]
    assert rows[0]["raw_value"] == "2026-13-45T99:00:00+08:00"


def test_zero_scan_is_billed_at_minimum_not_isolated(dirty, store):
    bills = {bill["query_id"]: bill for bill in store.load_bills(dirty["batch_id"])}
    assert bills["q1008"]["status"] == "ZERO_SCAN"
    assert bills["q1008"]["cost_cents"] == X.MIN_CHARGE_CENTS
    codes = _codes(store, dirty["batch_id"])
    assert "Q_ZERO_SCAN" in codes
    assert "Q_SENTINEL" not in [row["code"] for row in _by_code(
        store, dirty["batch_id"], "Q_TRUNCATED_ROW")]


def test_row_account_is_balanced(dirty, store):
    payload = service.load(store, dirty["batch_id"])
    account = payload["row_account"]
    assert account["ok"] is True
    assert account["left"] == X.ROWS_DATA
    assert account["right"] == (X.ROWS_VALID + X.ROWS_DEDUP
                                + X.ROWS_QUARANTINE + X.ROWS_EMPTY)
    assert payload["summary"]["rows_data"] == X.ROWS_DATA
    assert payload["summary"]["rows_valid"] == X.ROWS_VALID


def test_pass_rate_and_distribution_match_sample(dirty, store):
    payload = service.load(store, dirty["batch_id"])
    assert payload["summary"]["pass_rate"] == 52.6
    error_rows = sum(item["count"] for item in payload["distribution"]
                     if item["level"] == "error")
    assert error_rows == X.ROWS_QUARANTINE
    codes = {item["code"]: item["count"] for item in payload["distribution"]}
    assert codes["Q_DUP_CONFLICT"] == 2
    assert codes["Q_TRUNCATED_ROW"] == 1


def test_counts_are_independent_of_dict_order(tmp_path):
    """同一批记录换个遍历顺序，判定结果与计数不变。"""
    from app.modules.m1_ingest import reader
    from tests_helper import CRAFT_HEADER, CRAFT_ROWS

    text = "\r\n".join([CRAFT_HEADER] + CRAFT_ROWS) + "\r\n"
    parsed = reader.read_records(text)
    first = rules.validate({**parsed, "mapping": _mapping(), "expected_columns": 10,
                            "data_records": [r for r in parsed["records"]
                                             if r["kind"] == "data"]})
    reversed_text = "\r\n".join([CRAFT_HEADER] + list(reversed(CRAFT_ROWS))) + "\r\n"
    parsed_reversed = reader.read_records(reversed_text)
    second = rules.validate({**parsed_reversed, "mapping": _mapping(),
                             "expected_columns": 10,
                             "data_records": [r for r in parsed_reversed["records"]
                                              if r["kind"] == "data"]})
    assert first["counters"]["rows_valid"] == second["counters"]["rows_valid"]
    assert first["counters"]["rows_quarantine"] == second["counters"]["rows_quarantine"]
    assert sorted(row["code"] for row in first["issues"]) == \
        sorted(row["code"] for row in second["issues"])


def _mapping() -> dict:
    from app.modules.m2_billing import contract
    return {name: index for index, name in enumerate(contract.COLUMN_NAMES)}


def test_metrics_summary_handles_empty_input():
    summary = metrics.summarize([], {"rows_data": 0, "rows_valid": 0})
    assert summary["pass_rate"] == 0.0
    assert summary["top_code"] == ""
    assert quarantine.by_level([]) == {"error": 0, "warn": 0, "info": 0}
