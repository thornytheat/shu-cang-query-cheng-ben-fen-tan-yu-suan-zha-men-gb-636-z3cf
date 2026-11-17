"""字段归一化：全角 → 半角、去千分位、哨兵三态、时间归一 UTC、共享权重解析。

每个函数只抛 CoerceError（带原因码），由质量模块决定隔离，不在这里写日志。
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from app import config

FULLWIDTH = str.maketrans("０１２３４５６７８９．，－＋％", "0123456789.,-+%")
THOUSANDS = (",", "，", " ", "\u00a0", "\u2009", "_")
UNIT_SUFFIX_RE = re.compile(r"^([0-9.+\-eE]+)\s*([A-Za-z\u4e00-\u9fa5]{1,3})?$")
SPACE_RE = re.compile(r"[\s\u3000]+")


class CoerceError(ValueError):
    def __init__(self, code: str, field: str, raw: str, message: str = ""):
        self.code = code
        self.field = field
        self.raw = raw
        self.message = message or f"{field}={raw!r}"
        super().__init__(self.message)


def clean_text(raw) -> str:
    text = "" if raw is None else str(raw)
    return SPACE_RE.sub(" ", text.replace("\ufeff", "")).strip()


def halfwidth(text: str) -> str:
    return text.translate(FULLWIDTH)


def is_sentinel(raw) -> bool:
    return clean_text(raw).lower() in config.SENTINELS


def parse_decimal(raw, field: str, *, allow_sentinel: bool = False,
                  code: str = "Q_BAD_NUMBER") -> Decimal | None:
    text = clean_text(raw)
    if text == "":
        if allow_sentinel:
            return None
        raise CoerceError("Q_SENTINEL", field, text)
    if is_sentinel(text):
        if allow_sentinel:
            return None
        raise CoerceError("Q_SENTINEL", field, text)
    value = halfwidth(text)
    for sep in THOUSANDS:
        value = value.replace(sep, "")
    value = value.replace("％", "%")
    if value.startswith("+"):
        value = value[1:]
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CoerceError(code, field, text) from exc
    if not parsed.is_finite():
        raise CoerceError(code, field, text)
    return parsed


def parse_int(raw, field: str, *, default: int | None = None) -> int:
    text = clean_text(raw)
    if text == "" or is_sentinel(text):
        if default is not None:
            return default
        raise CoerceError("Q_SENTINEL", field, text)
    value = parse_decimal(text, field)
    int_value = int(value)
    if Decimal(int_value) != value:
        raise CoerceError("Q_BAD_NUMBER", field, text)
    return int_value


INSTANT_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
                   "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d")


def parse_instant(raw, field: str = "started_at") -> dt.datetime:
    """归一 UTC：带偏移按偏移，`Z` 当 UTC，无偏移按 +08:00 解释。"""
    text = clean_text(raw)
    if text == "" or is_sentinel(text):
        raise CoerceError("Q_BAD_TIME", field, text)
    value = halfwidth(text)
    if value.endswith("Z") or value.endswith("z"):
        value = value[:-1] + "+00:00"
    try:
        moment = dt.datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None:
        for fmt in INSTANT_FORMATS:
            try:
                moment = dt.datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if moment is None:
        raise CoerceError("Q_BAD_TIME", field, text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=config.CHINA_TZ)
    return moment.astimezone(config.UTC)


def parse_shared(raw, field: str = "shared_with") -> list[dict]:
    """`部门:时长h:扫描GB;部门:...`；空串表示独占查询。"""
    text = clean_text(raw)
    if text == "" or is_sentinel(text):
        return []
    parts: list[dict] = []
    for chunk in re.split(r"[;；|]", text):
        piece = clean_text(chunk)
        if piece == "":
            continue
        bits = [b.strip() for b in re.split(r"[:：]", piece)]
        if len(bits) != 3 or bits[0] == "":
            raise CoerceError("Q_BAD_SHARED", field, piece)
        hours = parse_decimal(bits[1], field, code="Q_BAD_SHARED")
        scan = parse_decimal(bits[2], field, code="Q_BAD_SHARED")
        if hours < 0 or scan < 0:
            raise CoerceError("Q_BAD_SHARED", field, piece)
        parts.append({"dept": bits[0], "hours": hours, "scan_gb": scan})
    if not parts:
        raise CoerceError("Q_BAD_SHARED", field, text)
    return parts


def to_cents(value: Decimal) -> int:
    return int((Decimal(value) * 100).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))


def from_cents(cents: int) -> Decimal:
    return (Decimal(int(cents)) / 100).quantize(config.CENT)
