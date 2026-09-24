"""Generate Garmin-importable FIT activity files for climbing sessions.

Uses ``fit-tool`` to write a minimal but valid activity FIT file with the native
climbing activity type (sport ``rock_climbing`` + a climbing sub-sport). There is
no HR/GPS data -- the file carries timing + activity type only, which is all
Garmin Connect needs to import it as an indoor climbing / bouldering activity.

FIT has no free-text field that Garmin Connect surfaces, so the climb summary is
carried in the filename (and is available via TCX ``<Notes>`` if you export TCX
instead).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    Activity,
    Event,
    EventType,
    FileType,
    LapTrigger,
    Manufacturer,
    SessionTrigger,
    Sport,
    SubSport,
)

from .metrics import SessionMetrics, compute_metrics
from .models import Session

SUB_SPORTS = {
    "bouldering": SubSport.BOULDERING,
    "indoor_climbing": SubSport.INDOOR_CLIMBING,
}


def _ms(t: dt.datetime) -> int:
    """Milliseconds since the Unix epoch, as fit-tool expects for time fields."""
    return round(t.timestamp() * 1000)


def _lap_bounds(session: Session) -> list[tuple[dt.datetime, dt.datetime]]:
    """One (start, end) window per climb.

    Kilter does not record per-climb durations, so when precise per-climb timing
    isn't available we estimate by distributing the session window evenly across
    the climbs, in chronological order. The result is one lap per logged climb.
    """
    n = len(session.ascents)
    if n == 0:
        return []
    total = (session.end - session.start).total_seconds()
    step = total / n
    bounds = []
    for i in range(n):
        lap_start = session.start + dt.timedelta(seconds=step * i)
        lap_end = session.end if i == n - 1 else session.start + dt.timedelta(seconds=step * (i + 1))
        bounds.append((lap_start, lap_end))
    return bounds


def build_fit_bytes(
    session: Session,
    *,
    sub_sport: str = "bouldering",
    metrics: SessionMetrics | None = None,
) -> bytes:
    """Build a FIT activity file (as bytes) for one session.

    The file is enriched with MET-based ``total_calories``, estimated aerobic /
    anaerobic Training Effect, and one ``lap`` per logged climb. There is still
    no HR/GPS data (Kilter records none).
    """
    if sub_sport not in SUB_SPORTS:
        raise ValueError(
            f"Unknown sub_sport {sub_sport!r}; expected one of {sorted(SUB_SPORTS)}"
        )
    sub = SUB_SPORTS[sub_sport]
    if metrics is None:
        metrics = compute_metrics(session)

    start, end = session.start, session.end
    elapsed = (end - start).total_seconds()

    builder = FitFileBuilder(auto_define=True)

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.time_created = _ms(start)
    file_id.serial_number = 0x12345678
    builder.add(file_id)

    start_event = EventMessage()
    start_event.event = Event.TIMER
    start_event.event_type = EventType.START
    start_event.timestamp = _ms(start)
    builder.add(start_event)

    bounds = _lap_bounds(session)

    # A record at each lap boundary (timestamp only, no HR/GPS) gives importers a
    # non-empty timeline that spans the whole session.
    record_times = [start] + [b[1] for b in bounds]
    for t in record_times:
        record = RecordMessage()
        record.timestamp = _ms(t)
        builder.add(record)

    stop_event = EventMessage()
    stop_event.event = Event.TIMER
    stop_event.event_type = EventType.STOP_ALL
    stop_event.timestamp = _ms(end)
    builder.add(stop_event)

    # One lap per climb. Calories are distributed evenly across laps (remainder
    # on the last), matching the even time distribution.
    n_laps = len(bounds)
    per_lap_cal = metrics.calories // n_laps if n_laps else 0
    for i, (lap_start, lap_end) in enumerate(bounds):
        lap = LapMessage()
        lap.message_index = i
        lap.timestamp = _ms(lap_end)
        lap.start_time = _ms(lap_start)
        lap_elapsed = (lap_end - lap_start).total_seconds()
        lap.total_elapsed_time = lap_elapsed
        lap.total_timer_time = lap_elapsed
        lap.sport = Sport.ROCK_CLIMBING
        lap.sub_sport = sub
        lap.event = Event.LAP
        lap.event_type = EventType.STOP
        lap.lap_trigger = (
            LapTrigger.SESSION_END if i == n_laps - 1 else LapTrigger.MANUAL
        )
        lap.total_calories = (
            per_lap_cal + (metrics.calories - per_lap_cal * n_laps)
            if i == n_laps - 1
            else per_lap_cal
        )
        builder.add(lap)

    session_msg = SessionMessage()
    session_msg.message_index = 0
    session_msg.timestamp = _ms(end)
    session_msg.start_time = _ms(start)
    session_msg.total_elapsed_time = elapsed
    session_msg.total_timer_time = elapsed
    session_msg.sport = Sport.ROCK_CLIMBING
    session_msg.sub_sport = sub
    session_msg.first_lap_index = 0
    session_msg.num_laps = max(1, n_laps)
    session_msg.event = Event.SESSION
    session_msg.event_type = EventType.STOP
    session_msg.trigger = SessionTrigger.ACTIVITY_END
    session_msg.total_distance = 0.0
    session_msg.total_calories = metrics.calories
    session_msg.total_training_effect = metrics.aerobic_te
    session_msg.total_anaerobic_training_effect = metrics.anaerobic_te
    builder.add(session_msg)

    activity = ActivityMessage()
    activity.timestamp = _ms(end)
    activity.total_timer_time = elapsed
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    activity.event = Event.ACTIVITY
    activity.event_type = EventType.STOP
    builder.add(activity)

    return bytes(builder.build().to_bytes())


def write_fit(
    session: Session,
    path: str | Path,
    *,
    sub_sport: str = "bouldering",
    metrics: SessionMetrics | None = None,
) -> Path:
    """Write a FIT file for the session and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_fit_bytes(session, sub_sport=sub_sport, metrics=metrics))
    return path
