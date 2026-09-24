"""Tests for the human-readable session summary."""

from __future__ import annotations

from datetime import datetime

from kilter_garmin_sync.sessions import group_sessions
from kilter_garmin_sync.summary import session_notes, session_title

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


def test_notes_lists_sends_and_attempts():
    notes = session_notes(_session())
    assert "Sends:" in notes
    assert "Attempts:" in notes
    assert "Alpha" in notes
    assert "Bravo" in notes
    assert "Charlie" in notes
    assert "3 tries" in notes
    assert "kilter-garmin-sync" in notes


def test_grade_range_orders_double_digits():
    ascents = [
        make_ascent(climb_name="A", when=datetime(2026, 3, 1, 9, 0), grade="V9"),
        make_ascent(climb_name="B", when=datetime(2026, 3, 1, 9, 30), grade="V10"),
    ]
    title = session_title(group_sessions(ascents)[0])
    # V10 must sort above V9 numerically, not lexically.
    assert "V9\u2013V10" in title
