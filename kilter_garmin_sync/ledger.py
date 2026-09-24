"""Export ledger: durable de-duplication of already-exported session days.

Each climbing session is one calendar day. The ledger records, per day, a
content **fingerprint** of that day's ascents plus the file that was written, so
re-running never regenerates a day that has already been exported — even on a
fresh CI runner, where the ledger is carried across runs via ``actions/cache``.

A day whose ascents changed since last export (e.g. more climbs logged later
that day) gets a *different* fingerprint and is reported as ``changed`` so the
user can decide to regenerate with ``--overwrite`` (re-importing to Garmin makes
a second activity, so this is opt-in rather than automatic).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Session

LEDGER_VERSION = 1


def session_fingerprint(session: Session) -> str:
    """A stable, order-independent hash of a day's meaningful ascent content.

    Changing which climbs were logged (name, angle, grade, send/attempt, tries,
    time) changes the fingerprint; re-running on identical data does not.
    """
    rows = sorted(
        (
            a.date.isoformat(),
            a.climb_name,
            "" if a.angle is None else str(a.angle),
            a.grade or "",
            "1" if a.is_ascent else "0",
            "" if a.tries is None else str(a.tries),
        )
        for a in session.ascents
    )
    digest = hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()
    return digest[:16]


class Ledger:
    """A JSON record of exported session days keyed by ``YYYY-MM-DD``."""

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self.entries: dict[str, dict[str, Any]] = entries or {}

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        """Load a ledger from ``path``; a missing/invalid file yields an empty one."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        entries = data.get("exported")
        if not isinstance(entries, dict):
            return cls()
        return cls(entries)

    def status(self, day_key: str, fingerprint: str) -> str:
        """Return ``"new"``, ``"unchanged"`` or ``"changed"`` for a day."""
        entry = self.entries.get(day_key)
        if entry is None:
            return "new"
        return "unchanged" if entry.get("fingerprint") == fingerprint else "changed"

    def record(self, day_key: str, fingerprint: str, filename: str, fmt: str) -> None:
        self.entries[day_key] = {
            "fingerprint": fingerprint,
            "file": filename,
            "format": fmt,
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": LEDGER_VERSION, "exported": self.entries}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
