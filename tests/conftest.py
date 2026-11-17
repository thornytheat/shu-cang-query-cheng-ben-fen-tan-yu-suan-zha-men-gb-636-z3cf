"""测试固定装置：临时库、固定评估日、可写样例目录。

约定：金额断言一律整数分；时间断言用固定日期 2026-11-15，不读 datetime.now()。
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TZ", "Asia/Shanghai")

from app import config                       # noqa: E402
from app.main import create_app              # noqa: E402
from app.modules.m1_ingest import service as ingest_service  # noqa: E402
from app.store import Store                  # noqa: E402

SAMPLE_DIR = ROOT / "sample_data"
EVAL_DATE = dt.date(2026, 11, 15)
HEADER = ("query_id,dept,project,started_at,duration_s,scan_size,scan_unit,"
          "concurrency,value_score,shared_with")


def write_sample(directory, name: str, rows: list[str], *, header: str = HEADER,
                 encoding: str = "utf-8", bom: bool = False,
                 newline: str = "\r\n", trailing_newline: bool = True,
                 header_block: bool = True) -> pathlib.Path:
    """按指定编码/BOM/换行写一份样例，用来构造针对性脏输入。"""
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    parts = ([header] if header_block else []) + list(rows)
    text = newline.join(parts)
    if trailing_newline:
        text += newline
    payload = text.encode(encoding)
    if bom:
        payload = b"\xef\xbb\xbf" + payload
    path = directory / name
    path.write_bytes(payload)
    return path


@pytest.fixture()
def settings(tmp_path) -> config.Settings:
    return config.Settings(db_path=str(tmp_path / "costsplit.sqlite"),
                           sample_dir=str(SAMPLE_DIR),
                           eval_date=EVAL_DATE)


@pytest.fixture()
def store(settings) -> Store:
    return Store(settings.db_path)


@pytest.fixture()
def client(settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def dirty(store, settings) -> dict:
    return ingest_service.run_import(store, settings,
                                     source="bills_2026_11_dirty.csv")


@pytest.fixture()
def sample_env(tmp_path):
    """自定义样例目录 + 独立库，用于构造脏文件。"""
    sample_dir = tmp_path / "samples"
    sample_dir.mkdir()
    (sample_dir / "budgets.csv").write_bytes(
        (SAMPLE_DIR / "budgets.csv").read_bytes())
    settings = config.Settings(db_path=str(tmp_path / "custom.sqlite"),
                               sample_dir=str(sample_dir),
                               eval_date=EVAL_DATE)
    store = Store(settings.db_path)
    return {"settings": settings, "store": store, "dir": sample_dir,
            "write": lambda name, rows, **kwargs: write_sample(sample_dir, name,
                                                               rows, **kwargs),
            "run": lambda name, **kwargs: ingest_service.run_import(
                store, settings, source=name, **kwargs)}


def load_budgets(settings) -> dict:
    from app.modules.m5_gate import service as gate_service
    return gate_service.load_budgets(settings, settings.default_budget_source)
