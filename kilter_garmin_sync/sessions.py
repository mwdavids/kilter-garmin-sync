"""Group individual ascents into climbing sessions.

Grouping rule (firm): all ascents logged on the same local calendar day form
exactly one session. There is deliberately no idle-gap splitting.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import groupby
from typing import Iterable

from .models import Ascent, Session


def group_sessions(
    ascents: Iterable[Ascent],
    *,
    min_duration_minutes: float = 10.0,
    ascents_only: bool = False,
    since: date | None = None,
) -> list[Session]:
    """Group ascents into one session per calendar day.

    Args:
        ascents: The ascents to group.
        min_duration_minutes: Minimum session length; shorter windows (e.g. a
            single ascent) are padded up to this so the activity has a sensible
            non-zero duration.
        ascents_only: If True, drop attempt-only entries (``is_ascent`` false).
        since: If set, keep only sessions whose day is on/after this date.

    Returns:
        Sessions sorted by start time, one per calendar day.
    """
    items = [a for a in ascents if a.is_ascent or not ascents_only]
    items.sort(key=lambda a: a.date)

    min_duration = timedelta(minutes=min_duration_minutes)
    sessions: list[Session] = []
    for _day, day_iter in groupby(items, key=lambda a: a.date.date()):
        group = list(day_iter)
        start = min(a.date for a in group)
        end = max(a.date for a in group)
        if end - start < min_duration:
            end = start + min_duration
        sessions.append(Session(ascents=group, start=start, end=end))

    if since is not None:
        sessions = [s for s in sessions if s.day >= since]

    sessions.sort(key=lambda s: s.start)
    return sessions
