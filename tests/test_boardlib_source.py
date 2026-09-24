"""Tests for parsing BoardLib logbook CSVs into Ascent objects."""

from __future__ import annotations

from datetime import datetime

from kilter_garmin_sync.boardlib_source import load_csv, parse_logbook


def test_load_csv_parses_all_rows(fixture_csv):
    ascents = load_csv(fixture_csv)
    assert len(ascents) == 7


def test_field_parsing(fixture_csv):
    ascents = load_csv(fixture_csv)
    first = ascents[0]
    assert first.climb_name == "Pistol Whip"
    assert first.angle == 40
    assert first.date == datetime(2026, 9, 20, 18, 5, 0)
    assert first.is_ascent is True
    assert first.is_benchmark is True
    assert first.is_mirror is False
    assert first.tries == 2
    # displayed_grade preferred over logged_grade
    assert first.grade == "V7"


def test_attempt_row_flagged(fixture_csv):
    ascents = load_csv(fixture_csv)
    slab = next(a for a in ascents if a.climb_name == "Slab Master")
    assert slab.is_ascent is False


def test_has_time_detects_midnight():
    rows = (
        "board,angle,climb_name,date,logged_grade,displayed_grade,is_benchmark,"
        "tries,is_mirror,sessions_count,tries_total,is_repeat,is_ascent,comment\n"
        "kilter,40,Timed,2026-01-01 14:30:00,V4,V4,False,1,False,1,1,False,True,\n"
        "kilter,40,DateOnly,2026-01-02,V4,V4,False,1,False,1,1,False,False,\n"
    )
    ascents = parse_logbook(rows)
    assert ascents[0].has_time is True
    assert ascents[1].has_time is False


def test_blank_rows_skipped():
    rows = (
        "board,angle,climb_name,date,logged_grade,displayed_grade,is_benchmark,"
        "tries,is_mirror,sessions_count,tries_total,is_repeat,is_ascent,comment\n"
        "kilter,40,Good,2026-01-01 14:30:00,V4,V4,False,1,False,1,1,False,True,\n"
        ",,,,,,,,,,,,,\n"
    )
    ascents = parse_logbook(rows)
    assert len(ascents) == 1
