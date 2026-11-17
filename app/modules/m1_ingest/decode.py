"""字节 → 文本：按回退链逐个严格解码，声明编码只作为首个候选。

失败时从左到右找第一个坏字节，换算出物理行号，避免"整份文件读不出来但不知道哪行坏"。
"""
from __future__ import annotations

import codecs

from app import config


class DecodeFailure(ValueError):
    def __init__(self, message: str, *, line_no: int, tried: list[dict]):
        super().__init__(message)
        self.message = message
        self.line_no = line_no
        self.tried = tried
        self.code = "Q_DECODE_FAILED"


def _alias(name: str | None) -> str | None:
    if not name:
        return None
    return config.CHARSET_ALIASES.get(str(name).strip().lower())


def candidates(declared: str | None = None) -> list[str]:
    chain: list[str] = []
    first = _alias(declared)
    if first:
        chain.append(first)
    for name in config.ENCODING_CHAIN:
        if name not in chain:
            chain.append(name)
    return chain


def line_of_offset(data: bytes, offset: int) -> int:
    return data[:offset].count(b"\n") + 1


def decode_bytes(data: bytes, declared: str | None = None) -> dict:
    tried: list[dict] = []
    fallback = False
    for index, encoding in enumerate(candidates(declared)):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as exc:
            tried.append({"encoding": encoding, "ok": False,
                          "line_no": line_of_offset(data, exc.start)})
            continue
        tried.append({"encoding": encoding, "ok": True, "line_no": None})
        if index > 0:
            fallback = True
        return {
            "text": text,
            "encoding": encoding,
            "declared": _alias(declared),
            "bom_stripped": data.startswith(codecs.BOM_UTF8),
            "fallback": fallback,
            "tried": tried,
        }
    last_line = tried[-1]["line_no"] if tried else 1
    raise DecodeFailure(
        f"按 {', '.join(c['encoding'] for c in tried)} 均无法解码（首个坏字节约在第 "
        f"{last_line} 行）", line_no=last_line, tried=tried)
