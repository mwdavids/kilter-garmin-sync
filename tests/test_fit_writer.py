"""Tests for FIT generation: build a file and parse it back with the Garmin SDK."""

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


def test_fit_is_valid_and_parses_back():
    messages, errors = _decode(build_fit_bytes(_session()))
    assert errors == []
    assert "file_id_mesgs" in messages
    assert messages["file_id_mesgs"][0]["type"] == "activity"
    assert len(messages["session_mesgs"]) == 1
    assert len(messages["activity_mesgs"]) == 1


def test_fit_has_climbing_sport():
    messages, _ = _decode(build_fit_bytes(_session(), sub_sport="bouldering"))
    session = messages["session_mesgs"][0]
    assert session["sport"] == "rock_climbing"
    assert session["sub_sport"] == "bouldering"


def test_fit_indoor_climbing_subsport():
    messages, _ = _decode(build_fit_bytes(_session(), sub_sport="indoor_climbing"))
    assert messages["session_mesgs"][0]["sub_sport"] == "indoor_climbing"


def test_fit_elapsed_time_matches_duration():
    session = _session(datetime(2026, 3, 1, 9, 0), datetime(2026, 3, 1, 9, 45))
    messages, _ = _decode(build_fit_bytes(session))
    assert messages["session_mesgs"][0]["total_elapsed_time"] == pytest.approx(45 * 60)


def test_fit_activity_is_manual_single_session():
    messages, _ = _decode(build_fit_bytes(_session()))
    activity = messages["activity_mesgs"][0]
    assert activity["num_sessions"] == 1
    assert activity["type"] == "manual"


def test_invalid_sub_sport_raises():
    with pytest.raises(ValueError):
        build_fit_bytes(_session(), sub_sport="nope")


def test_write_fit_creates_file(tmp_path):
    out = tmp_path / "nested" / "kilter-2026-03-01.fit"
    result = write_fit(_session(), out)
    assert result == out
    assert out.exists() and out.stat().st_size > 0
    messages, errors = _decode(out.read_bytes())
    assert errors == []


def _multi_climb_session():
    ascents = [
        make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4"),
        make_ascent(when=datetime(2026, 3, 1, 9, 30), grade="V5"),
        make_ascent(when=datetime(2026, 3, 1, 10, 0), grade="V6"),
        make_ascent(when=datetime(2026, 3, 1, 11, 0), grade="V3"),
    ]
    return group_sessions(ascents, min_duration_minutes=1)[0]


def test_fit_has_one_lap_per_climb():
    session = _multi_climb_session()
    messages, errors = _decode(build_fit_bytes(session))
    assert errors == []
    assert len(messages["lap_mesgs"]) == len(session.ascents)
    assert messages["session_mesgs"][0]["num_laps"] == len(session.ascents)


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
    # Per-lap calories sum back to the session total.
    lap_calories = sum(lap.get("total_calories", 0) for lap in messages["lap_mesgs"])
    assert lap_calories == metrics.calories


def test_fit_laps_carry_sub_sport():
    messages, _ = _decode(
        build_fit_bytes(_multi_climb_session(), sub_sport="indoor_climbing")
    )
    for lap in messages["lap_mesgs"]:
        assert lap["sub_sport"] == "indoor_climbing"
