"""New-Kilter (kiltergrips.com) data source.

Replaces the dead BoardLib/Aurora path. Authenticates via Keycloak, streams the
user's data from PowerSync, and maps the ``logs`` object_type into the tool's
existing :class:`~kilter_garmin_sync.models.Ascent` model so the rest of the
pipeline (day grouping, summary, FIT/TCX) is unchanged.

What the backend exposes (verified against the live sync):

* ``logs`` — the logbook. Fields: ``climb_uuid``, ``angle``, ``topped`` (1 = a
  successful ascent), ``flashed`` (1 = first try), ``attempts``, ``created_at``
  (UTC, e.g. ``2026-04-04 00:23:32.643479Z``).
* ``climb_ratings`` — the user's own per-(climb, angle) opinion:
  ``difficulty_grade_id`` + ``comment``.
* ``difficulty_grades`` — maps ``difficulty_grade_id`` to a V-scale grade.

Limitations of the new backend (documented, not bugs):

* There is **no climb-name table and no consensus-grade (``climb_stats``) table**
  in the sync, so climbs are labelled ``Climb <uuid8>`` and the grade shown is
  the user's own rating (only for climbs they rated).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .kilter_client import get_access_token, stream_sync
from .models import Ascent

BOARD = "kilter"

# object_types we need to build ascents.
_WANTED_TYPES = ("logs", "climb_ratings", "difficulty_grades")


def _parse_created_at(value: str) -> datetime:
    """Parse a PowerSync ``created_at`` (UTC) into an aware datetime."""
    s = (value or "").strip()
    if not s:
        raise ValueError("empty timestamp")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:  # pragma: no cover - defensive
            raise
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_local(dt: datetime, tz: Any) -> datetime:
    """Convert an aware UTC datetime to ``tz``, keeping it timezone-aware.

    Day-grouping keys off the local calendar date (``dt.date()`` in the resolved
    tz), and the FIT/TCX writers need the aware value so they can encode the true
    UTC instant plus the local display offset. When ``tz`` is None we fall back
    to system local time (still aware).
    """
    return dt.astimezone(tz) if tz is not None else dt.astimezone()


def resolve_tz(tz_name: str | None) -> Any:
    """Return a tzinfo for ``tz_name`` (IANA), or None for system local time."""
    if not tz_name:
        return None
    return ZoneInfo(tz_name)


def collect_tables(
    messages: Iterable[dict[str, Any]],
    *,
    wanted: tuple[str, ...] = _WANTED_TYPES,
) -> dict[str, list[dict[str, Any]]]:
    """Single-pass collection of the object_types we need from the stream.

    Reads PUT rows for ``wanted`` object_types across all buckets (``logs`` and
    ``climb_ratings`` only exist in the user's bucket; ``difficulty_grades`` is
    global), stopping at ``checkpoint_complete``.
    """
    tables: dict[str, list[dict[str, Any]]] = {t: [] for t in wanted}
    wanted_set = set(wanted)
    for msg in messages:
        if "checkpoint_complete" in msg:
            break
        data_msg = msg.get("data")
        if not isinstance(data_msg, dict):
            continue
        for op in data_msg.get("data", []) or []:
            if op.get("op") == "REMOVE":
                continue
            object_type = op.get("object_type")
            if object_type not in wanted_set:
                continue
            row = op.get("data")
            if isinstance(row, str):
                try:
                    row = json.loads(row)
                except json.JSONDecodeError:  # pragma: no cover - defensive
                    continue
            if isinstance(row, dict):
                tables[object_type].append(row)
    return tables


def _grade_index(difficulty_grades: list[dict[str, Any]]) -> dict[Any, str]:
    """Map difficulty_grade_id -> human grade (V-scale preferred)."""
    index: dict[Any, str] = {}
    for row in difficulty_grades:
        gid = row.get("difficulty_grade_id", row.get("id"))
        grade = row.get("v_scale") or row.get("boulder_difficulty")
        if gid is not None and grade:
            index[gid] = str(grade)
            index[str(gid)] = str(grade)
    return index


def _rating_index(
    climb_ratings: list[dict[str, Any]],
) -> dict[tuple[str, Any], dict[str, Any]]:
    """Map (climb_uuid, angle) -> rating row (difficulty_grade_id, comment)."""
    index: dict[tuple[str, Any], dict[str, Any]] = {}
    for row in climb_ratings:
        key = (row.get("climb_uuid"), row.get("angle"))
        index[key] = row
    return index


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_ascents(
    tables: Mapping[str, list[dict[str, Any]]],
    *,
    tz: Any = None,
) -> list[Ascent]:
    """Build the internal Ascent list from collected PowerSync tables."""
    grades = _grade_index(tables.get("difficulty_grades", []))
    ratings = _rating_index(tables.get("climb_ratings", []))

    ascents: list[Ascent] = []
    for log in tables.get("logs", []):
        climb_uuid = log.get("climb_uuid") or ""
        angle = _int_or_none(log.get("angle"))
        try:
            when = _to_local(_parse_created_at(log.get("created_at", "")), tz)
        except ValueError:
            continue  # a log without a usable timestamp can't be placed in a day

        rating = ratings.get((climb_uuid, log.get("angle")))
        grade = None
        comment = ""
        if rating is not None:
            grade = grades.get(rating.get("difficulty_grade_id"))
            comment = rating.get("comment") or ""

        # Climb names are not synced by the backend; label by short uuid.
        name = f"Climb {climb_uuid[:8]}" if climb_uuid else "Climb (unknown)"

        ascents.append(
            Ascent(
                board=BOARD,
                angle=angle,
                climb_name=name,
                date=when,
                logged_grade=None,
                displayed_grade=grade,
                is_benchmark=False,
                tries=_int_or_none(log.get("attempts")),
                is_mirror=False,
                is_ascent=bool(log.get("topped")),
                comment=comment,
            )
        )

    ascents.sort(key=lambda a: a.date)
    return ascents


def write_cache(tables: Mapping[str, list[dict[str, Any]]], db_path: Path) -> None:
    """Persist collected rows to a local SQLite cache (data/).

    Uses a generic ``sync_rows(object_type, data)`` store so the schema is robust
    to backend field changes. This is a convenience cache; the pipeline builds
    ascents from the in-memory tables regardless.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sync_rows "
            "(object_type TEXT, data TEXT)"
        )
        conn.execute("DELETE FROM sync_rows")
        conn.executemany(
            "INSERT INTO sync_rows (object_type, data) VALUES (?, ?)",
            [
                (object_type, json.dumps(row, default=str))
                for object_type, rows in tables.items()
                for row in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


def fetch_ascents(
    username: str,
    password: str,
    *,
    tz_name: str | None = None,
    cache_db: Path | None = None,
) -> list[Ascent]:
    """Authenticate, sync from PowerSync, and return the user's ascents."""
    token = get_access_token(username, password)
    tables = collect_tables(stream_sync(token))
    if cache_db is not None:
        write_cache(tables, Path(cache_db))
    return build_ascents(tables, tz=resolve_tz(tz_name))
