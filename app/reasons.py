"""原因码唯一来源：每个隔离/提示都有稳定编码、级别与所属阶段。

页面、导出 CSV 与测试都按这里取值，禁止在模块里另写裸字符串。
"""
from __future__ import annotations

REASONS = {
    # 采信阶段
    "Q_CHARSET_FALLBACK": {"label": "编码回退", "level": "info", "stage": "ingest"},
    "Q_BOM_STRIPPED": {"label": "剥离 BOM", "level": "info", "stage": "ingest"},
    "Q_DUP_HEADER": {"label": "重复表头", "level": "info", "stage": "ingest"},
    "Q_TRUNCATED_ROW": {"label": "末行截断", "level": "error", "stage": "ingest"},
    "Q_COLUMN_COUNT": {"label": "列数不齐", "level": "error", "stage": "ingest"},
    "Q_EMPTY_ROW": {"label": "空行", "level": "info", "stage": "ingest"},
    # 计费阶段
    "Q_BAD_NUMBER": {"label": "数值不可解析", "level": "error", "stage": "billing"},
    "Q_BAD_UNIT": {"label": "单位不可识别", "level": "error", "stage": "billing"},
    "Q_SENTINEL": {"label": "空值哨兵", "level": "error", "stage": "billing"},
    "Q_BAD_TIME": {"label": "时间不可解析", "level": "error", "stage": "billing"},
    "Q_BAD_SHARED": {"label": "共享权重格式错", "level": "error", "stage": "billing"},
    "Q_BIG_INT": {"label": "大整数保形", "level": "info", "stage": "billing"},
    "Q_ZERO_SCAN": {"label": "零扫描量", "level": "warn", "stage": "billing"},
    # 质量阶段
    "Q_DUP_CONFLICT": {"label": "同号不同值", "level": "error", "stage": "quality"},
    "Q_DUP_IDENTICAL": {"label": "完全重复行", "level": "info", "stage": "quality"},
    "Q_MISSING_FIELD": {"label": "必填缺失", "level": "error", "stage": "quality"},
    "Q_BAD_DEPT": {"label": "部门缺失", "level": "error", "stage": "quality"},
    # 闸门阶段
    "Q_GATE_TRUNCATED": {"label": "预算截断", "level": "warn", "stage": "gate"},
    "Q_GATE_NO_BUDGET": {"label": "部门无预算", "level": "warn", "stage": "gate"},
    "Q_STALE_VERSION": {"label": "陈旧提交", "level": "error", "stage": "gate"},
    "Q_NO_BATCH": {"label": "没有批次", "level": "error", "stage": "gate"},
    "Q_UNALLOCATED": {"label": "权重全零", "level": "warn", "stage": "alloc"},
    # 申诉与回滚阶段
    "Q_APPEAL_NO_QUERY": {"label": "查询不在本批", "level": "error", "stage": "appeal"},
    "Q_APPEAL_BAD_FIX": {"label": "改判字段不合法", "level": "error", "stage": "appeal"},
    "Q_APPEAL_MERGED": {"label": "同查询申诉合并", "level": "info", "stage": "appeal"},
    "Q_APPEAL_CONFLICT": {"label": "同查询改判冲突", "level": "error", "stage": "appeal"},
    "Q_APPEAL_BUCKET": {"label": "时段重算不一致", "level": "error", "stage": "appeal"},
    "Q_APPEAL_IMBALANCE": {"label": "改判后账不平", "level": "error", "stage": "appeal"},
    "Q_APPEAL_NONE": {"label": "没有可采纳申诉", "level": "error", "stage": "appeal"},
    "Q_ROLLBACK_DONE": {"label": "已回滚", "level": "info", "stage": "appeal"},
    "Q_ROLLBACK_TARGET": {"label": "回滚目标不存在", "level": "error", "stage": "appeal"},
    "Q_ROLLBACK_SAME": {"label": "回滚到当前版本", "level": "error", "stage": "appeal"},
}

LEVEL_LABELS = {"error": "拒绝", "warn": "提示", "info": "记录"}
STAGE_ORDER = ("ingest", "billing", "quality", "alloc", "gate", "appeal", "export")


def label(code: str) -> str:
    return REASONS.get(code, {}).get("label", code or "—")


def level(code: str) -> str:
    return REASONS.get(code, {}).get("level", "info")


def stage(code: str) -> str:
    return REASONS.get(code, {}).get("stage", "ingest")


def is_error(code: str) -> bool:
    return level(code) == "error"


def evidence(code: str, *, row_ref=None, field=None, raw_value=None,
             detail: str = "") -> dict:
    """一条证据行：原因码 + 阶段 + 级别 + 位置 + 原值。

    隔离区、申诉判定与页面共用同一形状，导出列不用各模块各拼一次。
    """
    return {"code": code, "stage": stage(code), "level": level(code),
            "row_ref": row_ref, "field": field, "raw_value": raw_value,
            "detail": detail}


def catalog() -> list[dict]:
    """按阶段与编码排序的原因码清单，供页面与文档展示。"""
    return [
        {"code": code, "label": item["label"], "level": item["level"],
         "level_label": LEVEL_LABELS[item["level"]], "stage": item["stage"]}
        for code, item in sorted(
            REASONS.items(), key=lambda kv: (STAGE_ORDER.index(kv[1]["stage"]), kv[0]))
    ]


def codes_for(stage_name: str) -> list[str]:
    return [c["code"] for c in catalog() if c["stage"] == stage_name]
