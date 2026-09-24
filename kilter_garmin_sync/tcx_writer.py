"""Generate Garmin-importable TCX activity files for climbing sessions.

TCX has no native climbing sport, so activities import as sport ``Other``. In
exchange, the full climb summary is embedded in the ``<Notes>`` element, which
Garmin Connect reliably displays on the activity. Use TCX when you want the
summary text; use FIT when you want the correct climbing activity type.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .metrics import SessionMetrics, compute_metrics
from .models import Session
from .summary import session_notes

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = (
    "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2 "
    "http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd"
)


def _iso(t: datetime) -> str:
    """Format a datetime as a UTC ISO-8601 Zulu timestamp.

    Timezone-aware inputs are converted straight to UTC (correct on any host);
    naive inputs are assumed to be system-local time first.
    """
    if t.tzinfo is None:
        t = t.astimezone()
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_version(parent: ET.Element, tag: str) -> None:
    """Append a schema-required Version_t block (all-zero placeholder)."""
    version = ET.SubElement(parent, tag)
    ET.SubElement(version, f"{{{TCX_NS}}}VersionMajor").text = "0"
    ET.SubElement(version, f"{{{TCX_NS}}}VersionMinor").text = "1"
    ET.SubElement(version, f"{{{TCX_NS}}}BuildMajor").text = "0"
    ET.SubElement(version, f"{{{TCX_NS}}}BuildMinor").text = "0"


def build_tcx_string(session: Session, metrics: SessionMetrics | None = None) -> str:
    """Build a TCX document (as a string) for one session."""
    if metrics is None:
        metrics = compute_metrics(session)
    start_iso = _iso(session.start)
    total_seconds = max((session.end - session.start).total_seconds(), 0.0)

    ET.register_namespace("", TCX_NS)
    ET.register_namespace("xsi", XSI_NS)

    root = ET.Element(f"{{{TCX_NS}}}TrainingCenterDatabase")
    root.set(f"{{{XSI_NS}}}schemaLocation", SCHEMA_LOCATION)

    activities = ET.SubElement(root, f"{{{TCX_NS}}}Activities")
    activity = ET.SubElement(activities, f"{{{TCX_NS}}}Activity")
    # No climbing sport exists in TCX; "Other" is the correct neutral value.
    activity.set("Sport", "Other")

    ET.SubElement(activity, f"{{{TCX_NS}}}Id").text = start_iso

    lap = ET.SubElement(activity, f"{{{TCX_NS}}}Lap")
    lap.set("StartTime", start_iso)
    ET.SubElement(lap, f"{{{TCX_NS}}}TotalTimeSeconds").text = f"{total_seconds:.1f}"
    ET.SubElement(lap, f"{{{TCX_NS}}}DistanceMeters").text = "0.0"
    ET.SubElement(lap, f"{{{TCX_NS}}}Calories").text = str(metrics.calories)
    ET.SubElement(lap, f"{{{TCX_NS}}}Intensity").text = "Active"
    ET.SubElement(lap, f"{{{TCX_NS}}}TriggerMethod").text = "Manual"

    ET.SubElement(activity, f"{{{TCX_NS}}}Notes").text = session_notes(session, metrics)

    creator = ET.SubElement(activity, f"{{{TCX_NS}}}Creator")
    creator.set(f"{{{XSI_NS}}}type", "Device_t")
    ET.SubElement(creator, f"{{{TCX_NS}}}Name").text = "kilter-garmin-sync"
    ET.SubElement(creator, f"{{{TCX_NS}}}UnitId").text = "0"
    ET.SubElement(creator, f"{{{TCX_NS}}}ProductID").text = "0"
    _append_version(creator, f"{{{TCX_NS}}}Version")

    author = ET.SubElement(root, f"{{{TCX_NS}}}Author")
    author.set(f"{{{XSI_NS}}}type", "Application_t")
    ET.SubElement(author, f"{{{TCX_NS}}}Name").text = "kilter-garmin-sync"
    build = ET.SubElement(author, f"{{{TCX_NS}}}Build")
    _append_version(build, f"{{{TCX_NS}}}Version")
    ET.SubElement(author, f"{{{TCX_NS}}}LangID").text = "en"
    ET.SubElement(author, f"{{{TCX_NS}}}PartNumber").text = "000-00000-00"

    ET.indent(root)
    xml = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n"


def write_tcx(session: Session, path: str | Path, metrics: SessionMetrics | None = None) -> Path:
    """Write a TCX file for the session and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_tcx_string(session, metrics), encoding="utf-8")
    return path
