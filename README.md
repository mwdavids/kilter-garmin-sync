# kilter-garmin-sync

Export your **Kilter Board** (by **Kilter Grips**) logbook into
**Garmin-importable activity files** — one file per climbing session — so you can
manually import your board sessions into Garmin Connect.

- Fetches your ascents/attempts directly from the **new Kilter Grips backend**
  (`kiltergrips.com`): Keycloak auth + PowerSync data sync (no third-party API).
- Groups them into **one activity per calendar day**.
- Writes one activity file per session to `./out`.
- **FIT** output (default) imports as a native **Bouldering** activity; **TCX**
  output embeds a readable climb summary in the activity notes.
- No auto-upload — you import the files yourself.

There is **no heart-rate, calorie, or GPS data** on a board session; that's
expected. The generated files carry valid timing + activity type (+ a summary),
which is all Garmin Connect needs to import them.

> **Data-source note:** Kilter split from Aurora Climbing and removed the old
> Aurora API that [BoardLib](https://github.com/lemeryfertitta/BoardLib)
> targeted — `kilterboardapp.com` is dead, so the BoardLib live-fetch path no
> longer works for Kilter. This tool now talks to the current Kilter Grips
> backend instead. The `--from-csv` path still accepts a legacy BoardLib logbook
> CSV for offline use.

## Backend limitations (read this)

The new Kilter Grips backend syncs your **logbook** to your account, but a
couple of fields simply aren't available to a user token:

- **Climb names are not served.** There is no climb-name table in the sync and
  no REST endpoint returns a name for a climb UUID (verified). Climbs are
  therefore labelled **`Climb <uuid8>`** (first 8 hex chars of the UUID).
- **Consensus grade is not synced.** The community/consensus grade
  (`climb_stats`) isn't available, so the grade shown is **your own rating** —
  and only for climbs you personally rated. Unrated climbs show no grade.

Everything else is intact: **duration** (first→last ascent that day), **angle**,
**send vs attempt**, attempt counts, and the **per-grade histogram** in the
summary.

## Install

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
# or install the package (adds the `kilter-garmin-sync` command):
python -m pip install .
```

## Authentication

Credentials are **never hardcoded**. Provide your **Kilter Grips** account
username and put the password in an environment variable. Auth uses the Keycloak
*password* grant against `idp.kiltergrips.com`.

| Value    | Source                                                       |
| -------- | ------------------------------------------------------------ |
| username | `--username` flag, or the `KILTER_USERNAME` environment var  |
| password | the `KILTER_PASSWORD` environment variable                   |

```powershell
# Windows PowerShell
$env:KILTER_USERNAME = "you@example.com"
$env:KILTER_PASSWORD = "your-password"
```

```bash
# macOS / Linux
export KILTER_USERNAME="you@example.com"
export KILTER_PASSWORD="your-password"
```

## Usage

```bash
# Fetch from Kilter and generate FIT files into ./out
kilter-garmin-sync --username you@example.com

# Generate TCX instead (summary shows up in Garmin Connect's activity notes)
kilter-garmin-sync --format tcx

# Only sessions on/after a date
kilter-garmin-sync --since 2026-09-01

# Offline / no credentials: use a logbook CSV you already exported with BoardLib
kilter-garmin-sync --from-csv logbook.csv --format tcx

# Preview what would be produced, without writing files
kilter-garmin-sync --from-csv logbook.csv --dry-run --verbose
```

You can also run it as a module: `python -m kilter_garmin_sync --help`.

### How fetching works

On a fetch run the tool (1) authenticates to Kilter Grips via the Keycloak
password grant, (2) streams your data from PowerSync (`sync1.kiltergrips.com`),
and (3) maps your `logs` entries — joined against your personal climb ratings —
into the internal `Ascent` model. Synced rows are cached to a local SQLite file
(`--db-path`, default `data/kilter.db`) unless you pass `--no-cache`. The
`--from-csv` path skips all of this and needs no credentials — handy for offline
use and testing with a legacy BoardLib CSV.

### Options

| Flag              | Default         | Description                                                               |
| ----------------- | --------------- | ------------------------------------------------------------------------- |
| `--from-csv PATH` | —               | Use a legacy BoardLib logbook CSV; skip fetching (no credentials).       |
| `--username`      | `KILTER_USERNAME` | Kilter Grips account username/email.                                   |
| `--board`         | `kilter`        | Board name (drives the `<BOARD>_PASSWORD` env var; only `kilter` is live).|
| `--db-path`       | `data/kilter.db`| Local SQLite cache of synced PowerSync rows.                             |
| `--no-cache`      | off             | Don't write the local SQLite cache when fetching.                        |
| `--tz`            | system local    | IANA timezone (e.g. `America/Los_Angeles`) for local-day grouping.       |
| `--out`           | `out`           | Output directory.                                                         |
| `--format`        | `fit`           | `fit` (native climbing type) or `tcx` (summary in notes, sport "Other"). |
| `--sub-sport`     | `bouldering`    | FIT climbing sub-sport: `bouldering` or `indoor_climbing`.               |
| `--since`         | —               | Only export sessions on/after `YYYY-MM-DD`.                               |
| `--min-duration`  | `10`            | Minimum session length in minutes (short sessions are padded).           |
| `--ascents-only`  | off             | Exclude attempt-only entries (keep only sends).                          |
| `--overwrite`     | off             | Regenerate files even if they already exist.                             |
| `--dry-run`       | off             | List sessions and target files without writing.                          |

### Session grouping

**One activity per calendar day** (your local date): every ascent logged on the
same day goes into a single session. The session's duration is first→last ascent
that day (padded to `--min-duration` when a day has a single ascent).

### Incremental use

Re-running is cheap: sessions whose output file already exists are **skipped**
unless you pass `--overwrite`. Combine with `--since` to only look at recent
dates. Filenames are `kilter-YYYY-MM-DD.<ext>`.

## Run it in the cloud (GitHub Actions)

If your local network blocks or throttles the Kilter Grips backend
(`kiltergrips.com` — for example Microsoft Global Secure Access or a corporate
secure edge that TLS-inspects the sync stream), the fetch will fail or crawl on
your machine. GitHub-hosted runners are **not** behind that edge, so you can run
the export in the cloud instead and download the files.

1. **Add your Kilter Grips credentials as repo secrets** (they are never printed
   and never committed). In the GitHub repo: **Settings → Secrets and variables
   → Actions → New repository secret**. Add two:
   - `KILTER_USERNAME` — your Kilter Grips account email.
   - `KILTER_PASSWORD` — your Kilter Grips password.
2. **Trigger the workflow.** Go to the **Actions** tab → **Export Kilter
   activities** → **Run workflow**. Optionally set:
   - `mode` — `export` (default) or `diagnose` (enumerates what the backend
     syncs into your account; writes no activity files).
   - `format` — `fit` (default) or `tcx`.
   - `since` — `YYYY-MM-DD` to only export sessions on or after that date.
   - `sub_sport` — `bouldering` (default) or `indoor_climbing` (FIT only).
   - `tz` — IANA timezone (e.g. `America/Los_Angeles`) for local-day grouping.
3. **Download the artifact.** When the run finishes, open it and download the
   **`kilter-activities`** artifact (a zip of `out/**`). Unzip it to get one
   `.fit`/`.tcx` per session.
4. **Import to Garmin** using the steps in the next section.

Generated activity files and the Kilter database are produced only inside the
runner and uploaded as an artifact — they are **not** committed to the repo.

## Importing into Garmin Connect

Garmin Connect (web) can import activity files manually:

1. Go to [connect.garmin.com](https://connect.garmin.com).
2. Click the **＋** (top right) → **Import Data**.
3. Upload a file from `./out` (drag-and-drop also works).
4. Repeat per session file.

Notes on what you'll see:

- **FIT files** import as a **Bouldering** activity (the native climbing type),
  with the correct **duration**. Garmin Connect does not display free text from
  a FIT file, so the climb summary (grades, angles, labels) lives only in the
  **filename** — rename the activity afterward if you want.
- **TCX files** import as sport **Other**, but the full climb summary appears in
  the activity's **Notes/Comments**: climb labels (`Climb <id>` — see
  limitations), angles, per-climb grades, sends vs attempts, the **hardest
  send**, and a **per-grade send count** (e.g. `V8×1, V6×1, V4×1`), plus the time
  window and duration.
- Either way there is **no heart rate, calories, or distance** — board sessions
  don't record them.

## FIT vs TCX — which to use?

| | FIT (default) | TCX |
| --- | --- | --- |
| Activity type in Garmin | ✅ native Bouldering / Indoor Climbing | ⚠️ "Other" |
| Climb summary visible in Garmin | ❌ (filename only) | ✅ in activity Notes |
| Format | binary (Garmin-native) | XML |

**Why FIT is the default:** getting the correct climbing activity type is the
higher-value outcome for most people, and FIT is Garmin's native format. FIT is
generated with [`fit-tool`](https://pypi.org/project/fit-tool/) (it exposes
named `Sport`/`SubSport` enums, so no magic numbers) and validated by decoding
every file back with the official
[`garmin-fit-sdk`](https://pypi.org/project/garmin-fit-sdk/). If you'd rather
keep the readable summary in Garmin, use `--format tcx`.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest
```

The tests use a small fixture logbook (`tests/fixtures/sample_logbook.csv`), so
they run **without any Kilter/Garmin credentials**. They cover session grouping,
the summary text, and file generation — including parsing every generated FIT
back with the Garmin SDK and re-parsing every TCX as XML to confirm validity.

## Project layout

```
kilter_garmin_sync/
  cli.py              CLI + orchestration
  kilter_client.py    new Kilter Grips backend: Keycloak auth + PowerSync stream
  kilter_source.py    map synced logs (+ your ratings) -> Ascent[]; SQLite cache
  boardlib_source.py  load a legacy BoardLib logbook CSV -> Ascent[] (offline)
  diagnose.py         inspect what the backend syncs into your account
  models.py           Ascent and Session data models
  sessions.py         group ascents into one session per calendar day
  summary.py          human-readable title + notes
  fit_writer.py       FIT generation (fit-tool)
  tcx_writer.py       TCX generation
tests/                pytest suite + fixtures (no credentials needed)
```

## License

MIT
