"""单位换算与时间分桶：MB/GB/TB/PB 归一成 GB，时长按小时桶左闭右开切分。

- 换算一律 1024 进制；字节数用 Decimal 保持精确，超过 2^53 也不丢位；
- 小时桶 `[bucket, bucket + 1h)`，跨零点/跨日按物理时间续切，不重不漏；
- 夜间单价按桶起点所在的本地小时判定。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app import config


class UnitError(ValueError):
    """单位不可识别。"""


def normalize_unit(raw: str) -> str:
    text = (raw or "").strip().strip("\ufeff").lower()
    if text in config.UNIT_ALIASES:
        return config.UNIT_ALIASES[text]
    raise UnitError(raw or "")


def exponent(unit: str) -> int:
    return config.UNIT_EXPONENT[normalize_unit(unit)]


def to_bytes(size: Decimal, unit: str) -> Decimal:
    return Decimal(size) * (Decimal(1024) ** exponent(unit))


def to_gb(size: Decimal, unit: str) -> Decimal:
    shift = exponent(unit) - 3
    value = Decimal(size) * (Decimal(1024) ** shift)
    return value.quantize(config.PRICE_DECIMALS)


def bytes_to_gb(nbytes: Decimal) -> Decimal:
    value = Decimal(nbytes) / (Decimal(1024) ** 3)
    return value.quantize(config.PRICE_DECIMALS)


def big_int_text(value: Decimal) -> str:
    """大整数保形：禁科学计数法，逐位原样输出。"""
    return format(Decimal(value), "f")


def local_hour_of(bucket_start_utc: dt.datetime) -> int:
    local = bucket_start_utc.astimezone(config.CHINA_TZ)
    return local.hour


def is_night(bucket_start_utc: dt.datetime) -> bool:
    hour = local_hour_of(bucket_start_utc)
    return hour >= config.NIGHT_START_HOUR or hour < config.NIGHT_END_HOUR


def bucket_rate(bucket_start_utc: dt.datetime) -> Decimal:
    return (config.NIGHT_RATE_PER_HOUR if is_night(bucket_start_utc)
            else config.DAY_RATE_PER_HOUR)


def hour_floor(moment: dt.datetime) -> dt.datetime:
    utc = moment.astimezone(config.UTC)
    return utc.replace(minute=0, second=0, microsecond=0)


def split_hours(start_utc: dt.datetime, duration_s) -> list[tuple[dt.datetime, Decimal]]:
    """把一个查询的时长切成若干小时桶，每段是 [桶起点, 桶起点+1h) 的子区间。"""
    duration = Decimal(str(duration_s))
    if duration <= 0:
        return []
    start = start_utc.astimezone(config.UTC)
    bucket = hour_floor(start)
    offset = Decimal(str(int((start - bucket).total_seconds())))
    remaining = duration
    out: list[tuple[dt.datetime, Decimal]] = []
    first = True
    while remaining > 0:
        capacity = Decimal(3600) - (offset if first else Decimal(0))
        take = capacity if capacity < remaining else remaining
        out.append((bucket, (take / Decimal(3600)).quantize(config.PRICE_DECIMALS)))
        remaining -= take
        bucket = bucket + dt.timedelta(hours=1)
        first = False
    return out


def hours_total(duration_s) -> Decimal:
    return (Decimal(str(duration_s)) / Decimal(3600)).quantize(config.PRICE_DECIMALS)
