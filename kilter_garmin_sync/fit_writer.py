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

from .models import Session

SUB_SPORTS = {
    "bouldering": SubSport.BOULDERING,
    "indoor_climbing": SubSport.INDOOR_CLIMBING,
}


def _ms(t: dt.datetime) -> int:
    """Milliseconds since the Unix epoch, as fit-tool expects for time fields."""
    return round(t.timestamp() * 1000)


def build_fit_bytes(session: Session, *, sub_sport: str = "bouldering") -> bytes:
    """Build a FIT activity file (as bytes) for one session."""
    if sub_sport not in SUB_SPORTS:
        raise ValueError(
            f"Unknown sub_sport {sub_sport!r}; expected one of {sorted(SUB_SPORTS)}"
        )
    sub = SUB_SPORTS[sub_sport]

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

    # Minimal records (timestamp only, no HR/GPS) bracket the activity so
    # importers reliably accept a non-empty timeline.
    for t in (start, end):
        record = RecordMessage()
        record.timestamp = _ms(t)
        builder.add(record)

    stop_event = EventMessage()
    stop_event.event = Event.TIMER
    stop_event.event_type = EventType.STOP_ALL
    stop_event.timestamp = _ms(end)
    builder.add(stop_event)

    lap = LapMessage()
    lap.message_index = 0
    lap.timestamp = _ms(end)
    lap.start_time = _ms(start)
    lap.total_elapsed_time = elapsed
    lap.total_timer_time = elapsed
    lap.sport = Sport.ROCK_CLIMBING
    lap.sub_sport = sub
    lap.event = Event.LAP
    lap.event_type = EventType.STOP
    lap.lap_trigger = LapTrigger.SESSION_END
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
    session_msg.num_laps = 1
    session_msg.event = Event.SESSION
    session_msg.event_type = EventType.STOP
    session_msg.trigger = SessionTrigger.ACTIVITY_END
    session_msg.total_distance = 0.0
    session_msg.total_calories = 0
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


def write_fit(session: Session, path: str | Path, *, sub_sport: str = "bouldering") -> Path:
    """Write a FIT file for the session and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_fit_bytes(session, sub_sport=sub_sport))
    return path
