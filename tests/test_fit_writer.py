"""Tests for FIT generation: build a file and parse it back with the Garmin SDK.

The generator reproduces the proven-importable Garmin watch envelope, so these
tests decode the file and assert the structure Garmin's importer needs: a Garmin
``file_id`` + ``file_creator``, exactly one session-spanning lap, and one
``split`` per climb carrying its V-grade (and no ``climb_send`` field 73).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from garmin_fit_sdk import Decoder, Stream

from kilter_garmin_sync.fit_writer import build_fit_bytes, write_fit
from kilter_garmin_sync.sessions import group_sessions

from conftest import make_ascent


def _decode(data: bytes):
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    return messages, errors


def _session(start=datetime(2026, 3, 1, 9, 0), end=datetime(2026, 3, 1, 10, 0)):
    return group_sessions(
        [make_ascent(when=start), make_ascent(when=end)], min_duration_minutes=1
    )[0]


def _multi_climb_session():
    ascents = [
        make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4"),
        make_ascent(when=datetime(2026, 3, 1, 9, 30), grade="V5"),
        make_ascent(when=datetime(2026, 3, 1, 10, 0), grade="V6"),
        make_ascent(when=datetime(2026, 3, 1, 11, 0), grade="V3"),
    ]
    return group_sessions(ascents, min_duration_minutes=1)[0]


def test_fit_is_valid_and_parses_back():
    messages, errors = _decode(build_fit_bytes(_session()))
    assert errors == []
    assert messages["file_id_mesgs"][0]["type"] == "activity"
    assert len(messages["session_mesgs"]) == 1
    assert len(messages["activity_mesgs"]) == 1


def test_fit_identifies_as_garmin_enduro2():
    # Garmin's importer trusts a real-watch identity; a DEVELOPMENT manufacturer
    # gets silently dropped.
    messages, _ = _decode(build_fit_bytes(_session()))
    file_id = messages["file_id_mesgs"][0]
    assert file_id["manufacturer"] == "garmin"
    assert file_id["garmin_product"] == "enduro2"


def test_fit_has_file_creator():
    messages, _ = _decode(build_fit_bytes(_session()))
    assert messages["file_creator_mesgs"]


def test_fit_has_climbing_sport():
    messages, _ = _decode(build_fit_bytes(_session(), sub_sport="bouldering"))
    session = messages["session_mesgs"][0]
    assert session["sport"] == "rock_climbing"
    assert session["sub_sport"] == "bouldering"
    sport = messages["sport_mesgs"][0]
    assert sport["sport"] == "rock_climbing"
    assert sport["sub_sport"] == "bouldering"


def test_fit_indoor_climbing_subsport():
    messages, _ = _decode(build_fit_bytes(_session(), sub_sport="indoor_climbing"))
    assert messages["session_mesgs"][0]["sub_sport"] == "indoor_climbing"
    assert messages["sport_mesgs"][0]["sub_sport"] == "indoor_climbing"


def test_fit_elapsed_time_matches_duration():
    session = _session(datetime(2026, 3, 1, 9, 0), datetime(2026, 3, 1, 9, 45))
    messages, _ = _decode(build_fit_bytes(session))
    assert messages["session_mesgs"][0]["total_elapsed_time"] == pytest.approx(45 * 60)


def test_fit_activity_single_session():
    messages, _ = _decode(build_fit_bytes(_session()))
    assert messages["activity_mesgs"][0]["num_sessions"] == 1


def test_invalid_sub_sport_raises():
    with pytest.raises(ValueError):
        build_fit_bytes(_session(), sub_sport="nope")


def test_write_fit_creates_file(tmp_path):
    out = tmp_path / "nested" / "kilter-2026-03-01.fit"
    result = write_fit(_session(), out)
    assert result == out
    assert out.exists() and out.stat().st_size > 0
    _, errors = _decode(out.read_bytes())
    assert errors == []


def test_fit_has_single_session_spanning_lap():
    # Exactly one lap for the whole session (NOT one per climb).
    session = _multi_climb_session()
    messages, errors = _decode(build_fit_bytes(session))
    assert errors == []
    assert len(messages["lap_mesgs"]) == 1
    assert messages["session_mesgs"][0]["num_laps"] == 1


def test_fit_has_calories_and_training_effect():
    from kilter_garmin_sync.metrics import compute_metrics

    session = _multi_climb_session()
    metrics = compute_metrics(session)
    messages, _ = _decode(build_fit_bytes(session, metrics=metrics))
    session_msg = messages["session_mesgs"][0]
    assert session_msg["total_calories"] == metrics.calories
    assert session_msg["total_training_effect"] == pytest.approx(metrics.aerobic_te)
    assert session_msg["total_anaerobic_training_effect"] == pytest.approx(
        metrics.anaerobic_te
    )
    # The single lap carries the full session calorie total.
    assert messages["lap_mesgs"][0]["total_calories"] == metrics.calories


def test_fit_lap_carries_sub_sport():
    messages, _ = _decode(
        build_fit_bytes(_multi_climb_session(), sub_sport="indoor_climbing")
    )
    assert messages["lap_mesgs"][0]["sub_sport"] == "indoor_climbing"


def test_fit_has_one_climb_active_split_per_climb():
    session = _multi_climb_session()
    messages, errors = _decode(build_fit_bytes(session))
    assert errors == []
    splits = messages["split_mesgs"]
    assert len(splits) == len(session.ascents)
    assert all(sp["split_type"] == "climb_active" for sp in splits)


def test_fit_splits_have_no_climb_send_field():
    # Field 73 (climb_send) is deliberately omitted; the watch never sets it.
    messages, _ = _decode(build_fit_bytes(_multi_climb_session()))
    for sp in messages["split_mesgs"]:
        assert 73 not in sp


def test_fit_split_summary_num_splits_is_route_count():
    session = _multi_climb_session()
    messages, _ = _decode(build_fit_bytes(session))
    summaries = messages["split_summary_mesgs"]
    assert len(summaries) == 1
    assert summaries[0]["split_type"] == "climb_active"
    assert summaries[0]["num_splits"] == len(session.ascents)


def test_fit_split_summary_max_grade():
    # Hardest of V4/V5/V6/V3 is V6 -> vermin enum 7 (field 55).
    messages, _ = _decode(build_fit_bytes(_multi_climb_session()))
    assert messages["split_summary_mesgs"][0][55] == 7


def test_fit_splits_carry_vermin_grades():
    # V4 -> vermin enum 5, V5 -> 6, V6 -> 7, V3 -> 4 (raw = V + 1).
    messages, _ = _decode(build_fit_bytes(_multi_climb_session()))
    grades = [sp.get(70) for sp in messages["split_mesgs"]]
    assert grades == [5, 6, 7, 4]
    # Every split declares the V-scale (vermin) grading scale enum = 8.
    scales = [sp.get(69) for sp in messages["split_mesgs"]]
    assert scales == [8, 8, 8, 8]


def test_fit_split_max_difficulty_is_hardest_route():
    messages, _ = _decode(build_fit_bytes(_multi_climb_session()))
    grade_values = [sp[70] for sp in messages["split_mesgs"] if 70 in sp]
    assert max(grade_values) == 7


def test_fit_split_send_vs_attempt_status():
    ascents = [
        make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4", is_ascent=True),
        make_ascent(when=datetime(2026, 3, 1, 9, 30), grade="V5", is_ascent=False),
    ]
    session = group_sessions(ascents, min_duration_minutes=1)[0]
    messages, _ = _decode(build_fit_bytes(session))
    splits = messages["split_mesgs"]
    # Field 71 = split_status: 3 completed (send) / 2 attempted.
    assert splits[0][71] == 3
    assert splits[1][71] == 2
    # summary field 59 = completed, 58 = attempted.
    summary = messages["split_summary_mesgs"][0]
    assert summary[59] == 1
    assert summary[58] == 1


def test_fit_unrated_climb_counts_as_route_without_grade():
    ascents = [
        make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4"),
        make_ascent(when=datetime(2026, 3, 1, 9, 30), grade=None),
    ]
    session = group_sessions(ascents, min_duration_minutes=1)[0]
    messages, errors = _decode(build_fit_bytes(session))
    assert errors == []
    splits = messages["split_mesgs"]
    assert len(splits) == 2
    assert messages["split_summary_mesgs"][0]["num_splits"] == 2
    # Unrated climb still declares a grading scale but carries no grade value.
    assert 70 not in splits[1]
