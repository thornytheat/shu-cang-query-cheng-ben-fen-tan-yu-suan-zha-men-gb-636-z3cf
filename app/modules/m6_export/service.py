"""M6 编排：把批次明细拼成可复核的导出物（CSV / JSON / 收据文本）。"""
from __future__ import annotations

from app import config, errors, manifest
from app.modules.m3_quality import quarantine as quality_view

from . import csv_out, json_out, receipt

BILL_FIELDS = ("row_ref", "line_no", "segment", "query_id", "dept", "project",
               "started_at", "duration_s", "scan_raw", "scan_unit", "scan_gb",
               "concurrency", "value_score", "rules", "cost_yuan", "status")
SHARE_FIELDS = ("query_id", "dept", "kind", "weight", "share_yuan")
GATE_FIELDS = ("dept", "rank_no", "query_id", "value_score", "ratio",
               "cost_yuan", "cumulative_yuan", "decision")

KINDS = ("bill", "quarantine", "shares", "gates", "manifest", "receipt")


def _yuan(cents) -> str:
    return f"{int(cents or 0) / 100:.2f}"


def bill_rows(store, batch_id: str) -> list[dict]:
    rows = []
    for bill in store.load_bills(batch_id):
        rows.append({
            "row_ref": bill["row_ref"],
            "line_no": bill["line_no"],
            "segment": bill["segment"],
            "query_id": bill["query_id"],
            "dept": bill["dept"],
            "project": bill["project"],
            "started_at": bill["started_at"],
            "duration_s": format(bill["duration_s"], "f"),
            "scan_raw": bill["scan_raw"],
            "scan_unit": bill["scan_unit"],
            "scan_gb": format(bill["scan_gb"], "f"),
            "concurrency": bill["concurrency"],
            "value_score": format(bill["value_score"], "f"),
            "rules": "|".join(bill["rules"]),
            "cost_yuan": _yuan(bill["cost_cents"]),
            "status": bill["status"],
        })
    return rows


def share_rows(store, batch_id: str) -> list[dict]:
    return [{"query_id": row["query_id"], "dept": row["dept"], "kind": row["kind"],
             "weight": format(row["weight"], "f"),
             "share_yuan": _yuan(row["share_cents"])}
            for row in store.load_shares(batch_id)]


def gate_rows(store, batch_id: str) -> list[dict]:
    return [{"dept": row["dept"], "rank_no": row["rank_no"],
             "query_id": row["query_id"],
             "value_score": format(row["value_score"], "f"),
             "ratio": format(row["ratio"], "f"),
             "cost_yuan": _yuan(row["cost_cents"]),
             "cumulative_yuan": _yuan(row["cumulative_cents"]),
             "decision": row["decision"]}
            for row in store.load_gates(batch_id)]


def build(store, settings, batch: dict, kind: str) -> dict:
    batch_id = batch["batch_id"]
    if kind not in KINDS:
        raise errors.BadRequest(f"不支持的导出类型：{kind}",
                                code="E_BAD_REQUEST",
                                detail={"kinds": list(KINDS)})
    if kind == "bill":
        return {"filename": f"{batch['batch_no']}-bill.csv",
                "media": "text/csv; charset=utf-8",
                "text": csv_out.to_csv(list(BILL_FIELDS),
                                      bill_rows(store, batch_id))}
    if kind == "quarantine":
        issues = store.load_quarantine(batch_id)
        return {"filename": f"{batch['batch_no']}-quarantine.csv",
                "media": "text/csv; charset=utf-8",
                "text": csv_out.to_csv(list(quality_view.EXPORT_FIELDS),
                                      quality_view.for_export(issues))}
    if kind == "shares":
        return {"filename": f"{batch['batch_no']}-shares.csv",
                "media": "text/csv; charset=utf-8",
                "text": csv_out.to_csv(list(SHARE_FIELDS), share_rows(store, batch_id))}
    if kind == "gates":
        return {"filename": f"{batch['batch_no']}-gates.csv",
                "media": "text/csv; charset=utf-8",
                "text": csv_out.to_csv(list(GATE_FIELDS), gate_rows(store, batch_id))}
    if kind == "manifest":
        payload = store.load_manifest(batch_id) or {}
        return {"filename": f"{batch['batch_no']}-manifest.json",
                "media": "application/json; charset=utf-8",
                "text": json_out.dumps(payload)}
    totals = store.load_totals(batch_id)
    counters = {key: int(totals.get(key, 0)) for key in
                ("rows_data", "rows_valid", "rows_quarantine", "rows_dedup",
                 "rows_empty")}
    batch_view = {**batch, "rule_version": settings.rule_version}
    text = receipt.build(batch=batch_view, totals=totals, counters=counters,
                         depts=store.load_depts(batch_id))
    return {"filename": f"{batch['batch_no']}-receipt.txt",
            "media": "text/plain; charset=utf-8", "text": text}


def listing(store) -> list[dict]:
    return [{"kind": kind, "hint": hint} for kind, hint in (
        ("bill", "账单明细：行号 + 原值 + 规则 + 金额"),
        ("quarantine", "隔离证据：原因码 + 字段 + 原值"),
        ("shares", "分摊份额：部门 + 权重 + 金额"),
        ("gates", "闸门决策：排序 + 累计 + 结论"),
        ("manifest", "运行清单：run_id + sha256 + 参数"),
        ("receipt", "收据联：批次号 + 校验位 + 骑缝线"),
    )]


def digest_of(text: str) -> str:
    return manifest.digest_bytes(text.encode("utf-8"))


def money_unit() -> str:
    return config.MONEY_UNIT
