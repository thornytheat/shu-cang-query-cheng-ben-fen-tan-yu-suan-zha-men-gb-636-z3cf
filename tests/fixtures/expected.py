"""样例期望值：先按单价表与规则手工推算，再写死在这里。

所有金额都是整数分，避免测试跟着实现走。
"""
from __future__ import annotations

from decimal import Decimal

# ---- 单价（元） ----
PRICE_PER_GB = Decimal("0.0280")
DAY_RATE = Decimal("0.4200")
NIGHT_RATE = Decimal("0.2100")
CONC_SURCHARGE = Decimal("0.1500")
VOLUME_FACTOR = Decimal("0.90")
MIN_CHARGE_CENTS = 1

# ---- 单条查询成本（不走大客户折扣的价目试算）----
CASE_DAY_CENTS = 322        # 100 GB × 0.028 + 1h × 0.42
CASE_NIGHT_CENTS = 161      # 50 GB × 0.028 + 1h × 0.21
CASE_BOUNDARY_CENTS = 60    # 05:30 起 1 小时：0.5h 夜间 + 0.5h 白天 + 10 GB
CASE_CONCURRENCY_CENTS = 382  # 100 GB + 1h 白天 + 4 并发 × 1h × 0.15
CASE_FULLWIDTH_GB = Decimal("128.000000")
CASE_THOUSANDS_GB = Decimal("1.000000")
CASE_MIXED_UNITS_GB = Decimal("1025.000977")  # 1MB 折算到 6 位小数

# ---- 脏样例整批（bills_2026_11_dirty.csv）----
ROWS_DATA = 19
ROWS_VALID = 10
ROWS_QUARANTINE = 7
ROWS_DEDUP = 1
ROWS_EMPTY = 1
BLANK_LINES = 1
SEGMENTS = 2
DUPLICATE_HEADERS = 1

TOTAL_CENTS = 7315
ALLOCATED_CENTS = 7182
UNALLOCATED_CENTS = 133
KEPT_CENTS = 1087
BLOCKED_CENTS = 6095

COST_CENTS = {
    "q1001": 358,
    "q1002": 5372,
    "q1003": 13,
    "q1004": 365,
    "q1006": 25,
    "q1008": 1,
    "q1009": 729,
    "q1010": 301,
    "q1011": 133,
    "q1015": 18,
}

DEPT_ALLOCATED_CENTS = {"数据平台": 923, "风控": 6221, "营销": 38}
DEPT_KEPT_CENTS = {"数据平台": 200, "风控": 849, "营销": 38}
DEPT_BLOCKED_CENTS = {"数据平台": 723, "风控": 5372, "营销": 0}
DEPT_BUDGET_CENTS = {"数据平台": 400, "风控": 5000, "营销": 100}

SHARE_Q1010 = {"数据平台": 181, "风控": 120}
SHARED_QUERY_COUNT = 2

# ---- 隔离原因码 → 行号 ----
QUARANTINE = {
    "Q_TRUNCATED_ROW": [22],
    "Q_COLUMN_COUNT": [21],
    "Q_BAD_TIME": [17],
    "Q_BAD_UNIT": [18],
    "Q_SENTINEL": [9],
    "Q_DUP_CONFLICT": [6, 7],
}

# ---- 预算表 ----
BUDGETS_CENTS = {"数据平台": 400, "风控": 5000, "营销": 100, "平台组": 120000}
BUDGET_ROWS = 5
