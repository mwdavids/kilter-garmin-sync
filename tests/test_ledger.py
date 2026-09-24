"""Tests for the export ledger / de-duplication (no credentials)."""

from datetime import datetime
from pathlib import Path

from kilter_garmin_sync.cli import main
from kilter_garmin_sync.ledger import Ledger, session_fingerprint
from kilter_garmin_sync.models import Ascent, Session


def _ascent(name, when, *, grade="V4", angle=40, is_ascent=True, tries=1):
    return Ascent(
        board="kilter",
        angle=angle,
        climb_name=name,
        date=when,
        logged_grade=None,
        displayed_grade=grade,
        is_benchmark=False,
        tries=tries,
        is_mirror=False,
        is_ascent=is_ascent,
        comment="",
    )


def _session(ascents):
    start = min(a.date for a in ascents)
    end = max(a.date for a in ascents)
    return Session(ascents=ascents, start=start, end=end)


def test_fingerprint_stable_and_order_independent():
    a = _ascent("Climb AAAA", datetime(2026, 9, 20, 18, 0))
    b = _ascent("Climb BBBB", datetime(2026, 9, 20, 20, 0), grade="V6")
    assert session_fingerprint(_session([a, b])) == session_fingerprint(_session([b, a]))


def test_fingerprint_changes_when_a_climb_is_added():
    a = _ascent("Climb AAAA", datetime(2026, 9, 20, 18, 0))
    b = _ascent("Climb BBBB", datetime(2026, 9, 20, 20, 0), grade="V6")
    assert session_fingerprint(_session([a])) != session_fingerprint(_session([a, b]))


def test_ledger_status_transitions(tmp_path):
    session = _session([_ascent("Climb AAAA", datetime(2026, 9, 20, 18, 0))])
    fp = session_fingerprint(session)
    led = Ledger()
    assert led.status("2026-09-20", fp) == "new"
    led.record("2026-09-20", fp, "kilter-2026-09-20.fit", "fit")
    assert led.status("2026-09-20", fp) == "unchanged"
    assert led.status("2026-09-20", "deadbeef") == "changed"

    path = tmp_path / "exported.json"
    led.save(path)
    assert Ledger.load(path).status("2026-09-20", fp) == "unchanged"


def test_missing_ledger_loads_empty(tmp_path):
    assert Ledger.load(tmp_path / "nope.json").entries == {}


FIXTURE = str(Path(__file__).parent / "fixtures" / "sample_logbook.csv")


def test_second_run_skips_via_ledger(tmp_path):
    out = tmp_path / "out"
    ledger = tmp_path / "exported.json"
    argv = ["--from-csv", FIXTURE, "--format", "tcx", "--out", str(out), "--ledger", str(ledger)]

    assert main(argv) == 0
    first = sorted(p.name for p in out.glob("*.tcx"))
    assert first  # something was written
    assert ledger.exists()

    # Delete the files but keep the ledger: a re-run must NOT regenerate them.
    for p in out.glob("*.tcx"):
        p.unlink()
    assert main(argv) == 0
    assert list(out.glob("*.tcx")) == []


def test_no_ledger_flag_regenerates(tmp_path):
    out = tmp_path / "out"
    ledger = tmp_path / "exported.json"
    base = ["--from-csv", FIXTURE, "--format", "tcx", "--out", str(out)]

    assert main(base + ["--ledger", str(ledger)]) == 0
    for p in out.glob("*.tcx"):
        p.unlink()

    # With --no-ledger the ledger is ignored, so files come back.
    assert main(base + ["--no-ledger"]) == 0
    assert sorted(p.name for p in out.glob("*.tcx"))
