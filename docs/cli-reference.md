# CLI reference

Full command-line options, how fetching works, session grouping, and duplicate
protection. For a quick start see the [README](../README.md).

## How fetching works

On a fetch run the tool (1) authenticates to Kilter Grips via the Keycloak
password grant, (2) streams your data from PowerSync (`sync1.kiltergrips.com`),
and (3) maps your `logs` entries — joined against your personal climb ratings —
into the internal `Ascent` model. Synced rows are cached to a local SQLite file
(`--db-path`, default `data/kilter.db`) unless you pass `--no-cache`. The
`--from-csv` path skips all of this and needs no credentials — handy for offline
use and testing with a legacy BoardLib CSV.

## Options

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
| `--weight-lb`     | `180`           | Body weight (lb) for the MET-based calorie estimate.                     |
| `--met`           | `8.0`           | MET intensity for the calorie estimate (vigorous bouldering).            |
| `--upload`        | off             | After writing, auto-upload the files to Garmin Connect (see below).      |
| `--upload-ledger` | `data/uploaded.json` | Ledger of already-uploaded files so re-uploads are skipped.         |
| `--overwrite`     | off             | Regenerate files even if they already exist or are in the ledger.        |
| `--ledger PATH`   | `data/exported.json` | Ledger of already-exported days; re-runs skip them.                 |
| `--no-ledger`     | off             | Ignore the ledger entirely (read nor write).                             |
| `--dry-run`       | off             | List sessions and target files without writing.                          |

## Session grouping

**One activity per calendar day** (your local date): every ascent logged on the
same day goes into a single session. The session's duration is first→last ascent
that day (padded to `--min-duration` when a day has a single ascent).

## Incremental use / duplicate protection

The tool keeps an **export ledger** (`data/exported.json` by default) recording
which session days it has already generated, with a content fingerprint of that
day's ascents. On every run each day is classified:

- **new** — not exported before → written and recorded.
- **unchanged** — already exported, ascents identical → **skipped**.
- **changed** — already exported but that day's ascents changed since (e.g. you
  logged more climbs later that day) → **skipped with a warning**; pass
  `--overwrite` to regenerate it.

So re-running never re-creates a file for a day you've already exported, even if
you deleted the files from `./out`. Garmin Connect itself does **not**
de-duplicate imports, so this ledger is what stops you from importing the same
session twice — only import the newly-written files each run. As a second guard,
a `new` day whose file already exists in `--out` is also skipped. Use
`--overwrite` to force regeneration, `--no-ledger` to ignore the ledger, or
`--since` to limit the date range. Filenames are `kilter-YYYY-MM-DD.<ext>`.

In the cloud (GitHub Actions) the ledger is carried across runs with
`actions/cache`, so a fresh runner still skips days exported by earlier runs.
