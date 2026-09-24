"""Tests for TCX generation: build a document and parse it back."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from kilter_garmin_sync.sessions import group_sessions
from kilter_garmin_sync.tcx_writer import TCX_NS, build_tcx_string, write_tcx

from conftest import make_ascent

NS = {"t": TCX_NS}


def _session():
    return group_sessions(
        [
            make_ascent(climb_name="Alpha", when=datetime(2026, 3, 1, 9, 0), grade="V4"),
            make_ascent(climb_name="Bravo", when=datetime(2026, 3, 1, 9, 45), grade="V7"),
        ],
        min_duration_minutes=1,
    )[0]


def test_tcx_is_well_formed_xml():
    root = ET.fromstring(build_tcx_string(_session()))
    assert root.tag == f"{{{TCX_NS}}}TrainingCenterDatabase"


def test_tcx_sport_is_other():
    root = ET.fromstring(build_tcx_string(_session()))
    activity = root.find("t:Activities/t:Activity", NS)
    assert activity.get("Sport") == "Other"


def test_tcx_notes_contain_summary():
    root = ET.fromstring(build_tcx_string(_session()))
    notes = root.find("t:Activities/t:Activity/t:Notes", NS).text
    assert "Alpha" in notes
    assert "Bravo" in notes
    assert "Kilter Board" in notes


def test_tcx_id_and_lap_start_match():
    root = ET.fromstring(build_tcx_string(_session()))
    activity = root.find("t:Activities/t:Activity", NS)
    activity_id = activity.find("t:Id", NS).text
    lap = activity.find("t:Lap", NS)
    assert lap.get("StartTime") == activity_id
    assert activity_id.endswith("Z")


def test_tcx_total_time_matches_duration():
    root = ET.fromstring(build_tcx_string(_session()))
    lap = root.find("t:Activities/t:Activity/t:Lap", NS)
    total = float(lap.find("t:TotalTimeSeconds", NS).text)
    assert total == 45 * 60


def test_write_tcx_creates_file(tmp_path):
    out = tmp_path / "nested" / "kilter-2026-03-01.tcx"
    result = write_tcx(_session(), out)
    assert result == out
    # Re-parse from disk to confirm validity.
    root = ET.parse(out).getroot()
    assert root.tag == f"{{{TCX_NS}}}TrainingCenterDatabase"
