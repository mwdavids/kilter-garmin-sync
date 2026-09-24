"""Timezone handling: local-day grouping + correct UTC/local encoding in FIT/TCX.

These tests build ascents from known UTC ``created_at`` values (as the PowerSync
source does) with an explicit target tz, then decode the generated FIT back with
the Garmin SDK to confirm the true UTC instant and the local display offset.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from garmin_fit_sdk import Decoder, Stream

from kilter_garmin_sync.fit_writer import build_fit_bytes
from kilter_garmin_sync.kilter_source import _parse_created_at, _to_local
from kilter_garmin_sync.models import Ascent
from kilter_garmin_sync.sessions import group_sessions
from kilter_garmin_sync.tcx_writer import build_tcx_string

LA = ZoneInfo("America/Los_Angeles")
FIT_EPOCH = dt.datetime(1989, 12, 31, tzinfo=dt.timezone.utc)


def _ascent(utc_iso: str, tz=LA, grade: str = "V4") -> Ascent:
    when = _to_local(_parse_created_at(utc_iso), tz)
    return Ascent(
        board="kilter",
        angle=40,
        climb_name="C",
        date=when,
        logged_grade=None,
        displayed_grade=grade,
        is_benchmark=False,
        tries=1,
        is_mirror=False,
        is_ascent=True,
        comment="",
    )


def _decode(data: bytes):
    return Decoder(Stream.from_byte_array(bytearray(data))).read()


def test_group_by_local_date_crosses_utc_midnight():
    # 2026-04-04T00:23:32Z is still 2026-04-03 17:23 in Los Angeles (PDT).
    session = group_sessions([_ascent("2026-04-04T00:23:32Z")], min_duration_minutes=10)[0]
    assert session.day == dt.date(2026, 4, 3)


def test_fit_start_time_is_true_utc_instant():
    utc = "2026-04-04T00:23:32Z"
    session = group_sessions([_ascent(utc)], min_duration_minutes=10)[0]
    messages, errors = _decode(build_fit_bytes(session))
    assert errors == []
    start = messages["session_mesgs"][0]["start_time"].replace(tzinfo=dt.timezone.utc)
    assert start == _parse_created_at(utc)


def test_fit_local_timestamp_offset_pdt():
    # April -> Pacific Daylight Time, UTC-7.
    session = group_sessions([_ascent("2026-04-04T00:23:32Z")], min_duration_minutes=10)[0]
    activity = _decode(build_fit_bytes(session))[0]["activity_mesgs"][0]
    ts = activity["timestamp"].replace(tzinfo=dt.timezone.utc)
    local = FIT_EPOCH + dt.timedelta(seconds=activity["local_timestamp"])
    assert (local - ts) == dt.timedelta(hours=-7)


def test_fit_local_timestamp_offset_pst():
    # January -> Pacific Standard Time, UTC-8 (DST handled from the aware dt).
    session = group_sessions([_ascent("2026-01-15T02:00:00Z")], min_duration_minutes=10)[0]
    assert session.day == dt.date(2026, 1, 14)
    activity = _decode(build_fit_bytes(session))[0]["activity_mesgs"][0]
    ts = activity["timestamp"].replace(tzinfo=dt.timezone.utc)
    local = FIT_EPOCH + dt.timedelta(seconds=activity["local_timestamp"])
    assert (local - ts) == dt.timedelta(hours=-8)


def test_tcx_start_time_is_utc_zulu_from_aware():
    session = group_sessions([_ascent("2026-04-04T00:23:32Z")], min_duration_minutes=10)[0]
    xml = build_tcx_string(session)
    # 17:23 PDT == 00:23:32Z the next UTC day.
    assert "<Id>2026-04-04T00:23:32Z</Id>" in xml
