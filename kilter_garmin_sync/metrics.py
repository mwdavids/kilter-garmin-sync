"""Derived, HR-free effort metrics for a climbing session.

Kilter logs carry no heart rate, so Garmin's automatic Training Load / Training
Effect cannot be *truly* computed. Rather than fabricate an HR stream (which
would pollute Garmin's HRV / resting-HR baselines), we derive a few honest,
clearly-labelled estimates from the real logged data: MET-based calories, a
grade-weighted effort score, a suggested RPE, and rough aerobic/anaerobic
Training Effect estimates.

All functions are pure and fixture-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Session

# Physical constants / defaults.
LB_TO_KG = 0.45359237
DEFAULT_WEIGHT_LB = 180.0
DEFAULT_MET = 8.0  # vigorous bouldering (compendium ~8.0)

# ~6 effort points per RPE point (a heuristic; RPE is inherently subjective).
EFFORT_PER_RPE = 6.0


def grade_value(grade: str | None) -> int | None:
    """Numeric V-scale value of a grade string (``"V4"`` -> ``4``), else ``None``."""
    if not grade:
        return None
    match = re.search(r"\d+", grade)
    return int(match.group()) if match else None


def _max_grade_value(session: Session) -> int | None:
    values = [grade_value(a.grade) for a in session.ascents]
    values = [v for v in values if v is not None]
    return max(values) if values else None


def duration_hours(session: Session) -> float:
    return session.duration.total_seconds() / 3600.0


def estimated_calories(
    session: Session,
    *,
    weight_lb: float = DEFAULT_WEIGHT_LB,
    met: float = DEFAULT_MET,
) -> int:
    """MET-based calorie estimate: ``MET x body_kg x hours`` (kcal)."""
    kg = weight_lb * LB_TO_KG
    return round(met * kg * duration_hours(session))


def effort_score(session: Session) -> int:
    """Grade-weighted volume score.

    Each send contributes its grade weight ``(V + 1)`` (so a V0 counts 1, a V5
    counts 6); attempts count at half weight. Climbs with no grade (unrated on
    the new backend) count a baseline weight of 1 so volume is never ignored.
    """
    total = 0.0
    for ascent in session.ascents:
        v = grade_value(ascent.grade)
        weight = (v + 1) if v is not None else 1.0
        total += weight if ascent.is_ascent else 0.5 * weight
    return round(total)


def suggested_rpe(session: Session) -> int:
    """A suggested session RPE on the 1-10 scale, from the effort score.

    RPE (rate of perceived exertion) is the real lever for manual training load
    on HR-less activities, so we surface a starting suggestion the user can set
    in Garmin Connect.
    """
    rpe = round(effort_score(session) / EFFORT_PER_RPE)
    return max(1, min(10, rpe))


def _clamp_te(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 1)


def training_effect(session: Session) -> tuple[float, float]:
    """Estimated (aerobic, anaerobic) Training Effect, each on Garmin's 0.0-5.0.

    Bouldering is highly anaerobic, so the anaerobic estimate is driven by max
    grade + send volume; the aerobic estimate is driven by session duration +
    total climbs. These are rough estimates (no HR), labelled as such.
    """
    max_v = _max_grade_value(session) or 0
    n = len(session.ascents)
    sends = len(session.sends)
    anaerobic = 1.0 + 0.3 * max_v + 0.06 * sends
    aerobic = 0.3 + 0.5 * duration_hours(session) + 0.04 * n
    return _clamp_te(aerobic), _clamp_te(anaerobic)


@dataclass
class SessionMetrics:
    """Bundle of derived metrics for one session."""

    calories: int
    effort: int
    rpe: int
    aerobic_te: float
    anaerobic_te: float
    weight_lb: float
    met: float


def compute_metrics(
    session: Session,
    *,
    weight_lb: float = DEFAULT_WEIGHT_LB,
    met: float = DEFAULT_MET,
) -> SessionMetrics:
    aerobic, anaerobic = training_effect(session)
    return SessionMetrics(
        calories=estimated_calories(session, weight_lb=weight_lb, met=met),
        effort=effort_score(session),
        rpe=suggested_rpe(session),
        aerobic_te=aerobic,
        anaerobic_te=anaerobic,
        weight_lb=weight_lb,
        met=met,
    )
