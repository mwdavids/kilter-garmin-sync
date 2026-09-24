"""Fetch and load a Kilter logbook via BoardLib.

The core of this tool operates on an in-memory list of :class:`Ascent` objects
parsed from a BoardLib logbook CSV. Two entry points are provided:

* :func:`load_csv` parses an existing logbook CSV (no network, no credentials) --
  this is what the tests and the ``--from-csv`` flag use.
* :func:`fetch_logbook` shells out to the ``boardlib`` CLI to download/sync the
  board database and export a fresh logbook CSV, then parses it. Credentials are
  never hardcoded: the username is passed on the command line and the password
  is read by BoardLib from the ``<BOARD>_PASSWORD`` environment variable
  (e.g. ``KILTER_PASSWORD``).
"""

from __future__ import annotations

import csv
import io
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .models import Ascent

_TRUE = {"true", "1", "yes", "t"}


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE


def _parse_int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_date(value: str) -> datetime:
    """Parse a BoardLib ``date`` field into a datetime.

    BoardLib emits full timestamps (``YYYY-MM-DD HH:MM:SS``) for ascents and, for
    attempt-only rows, a date. Pandas may render these with a trailing time or
    fractional seconds, so we try a few known shapes.
    """
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Last resort: ISO 8601 (handles e.g. "2026-09-20T18:30:00").
    return datetime.fromisoformat(value)


def _row_to_ascent(row: dict[str, str]) -> Ascent:
    return Ascent(
        board=(row.get("board") or "").strip(),
        angle=_parse_int(row.get("angle")),
        climb_name=(row.get("climb_name") or "").strip(),
        date=_parse_date(row["date"]),
        logged_grade=(row.get("logged_grade") or "").strip() or None,
        displayed_grade=(row.get("displayed_grade") or "").strip() or None,
        is_benchmark=_parse_bool(row.get("is_benchmark")),
        tries=_parse_int(row.get("tries")),
        is_mirror=_parse_bool(row.get("is_mirror")),
        is_ascent=_parse_bool(row.get("is_ascent")) if row.get("is_ascent") not in (None, "") else True,
        comment=(row.get("comment") or "").strip(),
    )


def parse_logbook(text: str) -> list[Ascent]:
    """Parse BoardLib logbook CSV text into a list of :class:`Ascent`."""
    reader = csv.DictReader(io.StringIO(text))
    ascents: list[Ascent] = []
    for row in reader:
        if not (row.get("date") or "").strip():
            continue
        ascents.append(_row_to_ascent(row))
    return ascents


def load_csv(path: str | Path) -> list[Ascent]:
    """Load and parse a logbook CSV file from disk."""
    return parse_logbook(Path(path).read_text(encoding="utf-8"))


def _run(cmd: list[str]) -> None:
    """Run a subprocess, streaming output, raising on failure."""
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}"
        )


def fetch_logbook(
    *,
    board: str,
    username: str,
    database_path: str | Path,
    boardlib_bin: str | None = None,
) -> list[Ascent]:
    """Fetch a fresh logbook via BoardLib and return parsed ascents.

    Requires the ``<BOARD>_PASSWORD`` environment variable (e.g.
    ``KILTER_PASSWORD``) to be set so BoardLib can authenticate. The board
    database is downloaded/synced at ``database_path`` (reused on later runs).
    """
    boardlib = boardlib_bin or "boardlib"
    db_path = Path(database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Download + sync the shared board database (idempotent; reused if present).
    _run([boardlib, "database", board, str(db_path), "--username", username])

    # 2. Export the logbook to a temporary CSV, then parse it.
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / f"{board}-logbook.csv"
        _run(
            [
                boardlib,
                "logbook",
                board,
                "--username",
                username,
                "--database-path",
                str(db_path),
                "--output",
                str(csv_path),
            ]
        )
        return load_csv(csv_path)
