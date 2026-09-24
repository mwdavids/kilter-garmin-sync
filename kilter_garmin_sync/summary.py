"""Build human-readable titles and notes describing a climbing session."""

from __future__ import annotations

import re

from .metrics import SessionMetrics, compute_metrics
from .models import Ascent, Session


def _grade_sort_key(grade: str) -> tuple[int, float, str]:
    """Sort key for grades.

    Handles V-scale (``V4``, ``V10``) and font/other scales gracefully by
    extracting a leading number where present. Non-numeric grades sort last,
    then alphabetically, so the summary stays deterministic.
    """
    match = re.search(r"\d+", grade)
    if match:
        return (0, float(match.group()), grade)
    return (1, 0.0, grade)


def _grade_range(ascents: list[Ascent]) -> str | None:
    grades = [a.grade for a in ascents if a.grade]
    if not grades:
        return None
    ordered = sorted(set(grades), key=_grade_sort_key)
    if len(ordered) == 1:
        return ordered[0]
    return f"{ordered[0]}\u2013{ordered[-1]}"  # en dash


def hardest_grade(ascents: list[Ascent]) -> str | None:
    """The hardest grade among the given ascents (numeric-aware)."""
    grades = [a.grade for a in ascents if a.grade]
    if not grades:
        return None
    return max(grades, key=_grade_sort_key)


def grade_histogram(ascents: list[Ascent]) -> list[tuple[str, int]]:
    """Count of ascents per grade, ordered hardest-first."""
    counts: dict[str, int] = {}
    for ascent in ascents:
        if ascent.grade:
            counts[ascent.grade] = counts.get(ascent.grade, 0) + 1
    return sorted(counts.items(), key=lambda kv: _grade_sort_key(kv[0]), reverse=True)


def _histogram_text(ascents: list[Ascent]) -> str | None:
    hist = grade_histogram(ascents)
    if not hist:
        return None
    return ", ".join(f"{grade}\u00d7{count}" for grade, count in hist)


def _duration_text(session: Session) -> str:
    total_minutes = int(round(session.duration.total_seconds() / 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _angles_text(session: Session) -> str | None:
    angles = session.angles
    if not angles:
        return None
    return ", ".join(f"{a}\u00b0" for a in angles)


def _counts_text(session: Session) -> str:
    n = len(session.ascents)
    sends = len(session.sends)
    attempts = len(session.attempts)
    label = f"{n} climb{'s' if n != 1 else ''}"
    parts = []
    if sends:
        parts.append(f"{sends} send{'s' if sends != 1 else ''}")
    if attempts:
        parts.append(f"{attempts} attempt{'s' if attempts != 1 else ''}")
    return f"{label} ({', '.join(parts)})" if parts else label


def session_title(session: Session, metrics: SessionMetrics | None = None) -> str:
    """A one-line title, e.g.
    ``Kilter Board - 5 climbs (4 sends, 1 attempt), 40 deg, V4-V8, hardest V8, effort 32``.
    """
    if metrics is None:
        metrics = compute_metrics(session)
    parts = [f"Kilter Board \u2014 {_counts_text(session)}"]
    angles = _angles_text(session)
    if angles:
        parts.append(angles)
    grades = _grade_range(session.ascents)
    if grades:
        parts.append(grades)
    hardest = hardest_grade(session.sends) or hardest_grade(session.ascents)
    if hardest:
        parts.append(f"hardest {hardest}")
    parts.append(f"effort {metrics.effort}")
    return ", ".join(parts)


def _entry_line(ascent: Ascent) -> str:
    bits = [ascent.climb_name or "(unnamed)"]
    detail = []
    if ascent.grade:
        detail.append(ascent.grade)
    if ascent.angle is not None:
        detail.append(f"{ascent.angle}\u00b0")
    if ascent.is_mirror:
        detail.append("mirror")
    line = bits[0]
    if detail:
        line += f" ({', '.join(detail)})"
    if ascent.tries:
        line += f" \u2014 {ascent.tries} tr{'y' if ascent.tries == 1 else 'ies'}"
    if ascent.comment:
        line += f" \u2014 {ascent.comment}"
    return line


def session_notes(session: Session, metrics: SessionMetrics | None = None) -> str:
    """A multi-line description suitable for a TCX ``<Notes>`` field."""
    if metrics is None:
        metrics = compute_metrics(session)
    sends = session.sends
    attempts = session.attempts

    lines = [
        session_title(session, metrics),
        f"Date: {session.start:%Y-%m-%d}",
        f"Time: {session.start:%H:%M}\u2013{session.end:%H:%M} ({_duration_text(session)})",
    ]

    hardest = hardest_grade(sends)
    if hardest:
        lines.append(f"Hardest send: {hardest}")
    histogram = _histogram_text(sends)
    if histogram:
        lines.append(f"Sends by grade: {histogram}")

    lines.append("")
    lines.append(
        f"Estimated calories: ~{metrics.calories} kcal "
        f"(MET {metrics.met:g} \u00d7 {metrics.weight_lb:g} lb, no HR data \u2014 estimate)"
    )
    lines.append(f"Effort score: {metrics.effort} (grade-weighted volume)")
    lines.append(
        f"Estimated Training Effect: aerobic {metrics.aerobic_te:.1f}, "
        f"anaerobic {metrics.anaerobic_te:.1f} (estimate, no HR)"
    )
    lines.append(
        f"Suggested RPE: {metrics.rpe} \u2014 set this in Garmin Connect for training load"
    )
    lines.append("")

    if sends:
        lines.append("Sends:")
        lines.extend(f"- {_entry_line(a)}" for a in sends)
    if attempts:
        if sends:
            lines.append("")
        lines.append("Attempts:")
        lines.extend(f"- {_entry_line(a)}" for a in attempts)

    lines.append("")
    lines.append("Logged from Kilter Board via kilter-garmin-sync.")
    return "\n".join(lines)
