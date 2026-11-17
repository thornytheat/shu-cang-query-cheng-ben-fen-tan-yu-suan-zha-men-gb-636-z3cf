"""M1 采信：编码回退、BOM、重复表头、截断、列数、空态与拒绝路径。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import errors
from app.modules.m1_ingest import decode, reader, service
from fixtures import expected as X


def _probe(path):
    data = path.read_bytes()
    decoded = decode.decode_bytes(data)
    return decoded, reader.read_records(decoded["text"])


def _issues(store, batch_id):
    return store.load_quarantine(batch_id)


def test_trap02_bom_stripped_before_header(sample_env):
    """TRAP-02：UTF-8 BOM 必须剥离，否则首列名带 \\ufeff 后续取值全空。"""
    path = sample_env["write"]("bom.csv", ["q1,风控,报表,2026-11-05T10:00:00+08:00,"
                                           "3600,10,GB,2,30,"], bom=True)
    decoded = decode.decode_bytes(path.read_bytes())
    assert decoded["bom_stripped"] is True
    parsed = reader.read_records(decoded["text"])
    assert parsed["header"][0] == "query_id"
    summary = sample_env["run"]("bom.csv")
    codes = [row["code"] for row in _issues(sample_env["store"], summary["batch_id"])]
    assert "Q_BOM_STRIPPED" in codes
    assert summary["totals"]["rows_valid"] == 1


def test_trap01_charset_fallback_decodes_gb18030(store, settings):
    """TRAP-01：声明 utf-8 但字节是 GB18030，回退后中文部门名不乱码。"""
    summary = service.run_import(store, settings,
                                 source="bills_2026_11_gb18030.csv",
                                 charset="utf-8")
    bills = store.load_bills(summary["batch_id"])
    assert [bill["dept"] for bill in bills][:3] == ["数据平台", "风控", "营销"]
    codes = [row["code"] for row in _issues(store, summary["batch_id"])]
    assert "Q_CHARSET_FALLBACK" in codes


def test_trap01_decode_failure_reports_first_bad_line():
    """TRAP-01 的反面：全部编码都失败时给出首个坏字节所在行号。"""
    payload = b"query_id,dept\r\nq1,\x80\xff\xfe bad\r\n"
    with pytest.raises(decode.DecodeFailure) as excinfo:
        decode.decode_bytes(payload)
    assert excinfo.value.line_no == 2
    assert excinfo.value.code == "Q_DECODE_FAILED"


def test_trap04_duplicate_header_starts_new_segment(store, settings):
    """TRAP-04：分段导出里的重复表头不能被当成数据行。"""
    summary = service.run_import(store, settings,
                                 source="bills_2026_11_dirty.csv")
    totals = summary["totals"]
    assert totals["segment_count"] == X.SEGMENTS
    assert totals["duplicate_headers"] == X.DUPLICATE_HEADERS
    bills = store.load_bills(summary["batch_id"])
    assert all(bill["query_id"] != "query_id" for bill in bills)
    codes = [row["code"] for row in _issues(store, summary["batch_id"])]
    assert "Q_DUP_HEADER" in codes


def test_trap16_truncated_last_row_isolated(store, settings):
    """TRAP-16：末行没有换行且列数不足，按截断拒绝而不是当有效数据。"""
    summary = service.run_import(store, settings,
                                 source="bills_2026_11_dirty.csv")
    rows = [row for row in _issues(store, summary["batch_id"])
            if row["code"] == "Q_TRUNCATED_ROW"]
    assert len(rows) == 1
    assert rows[0]["row_ref"] == "第 22 行"
    assert rows[0]["raw_value"] == "6"
    assert summary["totals"]["rows_quarantine"] == X.ROWS_QUARANTINE


def test_trap05_extra_column_quarantined_with_reason(store, settings):
    """TRAP-05：多一列的行必须带原因码进隔离区，不能静默补空。"""
    summary = service.run_import(store, settings,
                                 source="bills_2026_11_dirty.csv")
    rows = [row for row in _issues(store, summary["batch_id"])
            if row["code"] == "Q_COLUMN_COUNT"]
    assert len(rows) == 1
    assert rows[0]["row_ref"] == "第 21 行"
    assert rows[0]["raw_value"] == "11"


def test_blank_and_all_empty_rows_are_counted_separately(store, settings):
    """空行与「字段全空」的记录分开计入对账，不混进有效行。"""
    summary = service.run_import(store, settings,
                                 source="bills_2026_11_dirty.csv")
    totals = summary["totals"]
    assert totals["blank_lines"] == X.BLANK_LINES
    assert totals["rows_empty"] == X.ROWS_EMPTY
    assert totals["rows_data"] == X.ROWS_DATA


def test_empty_states_are_three_distinct(sample_env):
    """空态三态：仅表头 / 全空行 / 无表头，结论必须不同。"""
    sample_env["write"]("only_header.csv", [])
    only_header = sample_env["run"]("only_header.csv")
    assert only_header["empty_state"] == "ONLY_HEADER"
    assert only_header["totals"]["rows_data"] == 0

    sample_env["write"]("all_empty.csv", [",,,,,,,,,", ",,,,,,,,,"])
    all_empty = sample_env["run"]("all_empty.csv")
    assert all_empty["empty_state"] == "ALL_EMPTY"
    assert all_empty["totals"]["rows_empty"] == 2

    (sample_env["dir"] / "blank.csv").write_bytes(b"\r\n\r\n")
    with pytest.raises(errors.Unprocessable) as excinfo:
        sample_env["run"]("blank.csv")
    assert excinfo.value.code == "E_NO_HEADER"

    (sample_env["dir"] / "zero.csv").write_bytes(b"")
    with pytest.raises(errors.Unprocessable) as excinfo:
        sample_env["run"]("zero.csv")
    assert excinfo.value.code == "E_EMPTY_FILE"


def test_import_rejects_unsafe_or_unknown_source(client):
    """失败输入：路径穿越、缺失文件、非法后缀、空请求体都给 4xx。"""
    assert client.post("/api/import", json={"source": "../etc/passwd"}).status_code == 400
    missing = client.post("/api/import", json={"source": "nope.csv"})
    assert missing.status_code == 404
    assert missing.json()["error"] == "E_NOT_FOUND"
    assert client.post("/api/import", json={"source": "budgets.json"}).status_code == 400
    assert client.post("/api/import", json={}).status_code == 400
    assert client.post("/api/import", json=[]).status_code == 400


def test_reader_keeps_quoted_thousands_as_single_field(sample_env):
    """引号内的千分位逗号不能被当成分隔符切成两列。"""
    path = sample_env["write"]("quoted.csv", [
        'q1,风控,报表,2026-11-05T10:00:00+08:00,3600,"1,024.0",MB,2,30,'])
    _, parsed = _probe(path)
    data_rows = [row for row in parsed["records"] if row["kind"] == "data"]
    assert len(data_rows) == 1
    assert len(data_rows[0]["fields"]) == 10
    assert data_rows[0]["fields"][5] == "1,024.0"
