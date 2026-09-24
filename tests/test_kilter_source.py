"""Tests for the new-Kilter source mapping (fixtures, no credentials)."""

import json

from kilter_garmin_sync.kilter_source import (
    build_ascents,
    collect_tables,
    fetch_ascents,
    resolve_tz,
)
from kilter_garmin_sync.sessions import group_sessions


def _op(object_type, oid, data):
    return {
        "op": "PUT",
        "object_type": object_type,
        "object_id": str(oid),
        "data": json.dumps(data),
    }


def _msg(bucket, ops):
    return {"data": {"bucket": bucket, "data": ops}}


DIFFICULTY_GRADES = [
    {"difficulty_grade_id": 10, "v_scale": "V0", "boulder_difficulty": "4A/V0"},
    {"difficulty_grade_id": 14, "v_scale": "V4", "boulder_difficulty": "6B+/V4"},
    {"difficulty_grade_id": 18, "v_scale": "V7", "boulder_difficulty": "7A+/V7"},
]


def fake_stream():
    user = 'user_buckets["u-1"]'
    return [
        {"checkpoint": {"buckets": [{"bucket": user, "count": 5}]}},
        _msg(
            "global[]",
            [_op("difficulty_grades", g["difficulty_grade_id"], g) for g in DIFFICULTY_GRADES],
        ),
        _msg(
            user,
            [
                # A send, rated V7 at 40 deg (climb AAA).
                _op(
                    "logs",
                    1,
                    {
                        "log_uuid": "l1",
                        "climb_uuid": "AAAAAAAAAAAA",
                        "angle": 40,
                        "topped": 1,
                        "flashed": 0,
                        "attempts": 3,
                        "created_at": "2026-09-20 18:05:00.000000Z",
                    },
                ),
                # A send on the SAME local day, rated V4 at 40 (climb BBB).
                _op(
                    "logs",
                    2,
                    {
                        "log_uuid": "l2",
                        "climb_uuid": "BBBBBBBBBBBB",
                        "angle": 40,
                        "topped": 1,
                        "flashed": 1,
                        "attempts": 1,
                        "created_at": "2026-09-20 20:30:00.000000Z",
                    },
                ),
                # An attempt (not topped), unrated climb CCC.
                _op(
                    "logs",
                    3,
                    {
                        "log_uuid": "l3",
                        "climb_uuid": "CCCCCCCCCCCC",
                        "angle": 30,
                        "topped": 0,
                        "flashed": 0,
                        "attempts": 5,
                        "created_at": "2026-09-20 21:00:00.000000Z",
                    },
                ),
                # A send the NEXT UTC day but which is still the prior day in
                # US/Pacific (05:00Z -> 22:00 previous day).
                _op(
                    "logs",
                    4,
                    {
                        "log_uuid": "l4",
                        "climb_uuid": "AAAAAAAAAAAA",
                        "angle": 40,
                        "topped": 1,
                        "attempts": 1,
                        "created_at": "2026-09-24 05:00:00.000000Z",
                    },
                ),
                _op(
                    "climb_ratings",
                    "r1",
                    {
                        "climb_uuid": "AAAAAAAAAAAA",
                        "angle": 40,
                        "difficulty_grade_id": 18,
                        "comment": "crimpy",
                    },
                ),
                _op(
                    "climb_ratings",
                    "r2",
                    {
                        "climb_uuid": "BBBBBBBBBBBB",
                        "angle": 40,
                        "difficulty_grade_id": 14,
                        "comment": None,
                    },
                ),
            ],
        ),
        {"checkpoint_complete": {"last_op_id": "6"}},
    ]


def test_collect_tables_gathers_wanted_types():
    tables = collect_tables(fake_stream())
    assert len(tables["logs"]) == 4
    assert len(tables["climb_ratings"]) == 2
    assert len(tables["difficulty_grades"]) == 3


def test_build_ascents_maps_fields_and_joins_grade():
    tables = collect_tables(fake_stream())
    ascents = build_ascents(tables, tz=resolve_tz("UTC"))
    by_uuid_day = {(a.climb_name, a.date.isoformat()): a for a in ascents}

    # Grade join: AAA -> V7, BBB -> V4, CCC (unrated) -> None.
    aaa = next(a for a in ascents if a.climb_name == "Climb AAAAAAAA" and a.angle == 40)
    assert aaa.grade == "V7"
    assert aaa.is_ascent is True
    assert aaa.tries == 3
    assert aaa.comment == "crimpy"

    ccc = next(a for a in ascents if a.climb_name == "Climb CCCCCCCC")
    assert ccc.grade is None
    assert ccc.is_ascent is False  # topped == 0
    assert ccc.tries == 5

    # Name is derived from the uuid (backend has no names).
    assert all(a.climb_name.startswith("Climb ") for a in ascents)


def test_timezone_grouping_by_local_day():
    tables = collect_tables(fake_stream())

    # In UTC, log #4 (05:00Z on the 24th) is its own day.
    utc = group_sessions(build_ascents(tables, tz=resolve_tz("UTC")))
    utc_days = sorted(str(s.day) for s in utc)
    assert utc_days == ["2026-09-20", "2026-09-24"]

    # In US/Pacific, 05:00Z on the 24th is 22:00 on the 23rd.
    pac = group_sessions(build_ascents(tables, tz=resolve_tz("America/Los_Angeles")))
    pac_days = sorted(str(s.day) for s in pac)
    assert pac_days == ["2026-09-20", "2026-09-23"]

    # First day groups all three of that day's entries into ONE session.
    first = next(s for s in utc if str(s.day) == "2026-09-20")
    assert len(first.ascents) == 3
    assert len(first.sends) == 2  # two topped, one attempt


def test_fetch_ascents_uses_injected_stream(monkeypatch):
    import kilter_garmin_sync.kilter_source as src

    monkeypatch.setattr(src, "get_access_token", lambda u, p: "fake-token")
    monkeypatch.setattr(src, "stream_sync", lambda token: iter(fake_stream()))

    ascents = fetch_ascents("user", "pass", tz_name="UTC", cache_db=None)
    assert len(ascents) == 4
    assert {a.grade for a in ascents} == {"V7", "V4", None}
