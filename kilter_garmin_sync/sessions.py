"""Group individual ascents into climbing sessions.

Default behaviour: all ascents on the same calendar day form one session.
Optionally, ``gap_hours`` splits a day into multiple sessions whenever the idle
gap between consecutive (time-stamped) ascents exceeds the threshold.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import groupby
from typing import Iterable

from .models import Ascent, Session


def group_sessions(
    ascents: Iterable[Ascent],
    *,
    gap_hours: float | None = None,
    min_duration_minutes: float = 10.0,
    ascents_only: bool = False,
    since: date | None = None,
) -> list[Session]:
    """Group ascents into sessions.

    Args:
        ascents: The ascents to group.
        gap_hours: If set, split a calendar day into multiple sessions whenever
            the gap between consecutive ascents exceeds this many hours. If
            ``None`` (default), each calendar day is a single session.
        min_duration_minutes: Minimum session length; shorter windows (e.g. a
            single ascent) are padded up to this so the activity has a sensible
            non-zero duration.
        ascents_only: If True, drop attempt-only entries (``is_ascent`` false).
        since: If set, keep only sessions whose day is on/after this date.

    Returns:
        Sessions sorted by start time.
    """
    items = [a for a in ascents if a.is_ascent or not ascents_only]
    items.sort(key=lambda a: a.date)

    raw_groups: list[list[Ascent]] = []
    for _day, day_iter in groupby(items, key=lambda a: a.date.date()):
        day_items = list(day_iter)
        if gap_hours is None:
            raw_groups.append(day_items)
            continue

        gap = timedelta(hours=gap_hours)
        current = [day_items[0]]
        for prev, cur in zip(day_items, day_items[1:]):
            if cur.date - prev.date > gap:
                raw_groups.append(current)
                current = [cur]
            else:
                current.append(cur)
        raw_groups.append(current)

    min_duration = timedelta(minutes=min_duration_minutes)
    sessions: list[Session] = []
    for group in raw_groups:
        start = min(a.date for a in group)
        end = max(a.date for a in group)
        if end - start < min_duration:
            end = start + min_duration
        sessions.append(Session(ascents=list(group), start=start, end=end))

    if since is not None:
        sessions = [s for s in sessions if s.day >= since]

    sessions.sort(key=lambda s: s.start)
    return sessions
