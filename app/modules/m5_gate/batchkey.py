"""批次键与陈旧提交校验。

批次键 = sha256(来源 + 输入摘要 + 预算摘要 + 规则版本 + 评估日)，
同键重跑直接复用原批次，不新增行、不改合计。
"""
from __future__ import annotations

from app import errors, manifest


def batch_key(*, source: str, input_digest: str, budget_digest: str,
              rule_version: str, eval_date: str) -> str:
    return manifest.digest_parts([source, input_digest, budget_digest,
                                  rule_version, eval_date])[:32]


def require_version(current: int, expected: int | None) -> None:
    if expected is None:
        return
    if int(expected) != int(current):
        raise errors.StaleVersion(
            f"提交版本 {expected} 已落后于当前版本 {current}，请刷新后重试",
            detail={"current_version": int(current), "expected_version": int(expected)})


def verify_check(batch_no_value: str, digit: str) -> bool:
    return manifest.verify(batch_no_value, digit)
