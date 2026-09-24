"""Data models for Kilter ascents and grouped climbing sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class Ascent:
    """A single logged entry from a Kilter logbook (a send or an attempt)."""

    board: str
    angle: int | None
    climb_name: str
    date: datetime
    logged_grade: str | None
    displayed_grade: str | None
    is_benchmark: bool
    tries: int | None
    is_mirror: bool
    is_ascent: bool
    comment: str

    @property
    def grade(self) -> str | None:
        """Preferred human grade: the displayed (consensus) grade, else logged."""
        return self.displayed_grade or self.logged_grade

    @property
    def has_time(self) -> bool:
        """Whether this entry carries a real time-of-day.

        BoardLib summarizes attempt-only "bids" to a date (midnight), so those
        entries have no precise time and should not drive gap splitting.
        """
        return not (
            self.date.hour == 0 and self.date.minute == 0 and self.date.second == 0
        )


@dataclass
class Session:
    """A climbing session: a set of ascents plus its (padded) time window."""

    ascents: list[Ascent]
    start: datetime
    end: datetime

    @property
    def day(self) -> date:
        return self.start.date()

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def sends(self) -> list[Ascent]:
        return [a for a in self.ascents if a.is_ascent]

    @property
    def attempts(self) -> list[Ascent]:
        return [a for a in self.ascents if not a.is_ascent]

    @property
    def angles(self) -> list[int]:
        return sorted({a.angle for a in self.ascents if a.angle is not None})
