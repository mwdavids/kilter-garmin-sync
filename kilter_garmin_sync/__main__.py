"""Package entry point so `python -m kilter_garmin_sync` works."""

from kilter_garmin_sync.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
