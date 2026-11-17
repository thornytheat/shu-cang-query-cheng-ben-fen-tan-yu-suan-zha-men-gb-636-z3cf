"""收据式清单：等宽对齐的批次落款，含批次号、校验位与骑缝线记号。"""
from __future__ import annotations

from app import config

RULE = "-" * 48
PERF = "- - - - - - - - - - - - - - - - - - - - - - - -"


def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"{cents / 100:,.2f}"


def line(label: str, value: str, width: int = 14) -> str:
    pad = width - _width(label)
    return f"{label}{' ' * max(pad, 1)}{value}"


def _width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def build(*, batch: dict, totals: dict, counters: dict, depts: list[dict]) -> str:
    rows = [
        "数仓查询成本分摊单",
        RULE,
        line("批次号", batch["batch_no"]),
        line("校验位", str(batch["check_digit"])),
        line("数据版本", batch["input_digest"][:12]),
        line("规则版本", batch["rule_version"] if "rule_version" in batch
             else config.RULE_VERSION),
        line("时区", "Asia/Shanghai(+08:00)"),
        RULE,
        line("数据行", str(counters.get("rows_data", 0))),
        line("有效行", str(counters.get("rows_valid", 0))),
        line("隔离行", str(counters.get("rows_quarantine", 0))),
        line("去重行", str(counters.get("rows_dedup", 0))),
        RULE,
        line("总额", f"{money(int(totals.get('amount_total', 0)))} {config.MONEY_UNIT}"),
        line("已分摊", f"{money(int(totals.get('amount_allocated', 0)))} {config.MONEY_UNIT}"),
        line("未分摊", f"{money(int(totals.get('amount_unallocated', 0)))} {config.MONEY_UNIT}"),
        line("预算内", f"{money(int(totals.get('amount_kept', 0)))} {config.MONEY_UNIT}"),
        line("被截断", f"{money(int(totals.get('amount_blocked', 0)))} {config.MONEY_UNIT}"),
    ]
    if depts:
        rows.append(RULE)
        rows.append("部门           分摊金额      预算")
        for dept in depts[:12]:
            budget = "—" if dept.get("budget_cents") is None else money(
                int(dept["budget_cents"]))
            rows.append(f"{_pad(dept['dept'], 14)}{money(int(dept['allocated_cents'])):>12}"
                        f"{budget:>12}")
    rows.extend([PERF, "骑缝线以上为制表联，以下为归档联", PERF])
    return "\n".join(rows)


def _pad(text: str, width: int) -> str:
    pad = width - _width(text)
    return text + " " * max(pad, 0)
