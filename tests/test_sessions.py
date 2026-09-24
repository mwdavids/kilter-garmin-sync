"""Tests for session grouping logic."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from kilter_garmin_sync.boardlib_source import load_csv
from kilter_garmin_sync.sessions import group_sessions

from conftest import make_ascent


def test_default_groups_one_session_per_day(fixture_csv):
    sessions = group_sessions(load_csv(fixture_csv))
    # Days present: 2026-09-20, 2026-09-23, 2026-09-25
    assert len(sessions) == 3
    assert [s.day for s in sessions] == [
        date(2026, 9, 20),
        date(2026, 9, 23),
        date(2026, 9, 25),
    ]
    # First day has 5 ascents.
    assert len(sessions[0].ascents) == 5


def test_all_same_day_ascents_form_one_session(fixture_csv):
    # The 2026-09-20 climbs span 18:05 to 23:20 (>5h) but must stay one session.
    sessions = group_sessions(load_csv(fixture_csv))
    day20 = [s for s in sessions if s.day == date(2026, 9, 20)]
    assert len(day20) == 1
    assert len(day20[0].ascents) == 5
    # Duration is first->last ascent that day.
    assert day20[0].duration == timedelta(hours=5, minutes=15)


def test_since_filter(fixture_csv):
    sessions = group_sessions(load_csv(fixture_csv), since=date(2026, 9, 23))
    assert [s.day for s in sessions] == [date(2026, 9, 23), date(2026, 9, 25)]


def test_ascents_only_excludes_attempts(fixture_csv):
    sessions = group_sessions(load_csv(fixture_csv), ascents_only=True)
    day20 = next(s for s in sessions if s.day == date(2026, 9, 20))
    # The attempt ("Slab Master") is dropped, leaving 4 sends.
    assert len(day20.ascents) == 4
    assert all(a.is_ascent for a in day20.ascents)


def test_min_duration_padding_single_ascent():
    ascents = [make_ascent(when=datetime(2026, 3, 1, 9, 0, 0))]
    sessions = group_sessions(ascents, min_duration_minutes=10)
    assert len(sessions) == 1
    assert sessions[0].duration == timedelta(minutes=10)


def test_real_duration_kept_when_long_enough():
    ascents = [
        make_ascent(when=datetime(2026, 3, 1, 9, 0, 0)),
        make_ascent(when=datetime(2026, 3, 1, 10, 30, 0)),
    ]
    sessions = group_sessions(ascents, min_duration_minutes=10)
    assert sessions[0].duration == timedelta(hours=1, minutes=30)


def test_sessions_sorted_by_start():
    ascents = [
        make_ascent(when=datetime(2026, 3, 5, 9, 0, 0)),
        make_ascent(when=datetime(2026, 3, 1, 9, 0, 0)),
    ]
    sessions = group_sessions(ascents)
    assert sessions[0].day == date(2026, 3, 1)
    assert sessions[1].day == date(2026, 3, 5)
