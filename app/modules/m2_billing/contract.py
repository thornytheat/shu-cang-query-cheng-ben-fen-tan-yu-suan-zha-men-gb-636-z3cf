"""列契约与规则契约：列名/类型/单位/可空，规则优先级与单价表。

`ordered_rules()` 负责把规则表排成与注册顺序无关的确定顺序，这是
「同一查询命中多条规则」时结果可复现的前提。
"""
from __future__ import annotations

from app import config

COLUMNS = (
    {"name": "query_id", "label": "查询编号", "type": "text", "unit": "",
     "required": True, "nullable": False, "note": "字符串保形，前导零不改写",
     "aliases": ("query_id", "queryid", "查询编号", "查询id")},
    {"name": "dept", "label": "部门", "type": "text", "unit": "",
     "required": True, "nullable": False, "note": "计费归集主体",
     "aliases": ("dept", "department", "部门", "使用部门")},
    {"name": "project", "label": "项目", "type": "text", "unit": "",
     "required": True, "nullable": True, "note": "业务项目名",
     "aliases": ("project", "项目", "作业")},
    {"name": "started_at", "label": "开始时间", "type": "instant", "unit": "时区偏移",
     "required": True, "nullable": False, "note": "入库统一 UTC",
     "aliases": ("started_at", "start_time", "开始时间", "起始时间")},
    {"name": "duration_s", "label": "时长", "type": "decimal", "unit": "秒",
     "required": True, "nullable": False, "note": "换算小时后按时段单价计费",
     "aliases": ("duration_s", "duration", "时长", "运行时长")},
    {"name": "scan_size", "label": "扫描量", "type": "decimal", "unit": "见扫描单位",
     "required": True, "nullable": False, "note": "支持千分位、全角、科学计数法",
     "aliases": ("scan_size", "scan", "扫描量", "扫描")},
    {"name": "scan_unit", "label": "扫描单位", "type": "unit", "unit": "B/KB/MB/GB/TB/PB",
     "required": True, "nullable": True, "note": "归一 GB 后再计费",
     "aliases": ("scan_unit", "unit", "扫描单位", "单位")},
    {"name": "concurrency", "label": "并发", "type": "int", "unit": "个",
     "required": False, "nullable": True, "note": "超过 8 的部分按小时加价",
     "aliases": ("concurrency", "并发", "并发数")},
    {"name": "value_score", "label": "收益分", "type": "decimal", "unit": "分",
     "required": False, "nullable": True, "note": "闸门排序用，0-100",
     "aliases": ("value_score", "value", "收益分", "业务价值")},
    {"name": "shared_with", "label": "共享部门", "type": "shared", "unit": "部门:小时:GB",
     "required": False, "nullable": True, "note": "空表示独占",
     "aliases": ("shared_with", "shared", "共享部门", "共享")},
)

COLUMN_NAMES = tuple(col["name"] for col in COLUMNS)
REQUIRED = tuple(col["name"] for col in COLUMNS if col["required"])


def column_by_name(name: str) -> dict | None:
    for col in COLUMNS:
        if col["name"] == name:
            return col
    return None


def alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for col in COLUMNS:
        for alias in col["aliases"]:
            out[_key(alias)] = col["name"]
    return out


def _key(text: str) -> str:
    return "".join(str(text or "").replace("\ufeff", "").split()).lower()


def map_header(header: list[str]) -> tuple[dict[str, int], list[str]]:
    """把表头映射成 列名 → 下标；返回 (映射, 未识别列名)。"""
    aliases = alias_map()
    mapping: dict[str, int] = {}
    unknown: list[str] = []
    for index, raw in enumerate(header):
        name = aliases.get(_key(raw))
        if name and name not in mapping:
            mapping[name] = index
        elif name is None:
            unknown.append(str(raw))
    return mapping, unknown


def rule_table() -> list[dict]:
    return [dict(rule) for rule in config.RULES]


def ordered_rules(rules=None) -> list[dict]:
    rows = [dict(r) for r in (rules if rules is not None else config.RULES)]
    rows.sort(key=lambda r: (int(r["priority"]), str(r["id"])))
    return rows


def price_table() -> list[dict]:
    return [
        {"id": "P_SCAN", "label": "扫描单价", "value": str(config.PRICE_PER_GB),
         "unit": "元/GB", "when": "按折算后 GB 线性"},
        {"id": "P_DAY", "label": "白天时长单价", "value": str(config.DAY_RATE_PER_HOUR),
         "unit": "元/小时", "when": "本地 06:00-22:00 的小时桶"},
        {"id": "P_NIGHT", "label": "夜间时长单价", "value": str(config.NIGHT_RATE_PER_HOUR),
         "unit": "元/小时", "when": "本地 22:00-06:00 的小时桶"},
        {"id": "P_CONC", "label": "并发加价", "value": str(config.CONC_SURCHARGE_PER_HOUR),
         "unit": "元/并发·小时", "when": f"并发数 > {config.FREE_CONCURRENCY}"},
        {"id": "P_MIN", "label": "最低计费", "value": str(config.MIN_CHARGE),
         "unit": "元/查询", "when": "成本低于最低值时"},
        {"id": "P_VOL", "label": "大客户折扣系数", "value": str(config.VOLUME_FACTOR),
         "unit": "倍", "when": f"部门本批扫描量 ≥ {config.VOLUME_THRESHOLD_GB} GB"},
    ]


def contract_payload() -> dict:
    return {
        "rule_version": config.RULE_VERSION,
        "money_unit": config.MONEY_UNIT,
        "columns": [{k: (list(v) if isinstance(v, tuple) else v)
                     for k, v in col.items()} for col in COLUMNS],
        "required": list(REQUIRED),
        "rules": ordered_rules(),
        "prices": price_table(),
        "scan_units": list(config.UNIT_ORDER),
        "sentinels": sorted(config.SENTINELS),
        "night_window": f"{config.NIGHT_START_HOUR:02d}:00-{config.NIGHT_END_HOUR:02d}:00",
        "min_charge": str(config.MIN_CHARGE),
    }
