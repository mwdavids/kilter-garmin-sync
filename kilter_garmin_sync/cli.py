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
from .ledger import Ledger, session_fingerprint
from .metrics import DEFAULT_MET, DEFAULT_WEIGHT_LB, compute_metrics
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
    out.add_argument("--overwrite", action="store_true", help="Regenerate files even if they already exist or are recorded in the ledger.")
    out.add_argument(
        "--ledger",
        default="data/exported.json",
        metavar="PATH",
        help=(
            "Path to the export ledger that records already-exported session days "
            "so re-runs skip them (default: data/exported.json)."
        ),
    )
    out.add_argument(
        "--no-ledger",
        action="store_true",
        help="Disable the export ledger (do not read or write it).",
    )
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

    metrics = parser.add_argument_group("effort metrics (HR-free estimates)")
    metrics.add_argument(
        "--weight-lb",
        type=float,
        default=DEFAULT_WEIGHT_LB,
        metavar="LB",
        help=f"Body weight in pounds, used for calorie estimates (default: {DEFAULT_WEIGHT_LB:g}).",
    )
    metrics.add_argument(
        "--met",
        type=float,
        default=DEFAULT_MET,
        metavar="MET",
        help=f"MET intensity for calorie estimates (default: {DEFAULT_MET:g}, vigorous bouldering).",
    )

    upload = parser.add_argument_group("garmin auto-upload (optional)")
    upload.add_argument(
        "--upload",
        action="store_true",
        help=(
            "After generating files, upload them to Garmin Connect via garth. "
            "Requires GARMIN_TOKEN (from 'kilter-garmin-sync garmin-login') or "
            "GARMIN_EMAIL/GARMIN_PASSWORD. Duplicates are skipped."
        ),
    )
    upload.add_argument(
        "--upload-ledger",
        default="data/uploaded.json",
        metavar="PATH",
        help="Path to the upload ledger of already-uploaded files (default: data/uploaded.json).",
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


def _run_garmin_login(argv: list[str]) -> int:
    """Handle the ``garmin-login`` subcommand: mint a reusable Garmin token."""
    import getpass

    from .garmin_upload import GarminAuthError, make_token

    sub = argparse.ArgumentParser(
        prog="kilter-garmin-sync garmin-login",
        description=(
            "Log in to Garmin Connect once (handling any MFA prompt) and print a "
            "base64 token. Save it as the GARMIN_TOKEN env var / CI secret so "
            "later --upload runs authenticate without a password or MFA."
        ),
    )
    sub.add_argument(
        "--email",
        default=os.environ.get("GARMIN_EMAIL"),
        help="Garmin Connect email (or set GARMIN_EMAIL).",
    )
    args = sub.parse_args(argv)

    email = args.email or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")

    def prompt_mfa() -> str:
        return input("Garmin MFA code: ").strip()

    try:
        token = make_token(email, password, prompt_mfa=prompt_mfa)
    except GarminAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("\nLogin successful. Save the line below as the GARMIN_TOKEN secret:\n")
    print(token)
    print(
        "\nStore it in CI as a repository secret named GARMIN_TOKEN "
        "(Settings -> Secrets and variables -> Actions). Do not commit it.",
        file=sys.stderr,
    )
    return 0


def _do_upload(args: argparse.Namespace, written_paths: list[Path]) -> None:
    from .garmin_upload import GarminAuthError, UploadLedger, upload_files

    if not written_paths:
        print("No new files to upload.")
        return
    ledger = UploadLedger.load(Path(args.upload_ledger))
    try:
        results = upload_files(written_paths, ledger=ledger)
    except GarminAuthError as exc:
        print(f"upload skipped: {exc}", file=sys.stderr)
        return
    for result in results:
        suffix = f" (activity {result.activity_id})" if result.activity_id else ""
        print(f"upload {result.status}: {result.path.name}{suffix}")


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "garmin-login":
        return _run_garmin_login(args_list[1:])

    parser = build_parser()
    args = parser.parse_args(args_list)

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

    use_ledger = not args.no_ledger
    ledger_path = Path(args.ledger)
    ledger = Ledger.load(ledger_path) if use_ledger else None

    written = skipped = 0
    written_paths: list[Path] = []
    for i, session in enumerate(sessions):
        target = out_dir / filenames[i]
        day_key = f"{session.day:%Y-%m-%d}"
        fingerprint = session_fingerprint(session)
        metrics = compute_metrics(session, weight_lb=args.weight_lb, met=args.met)

        if args.dry_run:
            status = ledger.status(day_key, fingerprint) if ledger else "new"
            tag = "" if status == "new" else f"  [{status}]"
            print(f"[dry-run] {target}  |  {session_title(session, metrics)}{tag}")
            if args.verbose:
                for line in session_notes(session, metrics).splitlines():
                    print(f"    {line}")
            continue

        if not args.overwrite:
            status = ledger.status(day_key, fingerprint) if ledger else "new"
            if status == "unchanged":
                skipped += 1
                if args.verbose:
                    print(f"skip (already exported): {target}", file=sys.stderr)
                continue
            if status == "changed":
                skipped += 1
                print(
                    f"skip (changed since last export; use --overwrite to regenerate): {target}",
                    file=sys.stderr,
                )
                continue
            if status == "new" and target.exists():
                skipped += 1
                if args.verbose:
                    print(f"skip (exists): {target}", file=sys.stderr)
                continue

        if args.format == "fit":
            write_fit(session, target, sub_sport=args.sub_sport, metrics=metrics)
        else:
            write_tcx(session, target, metrics)
        written += 1
        written_paths.append(target)
        if ledger is not None:
            ledger.record(day_key, fingerprint, filenames[i], args.format)
        print(f"wrote {target}  ({session_title(session, metrics)})")

    if not args.dry_run:
        if ledger is not None:
            ledger.save(ledger_path)
        print(f"\nDone: {written} written, {skipped} skipped, {len(sessions)} sessions total.")
        if args.upload:
            _do_upload(args, written_paths)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
