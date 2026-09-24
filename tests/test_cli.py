"""End-to-end CLI tests over the fixture CSV (no network/credentials)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from garmin_fit_sdk import Decoder, Stream

from kilter_garmin_sync.cli import main


def test_cli_generates_fit_files(tmp_path, fixture_csv, capsys):
    out = tmp_path / "out"
    rc = main(["--from-csv", str(fixture_csv), "--out", str(out)])
    assert rc == 0
    files = sorted(p.name for p in out.glob("*.fit"))
    assert files == [
        "kilter-2026-09-20.fit",
        "kilter-2026-09-23.fit",
        "kilter-2026-09-25.fit",
    ]
    # Every generated FIT parses back without errors.
    for path in out.glob("*.fit"):
        _, errors = Decoder(Stream.from_byte_array(bytearray(path.read_bytes()))).read()
        assert errors == []


def test_cli_tcx_one_file_per_day(tmp_path, fixture_csv):
    out = tmp_path / "out"
    rc = main(["--from-csv", str(fixture_csv), "--out", str(out), "--format", "tcx"])
    assert rc == 0
    names = sorted(p.name for p in out.glob("*.tcx"))
    # Strictly one file per calendar day; no time-suffixed splits.
    assert names == [
        "kilter-2026-09-20.tcx",
        "kilter-2026-09-23.tcx",
        "kilter-2026-09-25.tcx",
    ]
    for path in out.glob("*.tcx"):
        ET.parse(path)  # must be well-formed


def test_cli_skips_existing_unless_overwrite(tmp_path, fixture_csv, capsys):
    out = tmp_path / "out"
    main(["--from-csv", str(fixture_csv), "--out", str(out)])
    capsys.readouterr()

    # Second run should skip all existing files.
    main(["--from-csv", str(fixture_csv), "--out", str(out)])
    captured = capsys.readouterr().out
    assert "0 written, 3 skipped" in captured

    # With --overwrite they are rewritten.
    main(["--from-csv", str(fixture_csv), "--out", str(out), "--overwrite"])
    captured = capsys.readouterr().out
    assert "3 written, 0 skipped" in captured


def test_cli_dry_run_writes_nothing(tmp_path, fixture_csv, capsys):
    out = tmp_path / "out"
    rc = main(["--from-csv", str(fixture_csv), "--out", str(out), "--dry-run"])
    assert rc == 0
    assert not out.exists() or not any(out.iterdir())
    assert "[dry-run]" in capsys.readouterr().out


def test_cli_since_filter(tmp_path, fixture_csv, capsys):
    out = tmp_path / "out"
    main(["--from-csv", str(fixture_csv), "--out", str(out), "--since", "2026-09-23"])
    names = sorted(p.name for p in out.glob("*.fit"))
    assert names == ["kilter-2026-09-23.fit", "kilter-2026-09-25.fit"]


def test_cli_errors_without_source(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main([])
