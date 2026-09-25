# kilter-garmin-sync

Export your **Kilter Board** (by **Kilter Grips**) logbook into
**Garmin-importable activity files** — one file per climbing session — so you can
import your board sessions into Garmin Connect.

- Fetches your ascents/attempts directly from the **new Kilter Grips backend**
  (`kiltergrips.com`): Keycloak auth + PowerSync data sync (no third-party API).
- Groups them into **one activity per calendar day** and writes one file to `./out`.
- **FIT** output (default) imports as a native **Bouldering** activity; **TCX**
  embeds a readable climb summary in the activity notes.
- Optionally **auto-uploads** to Garmin Connect, or you import the files yourself.

There is **no heart-rate, calorie, or GPS data** on a board session; that's
expected. The generated files carry valid timing + activity type (+ a summary),
which is all Garmin Connect needs to import them.

## Quick start

Requires Python 3.10+.

```bash
python -m pip install .          # adds the `kilter-garmin-sync` command
```

```bash
# Fetch from Kilter and generate FIT files into ./out
kilter-garmin-sync --username you@example.com

# Generate TCX instead (summary shows up in Garmin Connect's notes)
kilter-garmin-sync --format tcx

# Only sessions on/after a date
kilter-garmin-sync --since 2026-09-01

# Offline: use a logbook CSV you already exported with BoardLib (no credentials)
kilter-garmin-sync --from-csv logbook.csv --dry-run --verbose
```

Run as a module with `python -m kilter_garmin_sync --help`. See the
**[CLI reference](docs/cli-reference.md)** for every flag, how fetching works,
session grouping, and duplicate protection.

## Authentication

Credentials are **never hardcoded**. Provide your **Kilter Grips** username and
put the password in an environment variable (Keycloak *password* grant against
`idp.kiltergrips.com`):

| Value    | Source                                                       |
| -------- | ------------------------------------------------------------ |
| username | `--username` flag, or the `KILTER_USERNAME` environment var  |
| password | the `KILTER_PASSWORD` environment variable                   |

```bash
export KILTER_USERNAME="you@example.com"   # $env:KILTER_USERNAME on Windows
export KILTER_PASSWORD="your-password"      # $env:KILTER_PASSWORD on Windows
```

## Importing into Garmin Connect

Garmin Connect (web) can import activity files manually:

1. Go to [connect.garmin.com](https://connect.garmin.com).
2. Click the **＋** (top right) → **Import Data**.
3. Upload a file from `./out` (drag-and-drop also works); repeat per session file.

What you'll see:

- **FIT files** import as a **Bouldering** activity with the correct duration.
  Garmin doesn't display FIT free text, so the climb summary lives in the
  **filename** — rename the activity afterward if you want.
- **TCX files** import as sport **Other**, but the full climb summary (labels,
  angles, per-climb grades, sends vs attempts, hardest send, per-grade counts)
  appears in the activity's **Notes/Comments**.
- Either way there's **no heart rate** or GPS/distance. Calories, effort, and
  Training Effect are **estimates** — see
  [effort metrics](docs/metrics-and-fit.md#effort-metrics-no-heart-rate).

### FIT vs TCX — which to use?

| | FIT (default) | TCX |
| --- | --- | --- |
| Activity type in Garmin | ✅ native Bouldering / Indoor Climbing | ⚠️ "Other" |
| Climb summary visible in Garmin | ❌ (filename only) | ✅ in activity Notes |
| Format | binary (Garmin-native) | XML |

FIT is the default because the correct climbing activity type is the
higher-value outcome for most people. If you'd rather keep the readable summary
in Garmin, use `--format tcx`.

## More docs

- **[CLI reference](docs/cli-reference.md)** — every flag, fetching internals,
  session grouping, incremental/duplicate protection.
- **[Effort metrics, timezone & FIT internals](docs/metrics-and-fit.md)** — how
  the HR-free estimates and the bouldering-route FIT fields work.
- **[Auto-upload to Garmin Connect](docs/auto-upload.md)** — `--upload`, minting
  a `GARMIN_TOKEN`, and Garmin login rate-limits.
- **[Run it in the cloud (GitHub Actions)](docs/github-actions.md)** — export
  from a runner when your network blocks Kilter, plus nightly auto-upload.

## Backend limitations

The new Kilter Grips backend syncs your **logbook**, but two fields aren't
available to a user token:

- **Climb names are not served.** Climbs are labelled **`Climb <uuid8>`** (first
  8 hex chars of the UUID).
- **Consensus grade is not synced.** The grade shown is **your own rating**, and
  only for climbs you personally rated; unrated climbs show no grade.

Everything else is intact: **duration**, **angle**, **send vs attempt**, attempt
counts, and the per-grade histogram in the summary.

> **Data-source note:** Kilter split from Aurora Climbing and removed the old
> Aurora API that [BoardLib](https://github.com/lemeryfertitta/BoardLib)
> targeted, so `kilterboardapp.com` is dead. This tool talks to the current
> Kilter Grips backend instead. The `--from-csv` path still accepts a legacy
> BoardLib logbook CSV for offline use.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest
```

Tests use a small fixture logbook (`tests/fixtures/sample_logbook.csv`) and run
**without any Kilter/Garmin credentials**. They cover session grouping, the
effort-metrics math, the summary text, and file generation — including parsing
every generated FIT back with the Garmin SDK and re-parsing every TCX as XML.
The Garmin upload path is tested with a fully mocked `garth` client.

### Project layout

```
kilter_garmin_sync/
  cli.py              CLI + orchestration
  kilter_client.py    new Kilter Grips backend: Keycloak auth + PowerSync stream
  kilter_source.py    map synced logs (+ your ratings) -> Ascent[]; SQLite cache
  boardlib_source.py  load a legacy BoardLib logbook CSV -> Ascent[] (offline)
  diagnose.py         inspect what the backend syncs into your account
  models.py           Ascent and Session data models
  sessions.py         group ascents into one session per calendar day
  ledger.py           export ledger: skip already-exported session days
  metrics.py          HR-free effort estimates: calories, effort, RPE, TE
  summary.py          human-readable title + notes
  fit_writer.py       FIT generation (fit-tool): per-climb laps, calories, TE
  tcx_writer.py       TCX generation
  garmin_upload.py    optional auto-upload to Garmin Connect (garth)
tests/                pytest suite + fixtures (no credentials needed)
```

## License

[MIT](LICENSE)
