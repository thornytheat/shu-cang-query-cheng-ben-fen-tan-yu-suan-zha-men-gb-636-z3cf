"""SQLite 存储：批次、账单行、隔离、分摊份额、部门预算、闸门决策、阶段、合计、清单。

约定：
- 金额一律整数「分」（INTEGER），不落 float，避免二进制误差与尾差；
- 扫描量/权重落 TEXT（Decimal 字符串），读回再 Decimal 化；
- 一次运行的所有行在同一个事务里「先删后插」，失败整体回滚，不留半批数据；
- 页面永远只展示一个批次，读数按 batch_id 过滤。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    batch_no TEXT NOT NULL,
    check_digit TEXT NOT NULL,
    batch_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    budget_source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    input_digest TEXT NOT NULL,
    budget_digest TEXT NOT NULL DEFAULT '',
    total_cents INTEGER NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_batches_key ON batches(batch_key, status);
CREATE TABLE IF NOT EXISTS bills (
    batch_id TEXT NOT NULL, query_id TEXT NOT NULL, row_ref TEXT NOT NULL,
    line_no INTEGER NOT NULL, segment INTEGER NOT NULL, dept TEXT NOT NULL,
    project TEXT NOT NULL, started_at TEXT NOT NULL, duration_s TEXT NOT NULL,
    scan_gb TEXT NOT NULL, scan_unit TEXT NOT NULL, scan_raw TEXT NOT NULL,
    concurrency INTEGER NOT NULL DEFAULT 1, value_score TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL DEFAULT 'BILLED', cost_cents INTEGER NOT NULL DEFAULT 0,
    scan_cost_cents INTEGER NOT NULL DEFAULT 0, hour_cost_cents INTEGER NOT NULL DEFAULT 0,
    addon_cents INTEGER NOT NULL DEFAULT 0, buckets_json TEXT NOT NULL DEFAULT '[]',
    rules_json TEXT NOT NULL DEFAULT '[]', flags_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS quarantine (
    batch_id TEXT NOT NULL, code TEXT NOT NULL, stage TEXT NOT NULL, level TEXT NOT NULL,
    row_ref TEXT, field TEXT, raw_value TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS shares (
    batch_id TEXT NOT NULL, query_id TEXT NOT NULL, dept TEXT NOT NULL,
    kind TEXT NOT NULL, weight TEXT NOT NULL, share_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS depts (
    batch_id TEXT NOT NULL, dept TEXT NOT NULL, budget_cents INTEGER,
    allocated_cents INTEGER NOT NULL, kept_cents INTEGER NOT NULL DEFAULT 0,
    blocked_cents INTEGER NOT NULL DEFAULT 0, gate_status TEXT NOT NULL DEFAULT 'KEEP',
    scan_gb TEXT NOT NULL DEFAULT '0'
);
CREATE TABLE IF NOT EXISTS gates (
    batch_id TEXT NOT NULL, dept TEXT NOT NULL, query_id TEXT NOT NULL,
    value_score TEXT NOT NULL, ratio TEXT NOT NULL, cost_cents INTEGER NOT NULL,
    cumulative_cents INTEGER NOT NULL, decision TEXT NOT NULL,
    rank_no INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS stages (
    batch_id TEXT NOT NULL, stage TEXT NOT NULL, in_count INTEGER NOT NULL,
    out_count INTEGER NOT NULL, quarantine_count INTEGER NOT NULL,
    status TEXT NOT NULL, elapsed_ms INTEGER NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS totals (
    batch_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manifests (
    batch_id TEXT PRIMARY KEY, json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appeals (
    batch_id TEXT NOT NULL, appeal_id TEXT NOT NULL, query_id TEXT NOT NULL,
    dept TEXT NOT NULL, target_dept TEXT NOT NULL, fix_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL, code TEXT NOT NULL, version INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS snapshots (
    batch_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL,
    created_at TEXT NOT NULL, summary_json TEXT NOT NULL,
    state_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots ON snapshots(batch_id, version);
"""

TABLES = ("bills", "quarantine", "shares", "depts", "gates", "stages", "totals",
          "appeals", "snapshots")
BILL_COST_COLUMNS = ("query_id", "dept", "started_at", "duration_s", "scan_gb",
                     "scan_unit", "scan_raw", "status", "cost_cents",
                     "scan_cost_cents", "hour_cost_cents", "addon_cents",
                     "rules_json", "buckets_json")

_UTC = dt.timezone.utc


def _json_value(raw, fallback):
    """列里的 JSON 文本 → 对象；已经是对象就原样返回，坏了给兜底值。"""
    if isinstance(raw, (list, dict)):
        return raw
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class Store:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- 批次 ----------
    def find_done_by_key(self, batch_key: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE batch_key=? AND status='done' "
                "ORDER BY created_at, rowid LIMIT 1", (batch_key,)).fetchone()
        return dict(row) if row else None

    def get_batch(self, batch_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id=?",
                               (batch_id,)).fetchone()
        return dict(row) if row else None

    def latest_batch(self, status: str = "done") -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE status=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (status,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC "
                    "LIMIT 1").fetchone()
        return dict(row) if row else None

    def list_batches(self, limit: int = 30) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def next_seq(self, eval_date_compact: str) -> int:
        """同一天内的下一个批次序号（按已有批次号的最大序号 +1）。"""
        prefix = f"CS-{eval_date_compact}-"
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT batch_no FROM batches WHERE batch_no LIKE ?", (prefix + "%",)
            ).fetchall()
        seqs = []
        for row in rows:
            tail = str(row["batch_no"]).rsplit("-", 1)[-1]
            if tail.isdigit():
                seqs.append(int(tail))
        return (max(seqs) + 1) if seqs else 1

    # ---------- 一次运行的整批落库 ----------
    def save_run(self, *, batch: dict, bills: list[dict], quarantine: list[dict],
                 shares: list[dict], depts: list[dict], gates: list[dict],
                 stages: list[dict], totals: dict, manifest: dict) -> None:
        with self.connect() as conn:
            for table in TABLES:
                conn.execute(f"DELETE FROM {table} WHERE batch_id=?",
                             (batch["batch_id"],))
            self._insert_batch(conn, batch)
            for b in bills:
                conn.execute(
                    """INSERT INTO bills (batch_id,query_id,row_ref,line_no,segment,dept,
                        project,started_at,duration_s,scan_gb,scan_unit,scan_raw,concurrency,
                        value_score,status,cost_cents,scan_cost_cents,hour_cost_cents,
                        addon_cents,buckets_json,rules_json,flags_json,raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], b["query_id"], b["row_ref"], b["line_no"],
                     b["segment"], b["dept"], b["project"], b["started_at"],
                     str(b["duration_s"]), str(b["scan_gb"]), b["scan_unit"],
                     b["scan_raw"], b["concurrency"], str(b["value_score"]),
                     b["status"], b["cost_cents"], b["scan_cost_cents"],
                     b["hour_cost_cents"], b["addon_cents"],
                     json.dumps(b.get("buckets", []), ensure_ascii=False),
                     json.dumps(b.get("rules", []), ensure_ascii=False),
                     json.dumps(b.get("flags", []), ensure_ascii=False),
                     json.dumps(b.get("raw", {}), ensure_ascii=False)))
            for q in quarantine:
                conn.execute(
                    """INSERT INTO quarantine (batch_id,code,stage,level,row_ref,field,
                        raw_value,detail) VALUES (?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], q["code"], q["stage"], q["level"],
                     q.get("row_ref"), q.get("field"), q.get("raw_value"),
                     q.get("detail")))
            for s in shares:
                conn.execute(
                    """INSERT INTO shares (batch_id,query_id,dept,kind,weight,share_cents)
                       VALUES (?,?,?,?,?,?)""",
                    (batch["batch_id"], s["query_id"], s["dept"], s["kind"],
                     str(s["weight"]), s["share_cents"]))
            for d in depts:
                conn.execute(
                    """INSERT INTO depts (batch_id,dept,budget_cents,allocated_cents,
                        kept_cents,blocked_cents,gate_status,scan_gb)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], d["dept"], d["budget_cents"],
                     d["allocated_cents"], d["kept_cents"], d["blocked_cents"],
                     d["gate_status"], str(d["scan_gb"])))
            for g in gates:
                conn.execute(
                    """INSERT INTO gates (batch_id,dept,query_id,value_score,ratio,
                        cost_cents,cumulative_cents,decision,rank_no)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], g["dept"], g["query_id"],
                     str(g["value_score"]), str(g["ratio"]), g["cost_cents"],
                     g["cumulative_cents"], g["decision"], g["rank_no"]))
            for s in stages:
                conn.execute(
                    """INSERT INTO stages (batch_id,stage,in_count,out_count,
                        quarantine_count,status,elapsed_ms,note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], s["stage"], s["in_count"], s["out_count"],
                     s["quarantine_count"], s["status"], s["elapsed_ms"],
                     s.get("note", "")))
            for key, value in totals.items():
                conn.execute("INSERT INTO totals (batch_id,key,value) VALUES (?,?,?)",
                             (batch["batch_id"], key, str(value)))
            conn.execute(
                "INSERT INTO manifests (batch_id,json) VALUES (?,?) "
                "ON CONFLICT(batch_id) DO UPDATE SET json=excluded.json",
                (batch["batch_id"], json.dumps(manifest, ensure_ascii=False)))

    def _insert_batch(self, conn, batch: dict) -> None:
        conn.execute(
            """INSERT INTO batches (batch_id,batch_no,check_digit,batch_key,run_id,source,
                budget_source,status,version,created_at,finished_at,input_digest,
                budget_digest,total_cents,row_count,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(batch_id) DO UPDATE SET
                 status=excluded.status, finished_at=excluded.finished_at,
                 version=excluded.version, total_cents=excluded.total_cents,
                 row_count=excluded.row_count, note=excluded.note""",
            (batch["batch_id"], batch["batch_no"], batch["check_digit"],
             batch["batch_key"], batch["run_id"], batch["source"],
             batch.get("budget_source", ""), batch["status"],
             int(batch.get("version", 1)), batch["created_at"],
             batch.get("finished_at"), batch["input_digest"],
             batch.get("budget_digest", ""), int(batch.get("total_cents", 0)),
             int(batch.get("row_count", 0)), batch.get("note", "")))

    def save_attempt(self, *, batch: dict, stages: list[dict], note: str = "") -> None:
        """失败尝试：只写批次与阶段，不动任何已有批次的明细。"""
        with self.connect() as conn:
            self._insert_batch(conn, {**batch, "status": "failed",
                                     "note": note or batch.get("note", "")})
            conn.execute("DELETE FROM stages WHERE batch_id=?", (batch["batch_id"],))
            for s in stages:
                conn.execute(
                    """INSERT INTO stages (batch_id,stage,in_count,out_count,
                        quarantine_count,status,elapsed_ms,note)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (batch["batch_id"], s["stage"], s["in_count"], s["out_count"],
                     s["quarantine_count"], s["status"], s["elapsed_ms"],
                     s.get("note", "")))

    # ---------- 闸门重算：只替换 depts / gates / totals / version ----------
    def save_gate(self, *, batch_id: str, depts: list[dict], gates: list[dict],
                  totals: dict, stage: dict, exit_codes: list[dict]) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM batches WHERE batch_id=?",
                               (batch_id,)).fetchone()
            version = int(row["version"]) + 1 if row else 1
            conn.execute("DELETE FROM depts WHERE batch_id=?", (batch_id,))
            conn.execute("DELETE FROM gates WHERE batch_id=?", (batch_id,))
            conn.execute("UPDATE batches SET version=? WHERE batch_id=?",
                         (version, batch_id))
            self._insert_depts(conn, batch_id, depts)
            self._insert_gates(conn, batch_id, gates)
            self._set_totals(conn, batch_id, totals)
            conn.execute("DELETE FROM quarantine WHERE batch_id=? AND stage='gate'",
                         (batch_id,))
            self._insert_quarantine(conn, batch_id, exit_codes)
            self._insert_stage(conn, batch_id, stage)
        return version

    # ---------- 版本快照：每次结论变化都留一份可回滚的完整状态 ----------
    def state_payload(self, batch_id: str) -> dict:
        return {
            "depts": self.load_depts(batch_id),
            "gates": self.load_gates(batch_id),
            "shares": self.load_shares(batch_id),
            "totals": self.load_totals(batch_id),
            "bills": self.load_bill_costs(batch_id),
        }

    def load_bill_costs(self, batch_id: str) -> list[dict]:
        """只取回写版本时需要的列（不含 raw_json，快照体积可控）。"""
        rows = self._all(
            f"SELECT {', '.join(BILL_COST_COLUMNS)} FROM bills WHERE batch_id=? "
            "ORDER BY line_no", (batch_id,))
        for row in rows:
            row["rules"] = _json_value(row.pop("rules_json"), [])
            row["buckets"] = _json_value(row.pop("buckets_json"), [])
        return rows

    def capture_snapshot(self, batch_id: str, kind: str) -> dict:
        """把当前 depts/gates/shares/totals/bills 记成当前版本号的快照。"""
        state = self.state_payload(batch_id)
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM batches WHERE batch_id=?",
                               (batch_id,)).fetchone()
            version = int(row["version"]) if row else 1
            self._insert_snapshot(conn, batch_id, version, kind, state)
        return state

    def load_snapshots(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT version, kind, created_at, summary_json FROM snapshots "
            "WHERE batch_id=? ORDER BY version", (batch_id,))
        return [{"version": int(r["version"]), "kind": r["kind"],
                 "created_at": r["created_at"],
                 "summary": _json_value(r["summary_json"], {})}
                for r in rows]

    def load_snapshot(self, batch_id: str, version: int) -> dict | None:
        rows = self._all(
            "SELECT state_json FROM snapshots WHERE batch_id=? AND version=?",
            (batch_id, int(version)))
        return _json_value(rows[0]["state_json"], {}) if rows else None

    def load_appeals(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM appeals WHERE batch_id=? ORDER BY version, appeal_id",
            (batch_id,))
        for row in rows:
            row["fix"] = _json_value(row.pop("fix_json"), {})
            row["version"] = int(row["version"])
        return rows

    def save_version_state(self, *, batch_id: str, expected_version: int | None,
                           shares: list[dict], depts: list[dict], gates: list[dict],
                           totals: dict, stage: dict, quarantine: list[dict],
                           snapshot_kind: str, bills: list[dict] | None = None,
                           appeals: list[dict] | None = None) -> int:
        """追加一个新版本：替换 shares/depts/gates/totals，可选回写账单成本，
        并落一条快照。版本号只增不减，回滚也是新版本。"""
        from . import errors

        bill_state = bills if bills is not None else self.load_bill_costs(batch_id)
        with self.connect() as conn:
            row = conn.execute("SELECT version FROM batches WHERE batch_id=?",
                               (batch_id,)).fetchone()
            current = int(row["version"]) if row else 1
            if expected_version is not None and int(expected_version) != current:
                raise errors.StaleVersion(
                    f"提交版本 {expected_version} 已落后于当前版本 {current}，请刷新后重试",
                    detail={"current_version": current,
                            "expected_version": int(expected_version)})
            version = current + 1
            conn.execute("UPDATE batches SET version=? WHERE batch_id=?",
                         (version, batch_id))
            for table in ("shares", "depts", "gates"):
                conn.execute(f"DELETE FROM {table} WHERE batch_id=?", (batch_id,))
            self._insert_shares(conn, batch_id, shares)
            self._insert_depts(conn, batch_id, depts)
            self._insert_gates(conn, batch_id, gates)
            self._set_totals(conn, batch_id, totals)
            for bill in bills or []:
                self._update_bill(conn, batch_id, bill)
            for appeal in appeals or []:
                conn.execute(
                    """INSERT INTO appeals (batch_id,appeal_id,query_id,dept,
                        target_dept,fix_json,status,code,version,note)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (batch_id, appeal["appeal_id"], appeal["query_id"],
                     appeal["dept"], appeal["target_dept"],
                     json.dumps(appeal.get("fix") or {}, ensure_ascii=False),
                     appeal["status"], appeal["code"], version,
                     appeal.get("note", "")))
            conn.execute("DELETE FROM quarantine WHERE batch_id=? AND stage=?",
                         (batch_id, stage["stage"]))
            self._insert_quarantine(conn, batch_id, quarantine)
            self._insert_stage(conn, batch_id, stage)
            self._insert_snapshot(conn, batch_id, version, snapshot_kind,
                                  {**{"depts": depts, "gates": gates,
                                      "shares": shares, "totals": totals},
                                   "bills": bill_state})
        return version

    def _insert_snapshot(self, conn, batch_id: str, version: int, kind: str,
                         state: dict) -> None:
        summary = {
            "depts": {str(row["dept"]): int(row["allocated_cents"])
                      for row in state.get("depts", [])},
            "totals": {str(k): str(v) for k, v in state.get("totals", {}).items()},
            "bills": len(state.get("bills", [])),
        }
        conn.execute(
            "INSERT INTO snapshots (batch_id,version,kind,created_at,summary_json,"
            "state_json) VALUES (?,?,?,?,?,?)",
            (batch_id, int(version), kind,
             dt.datetime.now(_UTC).isoformat(timespec="seconds"),
             json.dumps(summary, ensure_ascii=False),
             json.dumps(state, ensure_ascii=False, default=str)))

    def _update_bill(self, conn, batch_id: str, bill: dict) -> None:
        conn.execute(
            """UPDATE bills SET dept=?, started_at=?, duration_s=?, scan_gb=?,
                scan_unit=?, scan_raw=?, status=?, cost_cents=?, scan_cost_cents=?,
                hour_cost_cents=?, addon_cents=?, rules_json=?, buckets_json=?
               WHERE batch_id=? AND query_id=?""",
            (bill["dept"], bill["started_at"], str(bill["duration_s"]),
             str(bill["scan_gb"]), bill["scan_unit"], bill["scan_raw"],
             bill["status"], int(bill["cost_cents"]), int(bill["scan_cost_cents"]),
             int(bill["hour_cost_cents"]), int(bill["addon_cents"]),
             json.dumps(_json_value(bill.get("rules"), []), ensure_ascii=False),
             json.dumps(_json_value(bill.get("buckets"), []), ensure_ascii=False),
             batch_id, bill["query_id"]))

    def _insert_shares(self, conn, batch_id: str, shares: list[dict]) -> None:
        for s in shares:
            conn.execute(
                """INSERT INTO shares (batch_id,query_id,dept,kind,weight,share_cents)
                   VALUES (?,?,?,?,?,?)""",
                (batch_id, s["query_id"], s["dept"], s["kind"], str(s["weight"]),
                 int(s["share_cents"])))

    def _insert_depts(self, conn, batch_id: str, depts: list[dict]) -> None:
        for d in depts:
            conn.execute(
                """INSERT INTO depts (batch_id,dept,budget_cents,allocated_cents,
                    kept_cents,blocked_cents,gate_status,scan_gb)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (batch_id, d["dept"],
                 None if d.get("budget_cents") is None else int(d["budget_cents"]),
                 int(d["allocated_cents"]), int(d["kept_cents"]),
                 int(d["blocked_cents"]), d["gate_status"], str(d["scan_gb"])))

    def _insert_gates(self, conn, batch_id: str, gates: list[dict]) -> None:
        for g in gates:
            conn.execute(
                """INSERT INTO gates (batch_id,dept,query_id,value_score,ratio,
                    cost_cents,cumulative_cents,decision,rank_no)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (batch_id, g["dept"], g["query_id"], str(g["value_score"]),
                 str(g["ratio"]), int(g["cost_cents"]), int(g["cumulative_cents"]),
                 g["decision"], int(g["rank_no"])))

    def _set_totals(self, conn, batch_id: str, totals: dict) -> None:
        """整组替换：旧版本才有（如 rollback_from）的键不留在新版本里。"""
        conn.execute("DELETE FROM totals WHERE batch_id=?", (batch_id,))
        for key, value in totals.items():
            conn.execute(
                "INSERT INTO totals (batch_id,key,value) VALUES (?,?,?)",
                (batch_id, key, str(value)))

    def _insert_quarantine(self, conn, batch_id: str, rows: list[dict]) -> None:
        for q in rows:
            conn.execute(
                """INSERT INTO quarantine (batch_id,code,stage,level,row_ref,field,
                    raw_value,detail) VALUES (?,?,?,?,?,?,?,?)""",
                (batch_id, q["code"], q["stage"], q["level"], q.get("row_ref"),
                 q.get("field"), q.get("raw_value"), q.get("detail")))

    def _insert_stage(self, conn, batch_id: str, stage: dict) -> None:
        conn.execute(
            """INSERT INTO stages (batch_id,stage,in_count,out_count,
                quarantine_count,status,elapsed_ms,note) VALUES (?,?,?,?,?,?,?,?)""",
            (batch_id, stage["stage"], stage["in_count"], stage["out_count"],
             stage["quarantine_count"], stage["status"], stage["elapsed_ms"],
             stage.get("note", "")))

    # ---------- 读取 ----------
    def load_bills(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM bills WHERE batch_id=? ORDER BY line_no", (batch_id,))
        for r in rows:
            for key in ("buckets", "rules", "flags"):
                r[key] = json.loads(r.pop(f"{key}_json") or "[]")
            r["raw"] = json.loads(r.pop("raw_json") or "{}")
            r["scan_gb"] = Decimal(r["scan_gb"])
            r["value_score"] = Decimal(r["value_score"])
            r["duration_s"] = Decimal(r["duration_s"])
        return rows

    def load_quarantine(self, batch_id: str) -> list[dict]:
        return self._all(
            "SELECT * FROM quarantine WHERE batch_id=? "
            "ORDER BY stage, code, row_ref", (batch_id,))

    def load_shares(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM shares WHERE batch_id=? ORDER BY query_id, kind, dept",
            (batch_id,))
        for r in rows:
            r["weight"] = Decimal(r["weight"])
        return rows

    def load_depts(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM depts WHERE batch_id=? ORDER BY allocated_cents DESC, dept",
            (batch_id,))
        for r in rows:
            r["scan_gb"] = Decimal(r["scan_gb"])
        return rows

    def load_gates(self, batch_id: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM gates WHERE batch_id=? ORDER BY dept, rank_no", (batch_id,))
        for r in rows:
            r["value_score"] = Decimal(r["value_score"])
            r["ratio"] = Decimal(r["ratio"])
        return rows

    def load_stages(self, batch_id: str) -> list[dict]:
        from . import config
        rows = self._all("SELECT * FROM stages WHERE batch_id=? ORDER BY rowid",
                         (batch_id,))
        for row in rows:
            row["label"] = config.STAGE_LABELS.get(row["stage"], row["stage"])
        return rows

    def load_totals(self, batch_id: str) -> dict:
        rows = self._all("SELECT key, value FROM totals WHERE batch_id=?", (batch_id,))
        return {r["key"]: r["value"] for r in rows}

    def load_manifest(self, batch_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT json FROM manifests WHERE batch_id=?",
                               (batch_id,)).fetchone()
        return json.loads(row["json"]) if row else None

    def count_rows(self, batch_id: str) -> dict:
        out = {}
        with self.connect() as conn:
            for table in ("bills", "quarantine", "shares", "depts", "gates"):
                out[table] = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE batch_id=?",
                    (batch_id,)).fetchone()["n"]
        return out

    def _all(self, sql: str, args: tuple) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
