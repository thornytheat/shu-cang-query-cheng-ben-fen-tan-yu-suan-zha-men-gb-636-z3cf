"""M2 计费：单位归一、千分位/全角、大整数保形、分桶、规则叠加与优先级。"""
from __future__ import annotations

from decimal import Decimal

from app import config
from app.modules.m2_billing import contract, coerce, service, units
from app.modules.m6_export import json_out
from fixtures import expected as X

BASE_ROW = {
    "query_id": "c1",
    "dept": "数据平台",
    "project": "日报",
    "started_at": "2026-11-05T10:00:00+08:00",
    "duration_s": "3600",
    "scan_size": "100",
    "scan_unit": "GB",
    "concurrency": "4",
    "value_score": "80",
    "shared_with": "",
}


def _preview(**overrides) -> dict:
    row = dict(BASE_ROW)
    row.update(overrides)
    return service.preview(row)


def test_day_rate_costs_scan_plus_hours():
    priced = _preview()
    assert priced["cost_cents"] == X.CASE_DAY_CENTS
    assert priced["scan_cost_cents"] == 280
    assert priced["hour_cost_cents"] == 42
    assert priced["addon_cents"] == 0


def test_trap06_thousands_and_fullwidth_are_normalised():
    """TRAP-06：千分位、全角数字、全角小数点都要归一后再 Decimal 化。"""
    thousands = _preview(scan_size="1,024.0", scan_unit="MB", started_at="2026-11-05T10:00:00+08:00")
    assert thousands["scan_gb"] == X.CASE_THOUSANDS_GB
    fullwidth = _preview(scan_size="１２８．０", scan_unit="GB")
    assert fullwidth["scan_gb"] == X.CASE_FULLWIDTH_GB
    assert coerce.parse_decimal("１，２３４．５", "scan_size") == Decimal("1234.5")


def test_trap15_units_mb_gb_tb_share_one_basis():
    """TRAP-15：MB/GB/TB 混用先折算成 GB 再相加，差 1024 倍的问题不出现。"""
    gbs = [units.to_gb(Decimal("1"), "MB"), units.to_gb(Decimal("1"), "GB"),
           units.to_gb(Decimal("1"), "TB")]
    assert sum(gbs) == X.CASE_MIXED_UNITS_GB
    assert units.to_gb(Decimal("2048"), "MB") == Decimal("2.000000")
    assert units.to_gb(Decimal("1"), "PB") == Decimal("1048576.000000")


def test_trap09_big_integer_keeps_every_digit(store, settings):
    """TRAP-09：超过 2^53 的字节数逐位保形，导出不得出现科学计数法。"""
    summary = service_bignum(store, settings)
    bills = {bill["query_id"]: bill for bill in store.load_bills(summary["batch_id"])}
    raw_text = bills["qB001"]["raw"]["scan_bytes"]
    assert raw_text == "9007199254740993"
    assert "E" not in raw_text and "e" not in raw_text
    assert bills["qB002"]["raw"]["scan_bytes"] == "9444732965739291475968"
    payload = json_out.dumps({bill["query_id"]: bill["raw"]["scan_bytes"]
                              for bill in bills.values()})
    assert "e+15" not in payload and "E+15" not in payload
    codes = [row["code"] for row in store.load_quarantine(summary["batch_id"])]
    assert codes.count("Q_BIG_INT") == 2


def service_bignum(store, settings):
    from app.modules.m1_ingest import service as ingest_service
    return ingest_service.run_import(store, settings, source="bills_bignum.csv")


def test_trap10_float_accumulation_loses_a_cent():
    """TRAP-10：三行各 0.145 元，float 累加后变成 0.43499999999999994，少 1 分。"""
    exact = coerce.to_cents(sum([Decimal("0.145")] * 3))
    naive = 0.0
    for _ in range(3):
        naive += 0.145
    assert exact == 44
    assert int(naive * 100) == 43
    assert str(naive) == "0.43499999999999994"


def test_trap23_hour_buckets_are_half_open_and_consecutive():
    """TRAP-23：小时桶左闭右开，跨零点续切且时长之和恰等于原时长。"""
    from app import config as cfg
    from app.modules.m2_billing import coerce as c
    start = c.parse_instant("2026-11-05T23:30:00+08:00")
    buckets = units.split_hours(start, Decimal("3600"))
    assert len(buckets) == 2
    assert sum(hours for _, hours in buckets) == Decimal("1.000000")
    first, second = buckets[0][0], buckets[1][0]
    assert (second - first).total_seconds() == 3600
    assert units.local_hour_of(first) == 23
    assert units.local_hour_of(second) == 0
    assert units.is_night(first) and units.is_night(second)
    assert cfg.NIGHT_START_HOUR == 22


def test_trap23_six_o_clock_bucket_is_day_rate():
    """TRAP-23 的边界：05:30 起一小时，前半小时夜间价、后半小时白天价。"""
    priced = _preview(started_at="2026-11-05T05:30:00+08:00", duration_s="3600",
                      scan_size="10")
    assert priced["cost_cents"] == X.CASE_BOUNDARY_CENTS
    rates = [item["rate"] for item in priced["buckets"]]
    assert rates == [str(X.NIGHT_RATE), str(X.DAY_RATE)]


def test_trap19_rule_order_independent_of_registration():
    """TRAP-19：多条规则同时命中时，结果只由 (priority, id) 决定。"""
    canonical = [rule["id"] for rule in contract.ordered_rules()]
    assert canonical == ["R_NIGHT_RATE", "R_VOLUME_DISCOUNT",
                         "R_CONC_SURCHARGE", "R_MIN_CHARGE"]
    reversed_rules = list(reversed(config.RULES))
    assert [rule["id"] for rule in contract.ordered_rules(reversed_rules)] == canonical
    shuffled = [config.RULES[2], config.RULES[0], config.RULES[3], config.RULES[1]]
    assert [rule["id"] for rule in contract.ordered_rules(shuffled)] == canonical


def test_night_volume_and_concurrency_rules_stack(store, settings):
    """三条规则同时命中同一条查询：夜间价 + 大客户折扣 + 并发加价。"""
    summary = service_bignum(store, settings)
    dirty = dirty_batch(store, settings)
    bills = {bill["query_id"]: bill
             for bill in store.load_bills(dirty["batch_id"])}
    assert bills["q1002"]["rules"] == ["R_NIGHT_RATE", "R_VOLUME_DISCOUNT",
                                       "R_CONC_SURCHARGE"]
    assert bills["q1002"]["cost_cents"] == X.COST_CENTS["q1002"]
    assert summary["totals"]["amount_total"] > 0


def dirty_batch(store, settings):
    from app.modules.m1_ingest import service as ingest_service
    return ingest_service.run_import(store, settings,
                                     source="bills_2026_11_dirty.csv")


def test_concurrency_surcharge_only_above_free_limit():
    assert _preview(concurrency="12")["cost_cents"] == X.CASE_CONCURRENCY_CENTS
    assert _preview(concurrency="12")["addon_cents"] == 60
    assert _preview(concurrency="8")["addon_cents"] == 0
    assert _preview(concurrency="8")["cost_cents"] == X.CASE_DAY_CENTS


def test_minimum_charge_floors_tiny_query(store, settings):
    dirty = dirty_batch(store, settings)
    bills = {bill["query_id"]: bill
             for bill in store.load_bills(dirty["batch_id"])}
    assert bills["q1008"]["cost_cents"] == X.MIN_CHARGE_CENTS
    assert bills["q1008"]["rules"] == ["R_MIN_CHARGE"]
    assert bills["q1008"]["status"] == "ZERO_SCAN"


def test_volume_discount_needs_department_threshold(store, settings):
    """大客户折扣按部门整批扫描量判定：数据平台 353.5 GB 不触发。"""
    dirty = dirty_batch(store, settings)
    bills = {bill["query_id"]: bill
             for bill in store.load_bills(dirty["batch_id"])}
    assert "R_VOLUME_DISCOUNT" not in bills["q1001"]["rules"]
    assert bills["q1001"]["cost_cents"] == X.COST_CENTS["q1001"]
    assert "R_VOLUME_DISCOUNT" in bills["q1009"]["rules"]


def test_bucket_endpoints_put_22_in_night_and_06_in_day():
    """时段边界含起点、不含终点：22:00 起整段夜价，06:00 起整段日价。"""
    night = _preview(started_at="2026-11-05T22:00:00+08:00", duration_s="3600",
                     scan_size="10")
    assert [item["rate"] for item in night["buckets"]] == [str(X.NIGHT_RATE)]
    assert night["hour_cost_cents"] == 21
    day = _preview(started_at="2026-11-05T06:00:00+08:00", duration_s="3600",
                   scan_size="10")
    assert [item["rate"] for item in day["buckets"]] == [str(X.DAY_RATE)]
    assert day["hour_cost_cents"] == 42
    early = _preview(started_at="2026-11-05T05:00:00+08:00", duration_s="7200",
                     scan_size="10")
    assert [item["rate"] for item in early["buckets"]] == [
        str(X.NIGHT_RATE), str(X.DAY_RATE)]
    assert early["hour_cost_cents"] == 63
    assert early["cost_cents"] == 91
