"""Tests for the HR-free effort metrics."""

from __future__ import annotations

from datetime import datetime

from kilter_garmin_sync.metrics import (
    DEFAULT_MET,
    LB_TO_KG,
    compute_metrics,
    effort_score,
    estimated_calories,
    grade_value,
    suggested_rpe,
    training_effect,
)
from kilter_garmin_sync.sessions import group_sessions

from conftest import make_ascent


def _session(ascents):
    return group_sessions(ascents)[0]


def test_grade_value_parses_v_scale():
    assert grade_value("V4") == 4
    assert grade_value("V10") == 10
    assert grade_value(None) is None
    assert grade_value("project") is None


def test_calories_match_met_formula():
    session = _session(
        [
            make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4"),
            make_ascent(when=datetime(2026, 3, 1, 11, 0), grade="V5"),
        ]
    )
    # Exactly 2.0 hours.
    expected = round(DEFAULT_MET * (180.0 * LB_TO_KG) * 2.0)
    assert estimated_calories(session) == expected
    # Weight scales the estimate linearly.
    assert estimated_calories(session, weight_lb=90.0) == round(expected / 2)


def test_effort_weights_sends_over_attempts():
    session = _session(
        [
            make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4"),  # send: 5
            make_ascent(
                when=datetime(2026, 3, 1, 9, 30),
                grade="V4",
                is_ascent=False,
            ),  # attempt: 2.5
        ]
    )
    # 5 + 2.5 = 7.5 -> round -> 8
    assert effort_score(session) == 8


def test_effort_unrated_uses_baseline():
    session = _session(
        [
            make_ascent(when=datetime(2026, 3, 1, 9, 0), grade=""),
            make_ascent(when=datetime(2026, 3, 1, 9, 30), grade=""),
        ]
    )
    # Two sends of unrated climbs -> baseline weight 1 each -> 2.
    assert effort_score(session) == 2


def test_suggested_rpe_is_clamped():
    tiny = _session([make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V0")])
    assert suggested_rpe(tiny) == 1  # never below 1
    hard = _session(
        [make_ascent(when=datetime(2026, 3, 1, 9, i), grade="V12") for i in range(0, 50, 5)]
    )
    assert 1 <= suggested_rpe(hard) <= 10
    assert suggested_rpe(hard) == 10  # very high volume clamps at 10


def test_training_effect_within_range():
    session = _session(
        [
            make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V8"),
            make_ascent(when=datetime(2026, 3, 1, 11, 0), grade="V6"),
        ]
    )
    aerobic, anaerobic = training_effect(session)
    assert 0.0 <= aerobic <= 5.0
    assert 0.0 <= anaerobic <= 5.0
    # Bouldering: anaerobic should dominate for a hard session.
    assert anaerobic > aerobic


def test_compute_metrics_bundles_values():
    session = _session(
        [
            make_ascent(when=datetime(2026, 3, 1, 9, 0), grade="V4"),
            make_ascent(when=datetime(2026, 3, 1, 10, 0), grade="V5"),
        ]
    )
    m = compute_metrics(session, weight_lb=200.0, met=9.0)
    assert m.weight_lb == 200.0
    assert m.met == 9.0
    assert m.calories == estimated_calories(session, weight_lb=200.0, met=9.0)
    assert m.effort == effort_score(session)
    assert m.rpe == suggested_rpe(session)
