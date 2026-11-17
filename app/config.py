"""costsplit 全局配置：单价表、时段边界、单位表、批次号、编码回退链。

规则唯一来源，其他模块只读：
- 金额一律 Decimal，单位「元」，最小刻度 0.01（分）；
- 扫描量入库一律折算成 GB（Decimal，6 位小数），原始单位原样保留；
- 时间入库一律 UTC，展示按 Asia/Shanghai(+08:00)；
- 夜间时段 = 本地 [22:00, 次日 06:00)，按小时桶左闭右开切分。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "costsplit/1.3.0"
MONEY_UNIT = "元"
CENT = Decimal("0.01")
PRICE_DECIMALS = Decimal("0.000001")

# 单价表（元）。时段单价按小时桶生效，扫描单价按 GB 生效。
PRICE_PER_GB = Decimal("0.0280")
DAY_RATE_PER_HOUR = Decimal("0.4200")
NIGHT_RATE_PER_HOUR = Decimal("0.2100")
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6

# 并发加价：超过免加价并发数后，每多 1 并发每小时加价
FREE_CONCURRENCY = 8
CONC_SURCHARGE_PER_HOUR = Decimal("0.1500")

# 大客户折扣：部门本批扫描量达到阈值后，扫描部分打折
VOLUME_THRESHOLD_GB = Decimal("1024")
VOLUME_FACTOR = Decimal("0.90")

# 单查询最低计费
MIN_CHARGE = Decimal("0.01")

BATCH_PREFIX = "CS"
CHECK_MOD = 11
CHECK_ALPHABET = "0123456789X"

STAGES = ("ingest", "billing", "quality", "alloc", "gate", "appeal", "export")
STAGE_LABELS = {
    "ingest": "采信",
    "billing": "计费",
    "quality": "质量",
    "alloc": "分摊",
    "gate": "闸门",
    "appeal": "申诉",
    "export": "导出",
}
STAGE_FLOW = "采信 → 计费 → 质量 → 分摊 → 闸门 → 申诉 → 导出"

# 编码回退链：gb18030 是 gbk/gb2312 的超集
ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")
CHARSET_ALIASES = {
    "utf8": "utf-8",
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8-sig",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
}

# 空值哨兵：三态（None 哨兵 / 0 真值 / 空串）集中定义
SENTINELS = frozenset(
    {"", "-", "--", "—", "——", "/", "n/a", "na", "null", "none", "nil",
     "未知", "无", "暂无", "不适用", "待定"}
)

# 扫描量单位 → GB 换算。字节数用 1024 进制，与大整数保形一致。
UNIT_EXPONENT = {"B": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4, "PB": 5}
UNIT_ALIASES = {
    "b": "B", "byte": "B", "bytes": "B", "字节": "B",
    "k": "KB", "kb": "KB", "kib": "KB", "千字节": "KB",
    "m": "MB", "mb": "MB", "mib": "MB", "兆": "MB",
    "g": "GB", "gb": "GB", "gib": "GB", "吉": "GB",
    "t": "TB", "tb": "TB", "tib": "TB", "太": "TB",
    "p": "PB", "pb": "PB", "pib": "PB", "拍": "PB",
}
UNIT_ORDER = ("B", "KB", "MB", "GB", "TB", "PB")

# 计费规则表：显式优先级，运行时按 (priority, id) 排序后依次作用，
# 与规则书写/注册顺序无关；同一查询可命中多条。
RULES = (
    {
        "id": "R_NIGHT_RATE",
        "priority": 10,
        "kind": "hour_rate",
        "value": str(NIGHT_RATE_PER_HOUR),
        "when": "小时桶落在本地 22:00-06:00",
    },
    {
        "id": "R_VOLUME_DISCOUNT",
        "priority": 20,
        "kind": "scan_discount",
        "value": str(VOLUME_FACTOR),
        "when": "部门本批扫描量 ≥ 1024 GB",
    },
    {
        "id": "R_CONC_SURCHARGE",
        "priority": 30,
        "kind": "addon",
        "value": f"{CONC_SURCHARGE_PER_HOUR}/并发·小时",
        "when": "并发数 > 8",
    },
    {
        "id": "R_MIN_CHARGE",
        "priority": 40,
        "kind": "floor",
        "value": str(MIN_CHARGE),
        "when": "单查询成本低于最低计费",
    },
)

GATE_DECISIONS = {
    "KEEP": "保留",
    "TRUNCATE": "截断",
    "NO_BUDGET": "无预算",
}
BILL_STATUS = {
    "BILLED": "计费",
    "ZERO_SCAN": "零扫描",
    "UNALLOCATED": "未分摊",
}

# 申诉改判只允许动这几列；其余字段要吃重新导入，不在这里改。
APPEAL_FIX_FIELDS = ("dept", "started_at", "duration_s", "scan_size", "scan_unit")
APPEAL_STATUS = {"APPLIED": "已改判", "REJECTED": "未采纳", "MERGED": "已合并"}
# 版本快照来源；回滚本身也写成新版本，历史不被覆盖。
VERSION_KINDS = {"import": "导入", "gate": "闸门", "appeal": "申诉", "rollback": "回滚"}


@dataclass(frozen=True)
class Settings:
    """运行参数；测试用 replace() 覆盖，不读环境变量的分支。"""

    db_path: str = str(PROJECT_ROOT / "var" / "costsplit.sqlite")
    sample_dir: str = str(PROJECT_ROOT / "sample_data")
    default_source: str = "bills_2026_11_dirty.csv"
    default_budget_source: str = "budgets.csv"
    eval_date: dt.date = dt.date(2026, 11, 15)
    rule_version: str = RULE_VERSION

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        db_path = os.getenv("COSTSPLIT_DB") or base.db_path
        sample_dir = os.getenv("COSTSPLIT_SAMPLE_DIR") or base.sample_dir
        default_source = os.getenv("COSTSPLIT_SOURCE") or base.default_source
        budget_source = os.getenv("COSTSPLIT_BUDGET_SOURCE") or base.default_budget_source
        eval_raw = os.getenv("EVAL_DATE") or ""
        eval_date = base.eval_date
        if eval_raw:
            eval_date = dt.date.fromisoformat(eval_raw)
        return replace(cls(), db_path=db_path, sample_dir=sample_dir,
                       default_source=default_source,
                       default_budget_source=budget_source, eval_date=eval_date)

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent

    @property
    def samples(self) -> Path:
        return Path(self.sample_dir).resolve()
