"""申诉规则：每条申诉要改哪条查询、改成什么，同一查询的多条怎么定序。

纯函数模块：入参是接口收到的申诉条目和本批账单行，出参是「逐条判定」（采纳 /
合并 / 冲突 / 拒绝）与要施加的改判；不读库、不写库、不算金额。

定序规则（决定结果可复现）：
- 只认 APPEAL_FIX_FIELDS 里登记的列，改别的字段直接拒，避免绕过采信与质量；
- 同一 query_id 的条目按出现顺序处理：改判完全一样的算一次（合并），
  改判目标不同的保留最早一条，其余记冲突——不按时间戳猜谁后到；
- 查询不在本批、改判部门为空、时间/时长/扫描量解析不了，都在这一步拒。
"""
from __future__ import annotations

from decimal import Decimal

from app import config, errors, reasons
from app.modules.m2_billing import coerce, units

APPLIED = "APPLIED"
REJECTED = "REJECTED"
MERGED = "MERGED"


def _blank(index: int) -> dict:
    return {"index": index, "appeal_id": "", "query_id": "", "claim_dept": "",
            "dept": "", "target_dept": "", "fix": {}, "status": "",
            "code": "", "note": ""}


def parse_entries(raw) -> list[dict]:
    """整批形状不对就 400；单条不合法只拒这一条，其余照常判定。"""
    if not isinstance(raw, list):
        raise errors.BadRequest("appeals 必须是数组", code="E_BAD_REQUEST")
    if not raw:
        raise errors.BadRequest("appeals 不能为空", code="E_BAD_REQUEST")
    return [_parse_one(item, index) for index, item in enumerate(raw, start=1)]


def _parse_one(raw, index: int) -> dict:
    verdict = _blank(index)
    if not isinstance(raw, dict):
        verdict.update(status=REJECTED, code="Q_APPEAL_BAD_FIX",
                       note="申诉条目必须是对象")
        return verdict
    query_id = coerce.clean_text(raw.get("query_id"))
    claim_dept = coerce.clean_text(raw.get("dept"))
    target = coerce.clean_text(raw.get("target_dept"))
    verdict.update(appeal_id=coerce.clean_text(raw.get("appeal_id")),
                   query_id=query_id, claim_dept=claim_dept, target_dept=target)
    if not query_id or coerce.is_sentinel(query_id):
        verdict.update(status=REJECTED, code="Q_APPEAL_BAD_FIX",
                       note="缺少 query_id")
        return verdict
    fix, error = parse_fix(raw.get("fix"))
    if error:
        verdict.update(status=REJECTED, code="Q_APPEAL_BAD_FIX", note=error)
        return verdict
    fix_dept = fix.pop("dept", "")
    if fix_dept:
        if target and target != fix_dept:
            verdict.update(status=REJECTED, code="Q_APPEAL_BAD_FIX",
                           note=f"target_dept={target} 与 fix.dept={fix_dept} 不一致")
            return verdict
        target = fix_dept
    if not target or coerce.is_sentinel(target):
        verdict.update(status=REJECTED, code="Q_APPEAL_BAD_FIX",
                       note="缺少改判到的部门")
        return verdict
    verdict.update(target_dept=target, fix=fix)
    return verdict


def parse_fix(raw) -> tuple[dict, str]:
    """返回 (改判字段, 错误说明)；错误说明非空表示这条申诉的改判不成立。"""
    if raw is None:
        return {}, ""
    if not isinstance(raw, dict):
        return {}, "fix 必须是对象"
    fix: dict = {}
    for key, value in raw.items():
        name = str(key)
        if name not in config.APPEAL_FIX_FIELDS:
            return {}, f"不能改字段 {name}"
        text = coerce.clean_text(value)
        if name == "dept":
            if not text or coerce.is_sentinel(text):
                return {}, "fix.dept 为空"
            fix["dept"] = text
        elif name == "started_at":
            try:
                fix["started_at"] = coerce.parse_instant(text, name).isoformat()
            except coerce.CoerceError:
                return {}, f"时间不可解析 {text}"
        elif name == "duration_s":
            value_dec = _decimal(text, name)
            if value_dec is None or value_dec <= 0:
                return {}, f"时长不合法 {text}"
            fix["duration_s"] = str(value_dec)
        elif name == "scan_size":
            value_dec = _decimal(text, name)
            if value_dec is None or value_dec < 0:
                return {}, f"扫描量不合法 {text}"
            fix["scan_size"] = str(value_dec)
        elif name == "scan_unit":
            try:
                fix["scan_unit"] = units.normalize_unit(text)
            except units.UnitError:
                return {}, f"单位不可识别 {text}"
    if "scan_unit" in fix and "scan_size" not in fix:
        return {}, "只改单位不改扫描量会让数值换义"
    return fix, ""


def _decimal(text: str, field: str) -> Decimal | None:
    try:
        return coerce.parse_decimal(text, field)
    except coerce.CoerceError:
        return None


def signature(verdict: dict) -> tuple:
    """两条申诉是否同一个改判：部门 + 归一后的字段值，逐字比较。"""
    fix = verdict.get("fix") or {}
    return (verdict.get("target_dept", ""),
            tuple(sorted((str(k), str(v)) for k, v in fix.items())))


def dedupe(verdicts: list[dict]) -> tuple[list[dict], list[dict]]:
    """同一 query_id 的条目去重，返回 (候选判定, 证据行)。"""
    kept: list[dict] = []
    issues: list[dict] = []
    anchor_by_query: dict[str, dict] = {}
    for verdict in verdicts:
        if verdict["status"] == REJECTED:
            continue
        query_id = verdict["query_id"]
        anchor = anchor_by_query.get(query_id)
        if anchor is None:
            anchor_by_query[query_id] = verdict
            kept.append(verdict)
            continue
        if signature(anchor) == signature(verdict):
            verdict.update(status=MERGED, code="Q_APPEAL_MERGED")
            issues.append(reasons.evidence(
                "Q_APPEAL_MERGED", row_ref=query_id, field="query_id",
                raw_value=query_id, detail="同查询同样改判，只算一次"))
        else:
            verdict.update(status=REJECTED, code="Q_APPEAL_CONFLICT")
            issues.append(reasons.evidence(
                "Q_APPEAL_CONFLICT", row_ref=query_id, field="target_dept",
                raw_value=verdict["target_dept"],
                detail=f"保留第 {anchor['index']} 条改判到 {anchor['target_dept']}"))
    return kept, issues


def corrected_rows(bills: list[dict], verdicts: list[dict]) -> tuple[dict, list[dict]]:
    """把采纳的申诉落成「查询 → 新字段」，同时把不成立的改成拒绝。"""
    known = {bill["query_id"]: bill for bill in bills}
    corrections: dict[str, dict] = {}
    issues: list[dict] = []
    for verdict in verdicts:
        query_id = verdict["query_id"]
        bill = known.get(query_id)
        if bill is None:
            verdict.update(status=REJECTED, code="Q_APPEAL_NO_QUERY")
            issues.append(reasons.evidence(
                "Q_APPEAL_NO_QUERY", row_ref=query_id, field="query_id",
                raw_value=query_id, detail="本批次没有这条查询"))
            continue
        verdict["dept"] = bill["dept"]
        verdict["status"] = APPLIED
        if verdict["claim_dept"] and verdict["claim_dept"] != bill["dept"]:
            verdict["note"] = (verdict["note"] or
                               f"申诉部门 {verdict['claim_dept']} 与账单部门"
                               f" {bill['dept']} 不同，证据行按账单部门记")
        corrections[query_id] = {"dept": verdict["target_dept"], **verdict["fix"]}
    return corrections, issues


def applied(verdicts: list[dict]) -> list[dict]:
    return [item for item in verdicts if item["status"] == APPLIED]


def parse_issues(verdicts: list[dict]) -> list[dict]:
    """解析阶段就拒的条目也要留证据行，页面与导出能看到原因与序号。"""
    return [reasons.evidence(
        item["code"], row_ref=item.get("query_id") or None, field="appeal",
        raw_value=str(item.get("index")), detail=item.get("note") or "条目不成立")
        for item in verdicts
        if item["status"] == REJECTED and item["code"]]
