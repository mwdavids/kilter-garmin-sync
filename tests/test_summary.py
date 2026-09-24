"""Tests for the human-readable session summary."""

from __future__ import annotations

from datetime import datetime

from kilter_garmin_sync.sessions import group_sessions
from kilter_garmin_sync.summary import (
    grade_histogram,
    hardest_grade,
    session_notes,
    session_title,
)

from conftest import make_ascent


def _session():
    ascents = [
        make_ascent(climb_name="Alpha", when=datetime(2026, 3, 1, 9, 0), grade="V4", angle=40),
        make_ascent(climb_name="Bravo", when=datetime(2026, 3, 1, 9, 30), grade="V7", angle=45),
        make_ascent(
            climb_name="Charlie",
            when=datetime(2026, 3, 1, 10, 0),
            grade="V6",
            angle=40,
            is_ascent=False,
            tries=3,
        ),
    ]
    return group_sessions(ascents)[0]


def test_title_has_counts_angles_grades():
    title = session_title(_session())
    assert "3 climbs" in title
    assert "2 sends" in title
    assert "1 attempt" in title
    assert "40\u00b0" in title and "45\u00b0" in title
    # Grade range spans V4..V7.
    assert "V4\u2013V7" in title
    # Hardest send is V7 (the attempt is V6).
    assert "hardest V7" in title


def test_notes_lists_sends_and_attempts():
    notes = session_notes(_session())
    assert "Sends:" in notes
    assert "Attempts:" in notes
    assert "Alpha" in notes
    assert "Bravo" in notes
    assert "Charlie" in notes
    assert "3 tries" in notes
    assert "kilter-garmin-sync" in notes


def test_notes_has_grade_histogram_and_duration():
    ascents = [
        make_ascent(climb_name="A", when=datetime(2026, 3, 1, 9, 0), grade="V4"),
        make_ascent(climb_name="B", when=datetime(2026, 3, 1, 9, 30), grade="V4"),
        make_ascent(climb_name="C", when=datetime(2026, 3, 1, 10, 30), grade="V8"),
    ]
    notes = session_notes(group_sessions(ascents)[0])
    assert "Hardest send: V8" in notes
    # Per-grade send counts, hardest first.
    assert "Sends by grade: V8\u00d71, V4\u00d72" in notes
    # Duration first->last = 1h30m.
    assert "1h 30m" in notes


def test_grade_helpers_are_numeric_aware():
    ascents = [
        make_ascent(climb_name="A", when=datetime(2026, 3, 1, 9, 0), grade="V9"),
        make_ascent(climb_name="B", when=datetime(2026, 3, 1, 9, 30), grade="V10"),
    ]
    assert hardest_grade(ascents) == "V10"
    assert grade_histogram(ascents) == [("V10", 1), ("V9", 1)]


def test_grade_range_orders_double_digits():
    ascents = [
        make_ascent(climb_name="A", when=datetime(2026, 3, 1, 9, 0), grade="V9"),
        make_ascent(climb_name="B", when=datetime(2026, 3, 1, 9, 30), grade="V10"),
    ]
    title = session_title(group_sessions(ascents)[0])
    # V10 must sort above V9 numerically, not lexically.
    assert "V9\u2013V10" in title
