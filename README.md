# kilter-garmin-sync

Export your **Kilter Board** (Aurora Climbing) logbook into **Garmin-importable
activity files** — one file per climbing session — so you can manually import
your board sessions into Garmin Connect.

- Fetches your ascents/attempts via [BoardLib](https://github.com/lemeryfertitta/BoardLib).
- Groups them into **one activity per calendar day**.
- Writes one activity file per session to `./out`.
- **FIT** output (default) imports as a native **Bouldering** activity; **TCX**
  output embeds a readable climb summary in the activity notes.
- No auto-upload — you import the files yourself.

There is **no heart-rate, calorie, or GPS data** on a board session; that's
expected. The generated files carry valid timing + activity type (+ a summary),
which is all Garmin Connect needs to import them.

## Install

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
# or install the package (adds the `kilter-garmin-sync` command):
python -m pip install .
```

## Authentication

Credentials are **never hardcoded**. Provide your Kilter account username and
put the password in an environment variable:

| Value    | Source                                                        |
| -------- | ------------------------------------------------------------ |
| username | `--username` flag, or the `KILTER_USERNAME` environment var  |
| password | the `KILTER_PASSWORD` environment variable (read by BoardLib) |

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

The password variable follows the board name: for a different Aurora board pass
`--board tension` and set `TENSION_PASSWORD`, etc.

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

On a fetch run the tool shells out to BoardLib to (1) download/sync the shared
board database to `--db-path` (default `data/kilter.db`, reused on later runs)
and (2) export your logbook, which it then parses in memory. The `--from-csv`
path skips all of this and needs no credentials — handy for offline use and
testing.

### Options

| Flag              | Default         | Description                                                               |
| ----------------- | --------------- | ------------------------------------------------------------------------- |
| `--from-csv PATH` | —               | Use an existing BoardLib logbook CSV; skip fetching (no credentials).     |
| `--username`      | `KILTER_USERNAME` | Board account username/email.                                           |
| `--board`         | `kilter`        | Aurora board name (kilter, tension, ...).                                 |
| `--db-path`       | `data/kilter.db`| BoardLib SQLite DB path (downloaded/synced on fetch).                     |
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

## Importing into Garmin Connect

Garmin Connect (web) can import activity files manually:

1. Go to [connect.garmin.com](https://connect.garmin.com).
2. Click the **＋** (top right) → **Import Data**.
3. Upload a file from `./out` (drag-and-drop also works).
4. Repeat per session file.

Notes on what you'll see:

- **FIT files** import as a **Bouldering** activity (the native climbing type),
  with the correct **duration**. Garmin Connect does not display free text from
  a FIT file, so the climb summary (grades, names, angles) lives only in the
  **filename** — rename the activity afterward if you want.
- **TCX files** import as sport **Other**, but the full climb summary appears in
  the activity's **Notes/Comments**: climb names, angles, per-climb grades,
  sends vs attempts, the **hardest send**, and a **per-grade send count**
  (e.g. `V8×1, V6×1, V4×1`), plus the time window and duration.
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
  boardlib_source.py  fetch via BoardLib / load logbook CSV -> Ascent[]
  models.py           Ascent and Session data models
  sessions.py         group ascents into one session per calendar day
  summary.py          human-readable title + notes
  fit_writer.py       FIT generation (fit-tool)
  tcx_writer.py       TCX generation
tests/                pytest suite + fixture logbook
```

## License

MIT
