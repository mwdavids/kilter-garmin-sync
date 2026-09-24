"""Command-line interface for kilter-garmin-sync."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .boardlib_source import load_csv
from .fit_writer import SUB_SPORTS, write_fit
from .kilter_source import fetch_ascents
from .models import Session
from .sessions import group_sessions
from .summary import session_notes, session_title
from .tcx_writer import write_tcx


def _parse_since(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--since must be YYYY-MM-DD, got {value!r}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kilter-garmin-sync",
        description=(
            "Export Kilter Board climbing sessions into Garmin-importable "
            "activity files (FIT or TCX). One file is produced per session."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Credentials: pass --username (or set KILTER_USERNAME) and set the\n"
            "KILTER_PASSWORD environment variable. Credentials are never needed\n"
            "with --from-csv.\n\n"
            "Examples:\n"
            "  # Fetch from Kilter and generate FIT files\n"
            "  set KILTER_PASSWORD=... (Windows)  |  export KILTER_PASSWORD=... (bash)\n"
            "  kilter-garmin-sync --username you@example.com\n\n"
            "  # Offline: generate TCX from an existing logbook CSV\n"
            "  kilter-garmin-sync --from-csv logbook.csv --format tcx\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    source = parser.add_argument_group("data source")
    source.add_argument(
        "--from-csv",
        metavar="PATH",
        help="Use an existing BoardLib logbook CSV instead of fetching (no credentials needed).",
    )
    source.add_argument(
        "--username",
        default=os.environ.get("KILTER_USERNAME"),
        help="Kilter account username/email (or set KILTER_USERNAME). Password comes from KILTER_PASSWORD.",
    )
    source.add_argument(
        "--board",
        default="kilter",
        help="Board name (default: kilter). Determines the <BOARD>_PASSWORD env var.",
    )
    source.add_argument(
        "--db-path",
        default="data/kilter.db",
        metavar="PATH",
        help="Path to the local SQLite cache of synced rows (default: data/kilter.db).",
    )
    source.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not write the local SQLite cache when fetching.",
    )
    source.add_argument(
        "--tz",
        metavar="ZONE",
        default=os.environ.get("KILTER_TZ"),
        help=(
            "IANA timezone (e.g. America/Los_Angeles) used to convert ascent "
            "timestamps to your local calendar day for grouping. Default: system "
            "local time (or set KILTER_TZ)."
        ),
    )

    out = parser.add_argument_group("output")
    out.add_argument("--out", default="out", metavar="DIR", help="Output directory (default: out).")
    out.add_argument(
        "--format",
        choices=("fit", "tcx"),
        default="fit",
        help="Output format. fit = native climbing activity type; tcx = summary in Notes, sport Other (default: fit).",
    )
    out.add_argument(
        "--sub-sport",
        choices=sorted(SUB_SPORTS),
        default="bouldering",
        help="FIT climbing sub-sport (default: bouldering; ignored for TCX).",
    )
    out.add_argument("--overwrite", action="store_true", help="Regenerate files even if they already exist.")
    out.add_argument("--dry-run", action="store_true", help="List sessions and target files without writing.")

    grouping = parser.add_argument_group("session grouping")
    grouping.add_argument(
        "--min-duration",
        type=float,
        default=10.0,
        metavar="MIN",
        help="Minimum session duration in minutes; short sessions are padded (default: 10).",
    )
    grouping.add_argument(
        "--since",
        type=_parse_since,
        metavar="YYYY-MM-DD",
        help="Only export sessions on or after this date.",
    )
    grouping.add_argument(
        "--ascents-only",
        action="store_true",
        help="Exclude attempt-only entries (keep only sends).",
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")
    return parser


def _assign_filenames(sessions: list[Session], ext: str) -> dict[int, str]:
    """Map session index -> filename.

    Grouping guarantees one session per calendar day, so a date-based name is
    unique. A numeric suffix is added only as a defensive guard against any
    collision.
    """
    names: dict[int, str] = {}
    used: set[str] = set()
    for i, s in enumerate(sessions):
        base = f"kilter-{s.day:%Y-%m-%d}"
        candidate = f"{base}.{ext}"
        n = 2
        while candidate in used:
            candidate = f"{base}-{n}.{ext}"
            n += 1
        used.add(candidate)
        names[i] = candidate
    return names


def _load_ascents(args: argparse.Namespace, parser: argparse.ArgumentParser):
    if args.from_csv:
        path = Path(args.from_csv)
        if not path.exists():
            parser.error(f"--from-csv file not found: {path}")
        return load_csv(path)

    if not args.username:
        parser.error(
            "no data source: pass --from-csv, or provide --username/KILTER_USERNAME "
            "(with KILTER_PASSWORD set) to fetch from Kilter."
        )
    password_var = f"{args.board.upper()}_PASSWORD"
    password = os.environ.get(password_var)
    if not password:
        parser.error(
            f"{password_var} environment variable is not set; required to fetch the logbook."
        )
    cache_db = None if args.no_cache else Path(args.db_path)
    return fetch_ascents(
        args.username,
        password,
        tz_name=args.tz,
        cache_db=cache_db,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    ascents = _load_ascents(args, parser)
    if args.verbose:
        print(f"Loaded {len(ascents)} logbook entries.", file=sys.stderr)

    sessions = group_sessions(
        ascents,
        min_duration_minutes=args.min_duration,
        ascents_only=args.ascents_only,
        since=args.since,
    )

    if not sessions:
        print("No sessions to export.")
        return 0

    out_dir = Path(args.out)
    filenames = _assign_filenames(sessions, args.format)

    written = skipped = 0
    for i, session in enumerate(sessions):
        target = out_dir / filenames[i]

        if args.dry_run:
            print(f"[dry-run] {target}  |  {session_title(session)}")
            if args.verbose:
                for line in session_notes(session).splitlines():
                    print(f"    {line}")
            continue

        if target.exists() and not args.overwrite:
            skipped += 1
            if args.verbose:
                print(f"skip (exists): {target}", file=sys.stderr)
            continue

        if args.format == "fit":
            write_fit(session, target, sub_sport=args.sub_sport)
        else:
            write_tcx(session, target)
        written += 1
        print(f"wrote {target}  ({session_title(session)})")

    if not args.dry_run:
        print(f"\nDone: {written} written, {skipped} skipped, {len(sessions)} sessions total.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
