"""尾差：最大余额法。整数分先按权重取整，再把剩下的分给小数余额最大的部门。

- 份额合计恒等于总额（守恒断言在 invariant.py）；
- 余额相同按输入下标升序，输入已按部门名排序，因此结果可复现。
"""
from __future__ import annotations

from decimal import Decimal

FLOOR = "ROUND_FLOOR"


class ZeroWeight(ValueError):
    """权重全为 0，无法按权重分摊。"""


def largest_remainder(total_cents: int, weights: list[Decimal]) -> list[int]:
    total = int(total_cents)
    if not weights:
        return []
    weight_sum = sum(Decimal(str(w)) for w in weights)
    if weight_sum <= 0:
        raise ZeroWeight("权重合计为 0")
    if total == 0:
        return [0 for _ in weights]
    floors: list[int] = []
    remainders: list[Decimal] = []
    for weight in weights:
        exact = Decimal(total) * Decimal(str(weight)) / weight_sum
        floor = int(exact.to_integral_value(rounding=FLOOR))
        floors.append(floor)
        remainders.append(exact - floor)
    leftover = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda idx: (-remainders[idx], idx))
    for idx in order[:leftover]:
        floors[idx] += 1
    return floors


def check_conserved(shares: list[int], total_cents: int) -> bool:
    return sum(shares) == int(total_cents)
