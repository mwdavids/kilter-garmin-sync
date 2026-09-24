"""Shared test helpers and fixtures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from kilter_garmin_sync.models import Ascent

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_logbook.csv"


def make_ascent(
    *,
    climb_name: str = "Test Climb",
    when: datetime,
    grade: str = "V4",
    angle: int | None = 40,
    tries: int = 1,
    is_ascent: bool = True,
    is_mirror: bool = False,
    comment: str = "",
) -> Ascent:
    return Ascent(
        board="kilter",
        angle=angle,
        climb_name=climb_name,
        date=when,
        logged_grade=grade,
        displayed_grade=grade,
        is_benchmark=False,
        tries=tries,
        is_mirror=is_mirror,
        is_ascent=is_ascent,
        comment=comment,
    )


@pytest.fixture
def fixture_csv() -> Path:
    return FIXTURE_CSV


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run each test in its own temp cwd so the default ``data/`` ledger and
    SQLite cache never leak across tests or into the repo."""
    monkeypatch.chdir(tmp_path)
