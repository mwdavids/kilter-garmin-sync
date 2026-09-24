"""Generate Garmin-importable FIT activity files for climbing sessions.

Garmin Connect's manual-FIT importer is picky: a from-scratch "minimal valid"
activity is accepted at upload (it returns an uploadId) but then **silently
dropped** -- no activity is ever created. To import reliably, the file has to
carry the same identity + message envelope a real Garmin watch emits for a
bouldering activity.

This module reproduces that proven-importable structure (verified end-to-end
against a live Garmin account): a Garmin ``file_id`` + ``file_creator``, a
``sport`` message, one ``split`` per logged climb (carrying the per-route
V-grade), a single session-spanning ``lap``, one ``session`` and one
``activity``. The constant "envelope" fields are captured from a real watch file
and shipped as ``data/garmin_template.json``; only the timing, calories, grades
and counts are filled in per session.

Note on Garmin's summary tiles: a bouldering activity's aggregate **Total
Routes** / **Max Grade** tiles come from a ``splitSummaries`` block that Garmin
computes *only* in its device-sync ingestion pipeline, **not** for manually
uploaded FIT files. (Re-uploading a real watch file byte-for-byte via the upload
API still yields ``splitSummaries: None``.) So an uploaded file shows the
individual routes with their grades -- which works well -- but not the aggregate
tiles. We still emit a clean ``split_summary`` for completeness/future-proofing.
"""

from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

from fit_tool.base_type import BaseType
from fit_tool.field import Field
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_creator_message import FileCreatorMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.split_message import SplitMessage
from fit_tool.profile.messages.split_summary_message import SplitSummaryMessage
from fit_tool.profile.messages.sport_message import SportMessage
from fit_tool.profile.profile_type import SplitType, SubSport

from .metrics import SessionMetrics, compute_metrics, grade_value
from .models import Session

SUB_SPORTS = {
    "bouldering": SubSport.BOULDERING,
    "indoor_climbing": SubSport.INDOOR_CLIMBING,
}

_TEMPLATE_PATH = Path(__file__).with_name("data") / "garmin_template.json"

# FIT epoch (1989-12-31 UTC) as Unix seconds. Some raw date_time fields store
# seconds since this epoch directly (fit-tool does not auto-convert them).
_FIT_EPOCH_SECONDS = 631065600

# Raw climbing fields on the FIT `split` message (global mesg 312), newer than
# the fields fit-tool / garmin-fit-sdk name, so written by field number:
#   253 timestamp     (uint32; session start in FIT-epoch seconds, constant)
#    69 climb_grading_scale (enum; 8 = vermin / V-scale)
#    70 climb_grade_value    (uint32; vermin enum, VB=0 V0=1 V1=2 ...)
#    71 status               (enum; 2 = attempted, 3 = completed/sent)
FIELD_TIMESTAMP = 253
FIELD_CLIMB_GRADING_SCALE = 69
FIELD_CLIMB_GRADE_VALUE = 70
FIELD_SPLIT_STATUS = 71
# Deliberately NOT emitted: field 73 (climb_send). The watch never sets it and
# it is not needed for import.
FIELD_SUB_SPORT_SPLIT = 12  # sub_sport constant carried on each split

# climb_grading_scale enum value for the V-scale (a.k.a. Vermin/Hueco).
GRADING_SCALE_VERMIN = 8
SPLIT_STATUS_CLIMB_ATTEMPTED = 2
SPLIT_STATUS_CLIMB_COMPLETED = 3
# vermin_grading_scale caps at V17 (enum 18); VB is 0, so raw = V + 1.
VERMIN_MAX_ENUM = 18


def _vermin_enum(grade: str | None) -> int | None:
    """Map a V-scale grade to the FIT ``vermin_grading_scale`` enum.

    ``V0`` -> 1, ``V4`` -> 5, ... (VB -> 0). Non-V/unrated grades return
    ``None`` so the climb still counts as a route but carries no difficulty.
    """
    v = grade_value(grade)
    if v is None:
        return None
    return max(0, min(VERMIN_MAX_ENUM, v + 1))


@lru_cache(maxsize=1)
def _load_template() -> dict:
    """Load the watch-derived envelope template, resolving base-type names.

    Returns a dict of section -> {field_id: (BaseType, value)}.
    """
    raw = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    template: dict = {}
    for section, fields in raw.items():
        if section.startswith("_"):
            continue
        template[section] = {
            int(fid): (BaseType[bt], value) for fid, (bt, value) in fields.items()
        }
    return template


def _raw(msg, field_id: int, base_type: BaseType, value) -> None:
    """Append a raw FIT field (by global number) and set its value."""
    msg.fields.append(
        Field(field_id=field_id, name=f"field_{field_id}", base_type=base_type,
              size=0, growable=True)
    )
    msg.get_field(field_id).set_value(0, value)


def _strip_empty(msg) -> None:
    """Drop fields that carry no value so they aren't serialized."""
    for f in list(msg.fields):
        fid = getattr(f, "field_id", None)
        try:
            vals = f.get_values()
        except Exception:  # noqa: BLE001 - fit-tool raises varied errors here
            vals = None
        if not vals:
            msg.remove_field(fid)


def _inherit(cls, values: dict):
    """Build a message from a template dict, using named fields where possible.

    For each ``field_id -> (base_type, value)`` set the message's named field if
    it has one (so fit-tool applies the right scaling), else attach it raw.
    """
    msg = cls()
    for fid, (bt, value) in values.items():
        named = msg.get_field(fid)
        if named is not None:
            named.set_value(0, value)
        else:
            _raw(msg, fid, bt, value)
    return msg


def _ms(t: dt.datetime) -> int:
    """Milliseconds since the Unix epoch, as fit-tool expects for time fields.

    For timezone-aware datetimes ``t.timestamp()`` yields the correct UTC epoch
    regardless of the host's system timezone; for naive datetimes Python treats
    the value as system-local time (the offline ``--from-csv`` path).
    """
    return round(t.timestamp() * 1000)


def _utc_offset_seconds(t: dt.datetime) -> int:
    """UTC offset (seconds) for ``t`` (DST-aware for aware datetimes)."""
    offset = t.utcoffset()
    if offset is None:
        offset = t.astimezone().utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def _fit_seconds(t: dt.datetime) -> int:
    """Whole seconds since the FIT epoch for ``t`` (true UTC instant)."""
    return round(t.timestamp()) - _FIT_EPOCH_SECONDS


def _lap_bounds(session: Session) -> list[tuple[dt.datetime, dt.datetime]]:
    """One (start, end) window per climb.

    Kilter does not record per-climb durations, so we estimate by distributing
    the session window evenly across the climbs, in chronological order.
    """
    n = len(session.ascents)
    if n == 0:
        return []
    total = (session.end - session.start).total_seconds()
    step = total / n
    bounds = []
    for i in range(n):
        lap_start = session.start + dt.timedelta(seconds=step * i)
        lap_end = (
            session.end
            if i == n - 1
            else session.start + dt.timedelta(seconds=step * (i + 1))
        )
        bounds.append((lap_start, lap_end))
    return bounds


def build_fit_bytes(
    session: Session,
    *,
    sub_sport: str = "bouldering",
    metrics: SessionMetrics | None = None,
) -> bytes:
    """Build a Garmin-importable FIT activity file (as bytes) for one session.

    Reproduces the message envelope a Garmin watch emits for a bouldering
    activity (see module docstring). One ``split`` is written per logged climb
    carrying its V-grade and send/attempt status; a single session-spanning
    ``lap`` and one ``session`` carry the MET-based calorie estimate.
    """
    if sub_sport not in SUB_SPORTS:
        raise ValueError(
            f"Unknown sub_sport {sub_sport!r}; expected one of {sorted(SUB_SPORTS)}"
        )
    sub_value = SUB_SPORTS[sub_sport].value
    if metrics is None:
        metrics = compute_metrics(session)

    tmpl = _load_template()
    start, end = session.start, session.end
    start_ms, end_ms = _ms(start), _ms(end)
    elapsed = (end - start).total_seconds()
    sess_fit_secs = _fit_seconds(start)
    bounds = _lap_bounds(session)
    n_climbs = len(bounds)
    per_climb_cal = metrics.calories // n_climbs if n_climbs else 0

    builder = FitFileBuilder(auto_define=True)

    # --- file_id: identify as a real Garmin watch so the importer trusts it ---
    file_id = _inherit(FileIdMessage, tmpl["fileid"])
    file_id.time_created = start_ms
    _strip_empty(file_id)
    builder.add(file_id)

    builder.add(_inherit(FileCreatorMessage, tmpl["creator"]))

    # --- sport: rock_climbing + bouldering (override sub_sport if requested) ---
    sport = _inherit(SportMessage, tmpl["sport"])
    sport.get_field(1).set_value(0, sub_value)
    builder.add(sport)

    start_event = _inherit(EventMessage, tmpl["event"])
    start_event.timestamp = start_ms
    _strip_empty(start_event)
    builder.add(start_event)

    # --- one split per climb ---
    max_grade: int | None = None
    n_completed = n_attempted = 0
    active_timer = 0.0
    for i, ((c_start, c_end), ascent) in enumerate(zip(bounds, session.ascents)):
        c_elapsed = (c_end - c_start).total_seconds()
        c_cal = (
            per_climb_cal + (metrics.calories - per_climb_cal * n_climbs)
            if i == n_climbs - 1
            else per_climb_cal
        )
        split = SplitMessage()
        split.message_index = i
        split.split_type = SplitType.CLIMB_ACTIVE
        split.start_time = _ms(c_start)
        split.end_time = _ms(c_end)
        split.total_elapsed_time = c_elapsed
        split.total_timer_time = c_elapsed
        split.total_calories = c_cal
        _raw(split, FIELD_TIMESTAMP, BaseType.UINT32, sess_fit_secs)
        _raw(split, FIELD_CLIMB_GRADING_SCALE, BaseType.ENUM, GRADING_SCALE_VERMIN)
        vermin = _vermin_enum(ascent.grade)
        if vermin is not None:
            _raw(split, FIELD_CLIMB_GRADE_VALUE, BaseType.UINT32, vermin)
            max_grade = vermin if max_grade is None else max(max_grade, vermin)
        status = (
            SPLIT_STATUS_CLIMB_COMPLETED
            if ascent.is_ascent
            else SPLIT_STATUS_CLIMB_ATTEMPTED
        )
        _raw(split, FIELD_SPLIT_STATUS, BaseType.ENUM, status)
        if ascent.is_ascent:
            n_completed += 1
        else:
            n_attempted += 1
        # Constant "envelope" fields the watch stamps on each split.
        for cf, (bt, v) in sorted(tmpl["split_const_fields"].items()):
            _raw(split, cf, bt, sub_value if cf == FIELD_SUB_SPORT_SPLIT else v)
        _strip_empty(split)
        builder.add(split)
        active_timer += c_elapsed

    # --- one clean split_summary (climb_active) ---
    if n_climbs:
        summary = SplitSummaryMessage()
        summary.message_index = 0
        summary.split_type = SplitType.CLIMB_ACTIVE
        summary.num_splits = n_climbs
        summary.total_timer_time = active_timer
        summary.total_calories = metrics.calories
        _raw(summary, FIELD_TIMESTAMP, BaseType.UINT32, sess_fit_secs)
        _raw(summary, 54, BaseType.ENUM, GRADING_SCALE_VERMIN)
        if max_grade is not None:
            _raw(summary, 55, BaseType.UINT32, max_grade)
        _raw(summary, 58, BaseType.UINT16, n_attempted)
        _raw(summary, 59, BaseType.UINT16, n_completed)
        _strip_empty(summary)
        builder.add(summary)

    # --- one session-spanning lap (NOT one per climb) ---
    lap = _inherit(LapMessage, tmpl["lap"])
    lap.message_index = 0
    lap.start_time = start_ms
    lap.timestamp = end_ms
    lap.total_elapsed_time = elapsed
    lap.total_timer_time = elapsed
    lap.total_calories = metrics.calories
    lap.get_field(39).set_value(0, sub_value)
    _strip_empty(lap)
    builder.add(lap)

    # --- session ---
    session_msg = _inherit(SessionMessage, tmpl["session"])
    session_msg.message_index = 0
    session_msg.start_time = start_ms
    session_msg.timestamp = end_ms
    session_msg.total_elapsed_time = elapsed
    session_msg.total_timer_time = elapsed
    session_msg.total_calories = metrics.calories
    session_msg.get_field(6).set_value(0, sub_value)
    session_msg.num_laps = 1
    # Estimated Training Effect (honest estimates; fields already in the envelope).
    session_msg.get_field(24).set_value(0, metrics.aerobic_te)
    if session_msg.get_field(137) is not None:
        session_msg.get_field(137).set_value(0, metrics.anaerobic_te)
    else:
        _raw(session_msg, 137, BaseType.UINT8, metrics.anaerobic_te)
    _strip_empty(session_msg)
    builder.add(session_msg)

    # --- activity (with DST-aware local_timestamp so Garmin shows local time) ---
    activity = _inherit(ActivityMessage, tmpl["activity"])
    activity.timestamp = end_ms
    activity.total_timer_time = elapsed
    activity.num_sessions = 1
    local_secs = _fit_seconds(end) + _utc_offset_seconds(end)
    activity.get_field(5).set_value(0, local_secs)
    _strip_empty(activity)
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
