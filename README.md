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
| `--weight-lb`     | `180`           | Body weight (lb) for the MET-based calorie estimate.                     |
| `--met`           | `8.0`           | MET intensity for the calorie estimate (vigorous bouldering).            |
| `--upload`        | off             | After writing, auto-upload the files to Garmin Connect (see below).      |
| `--upload-ledger` | `data/uploaded.json` | Ledger of already-uploaded files so re-uploads are skipped.         |
| `--overwrite`     | off             | Regenerate files even if they already exist or are in the ledger.        |
| `--ledger PATH`   | `data/exported.json` | Ledger of already-exported days; re-runs skip them.                 |
| `--no-ledger`     | off             | Ignore the ledger entirely (read nor write).                             |
| `--dry-run`       | off             | List sessions and target files without writing.                          |

### Session grouping

**One activity per calendar day** (your local date): every ascent logged on the
same day goes into a single session. The session's duration is first→last ascent
that day (padded to `--min-duration` when a day has a single ascent).

### Incremental use / duplicate protection

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

## Effort metrics (no heart rate)

Kilter logs carry **no heart rate**, so Garmin's automatic Training Load /
Training Effect can't be truly measured. Rather than fabricate a fake HR stream
(which would corrupt Garmin's HRV and resting-HR baselines), the tool derives a
few **honest, clearly-labelled estimates** from the real logged data and writes
them into each activity:

- **Calories** — MET-based: `MET (8.0) × body_kg × hours`. Tune with
  `--weight-lb` (default 180) and `--met`. Written to the FIT session/laps and
  the TCX `<Calories>`.
- **Effort score** — grade-weighted volume: each send counts `V+1` (a V5 = 6),
  attempts count half, unrated climbs count a baseline of 1. Shown in the title
  and notes.
- **Suggested RPE (1–10)** — derived from the effort score. Because there's no
  HR, **RPE is the real lever** for training load on these activities: after
  importing, set the activity's *Perceived Exertion* / *Feel* in Garmin Connect
  to this value (or your own) so it contributes to training load sensibly. The
  notes spell this out: `Suggested RPE: 7 — set this in Garmin Connect for
  training load`.
- **Estimated Training Effect** — rough aerobic/anaerobic numbers on Garmin's
  0.0–5.0 scale (anaerobic-weighted, since bouldering is anaerobic). Stored in
  the FIT session as `total_training_effect` / `total_anaerobic_training_effect`.
- **Per-climb laps** — one FIT *lap* per logged climb. Since Kilter doesn't
  record per-climb durations, lap timing is estimated by distributing the
  session window evenly across the climbs (documented in `fit_writer.py`).
- **Total Routes & Max Difficulty** — Garmin's native bouldering fields. The tool
  emits one FIT `split` message (`split_type = climb_active`) per logged climb,
  plus a `split_summary`, so Garmin Connect shows **Total Routes** = the number
  of climbs. Each graded climb carries its **V-grade** on the split, so Garmin
  shows **Max Difficulty** = your hardest climb that day. See
  [Total Routes / Max Difficulty](#total-routes--max-difficulty-fit-fields) below
  for the exact FIT fields.

All of these are **estimates**, labelled as such in the notes. The only measured
facts are the timestamps, grades, angles, and send/attempt flags from Kilter.

### Local time & timezone

Kilter stores each ascent's time in **UTC**. Garmin displays an activity using
the *local* time offset embedded in the file, so getting the timezone right
matters — otherwise activities can show the wrong start time or even land on the
wrong calendar day.

- Sessions are grouped by **local calendar day** in the resolved timezone
  (`--tz`, default `America/Los_Angeles` in the GitHub Actions workflow; system
  local time otherwise).
- FIT time fields encode the **true UTC instant**, and the FIT `activity`
  message sets `local_timestamp` to the local wall-clock (DST-aware, e.g. PDT
  `-7h` in summer vs PST `-8h` in winter) so Garmin shows the correct local time.
- TCX `Id`/`StartTime` are written as UTC `Z` timestamps.

### Total Routes / Max Difficulty (FIT fields)

Garmin populates a bouldering activity's **Total Routes** and **Max Difficulty**
from FIT `split` messages, *not* the session/lap data. The bundled `fit_tool` /
`garmin-fit-sdk` profiles don't name the climbing grade fields (they're newer
than those profiles), so the tool writes them by their FIT global field number
on the `split` message (global mesg 312):

| Field | # | Meaning |
| ----- | - | ------- |
| `climb_grading_scale` | 69 | grade system enum; `8` = V-scale (vermin) |
| `climb_grade_value`   | 70 | grade on that scale; for V-scale it's the `vermin` enum, `V + 1` (V0 → 1, V4 → 5) |
| `status`              | 71 | `3` = climb completed (send), `2` = attempted |
| `climb_send`          | 73 | `1` for a send, `0` for an attempt |

- **Total Routes** = the count of `climb_active` splits (also carried in
  `split_summary.num_splits`).
- **Max Difficulty** = the maximum `climb_grade_value` across the splits.
- Unrated climbs still emit a split (so they count toward Total Routes) but carry
  no grade fields.

The generated file still decodes cleanly with `garmin-fit-sdk` (verified in the
tests). Because these are less-documented FIT fields, the definitive confirmation
that Garmin Connect renders both values is a real upload; that's pending a Garmin
token in CI.


## Auto-upload to Garmin Connect (optional)

By default the tool only *generates files* and you import them manually. If you'd
rather have them pushed to Garmin Connect automatically, use `--upload`, which
uploads via [`garth`](https://github.com/matin/garth). (garth is in
maintenance/deprecated but still works against Garmin's upload service; it's an
optional dependency, only imported when you use `--upload`.)

Garmin accounts often require **MFA**, which can't be answered on a headless CI
runner. So auth is **token-based**: log in **once** interactively to mint a
reusable token, then store it as a secret.

1. **Mint a token once (locally):**
   ```bash
   kilter-garmin-sync garmin-login
   # prompts for your Garmin email, password, and MFA code if required
   ```
   It prints a base64 token blob. (You can also set `GARMIN_EMAIL` /
   `GARMIN_PASSWORD` env vars to skip the prompts.)
2. **Save the token** as the `GARMIN_TOKEN` environment variable locally, or as a
   repo secret named `GARMIN_TOKEN` for CI (**Settings → Secrets and variables →
   Actions**). The token contains OAuth session tokens, **not** your password —
   never commit it.
3. **Upload:**
   ```bash
   # local: resumes from GARMIN_TOKEN, no MFA needed
   kilter-garmin-sync --upload
   ```
   In GitHub Actions, tick the **`upload`** input when running the workflow (it
   reads the `GARMIN_TOKEN` secret).

Duplicate protection for uploads is twofold: Garmin rejects a re-upload of the
same file with HTTP **409**, which the tool treats as "already uploaded"; and a
local **upload ledger** (`data/uploaded.json`, cached across CI runs) records
uploaded filenames so repeat runs skip the network call entirely. If neither a
token nor email/password is available, `--upload` prints a clear message and the
generated files are still written for manual import.

### Login rate-limits & the garth 429 header fix

Two separate Garmin rate-limits are worth knowing about:

- **garth 0.8.0 login header regression (patched).** The bundled garth version
  sends browser-style headers (`SSO_PAGE_HEADERS`, a desktop User-Agent) on its
  `/mobile/api/login` and `/mobile/api/mfa/verifyCode` POSTs, which Garmin's
  mobile API rejects with **HTTP 429** (garth
  [PR #218](https://github.com/matin/garth/pull/218) /
  [issue #217](https://github.com/matin/garth/issues/217)). `garmin_upload.py`
  installs a small runtime monkeypatch (`patch_garth_mobile_headers`, called
  before `garth.login`) that strips those browser headers from just those two
  POSTs — the sign-in GET and the `/portal/sso/embed` GET keep them — so the
  default mobile User-Agent is used and login succeeds. It's idempotent and a
  no-op if garth's internals change.
- **IP-reputation limiting on the SSO login endpoint.** Garmin's SSO *login*
  endpoint is also rate-limited by IP reputation: residential IPs are generally
  fine, but datacenter IPs (including some CI runners) are often blocked. That's
  why the recommended flow is to **mint the token once from your own machine**.
  The token-based `--upload` path talks to a *different* endpoint
  (`/upload-service/upload`) and is **not** affected by that login limit, so
  uploading from CI with a saved `GARMIN_TOKEN` works.


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
   - `upload` — also push the generated files to Garmin Connect (requires a
     `GARMIN_TOKEN` secret; see [Auto-upload](#auto-upload-to-garmin-connect-optional)).
   - `weight_lb` — body weight in lb for the calorie estimate (optional).
3. **Download the artifact.** When the run finishes, open it and download the
   **`kilter-activities`** artifact (a zip of `out/**`). Unzip it to get one
   `.fit`/`.tcx` per session. Thanks to the ledger (cached across runs), a later
   run's artifact contains only **newly-exported** days, so you won't re-import
   duplicates.
4. **Import to Garmin** using the steps in the next section.

### Nightly auto-upload

The workflow also runs **automatically every night** (cron `0 13 * * *` =
13:00 UTC, ~06:00 Pacific). A scheduled run always exports **FIT bouldering** in
`America/Los_Angeles` and **auto-uploads** the results to Garmin Connect using
the `GARMIN_TOKEN` secret — no button-press needed.

- **Scope / de-dup floor.** To avoid re-touching sessions you already imported,
  scheduled uploads are limited to sessions **on or after `UPLOAD_SINCE`**
  (passed as `--since`). It defaults to **`2026-09-24`** and is overridable with
  a repo **Variable** named `UPLOAD_SINCE` (**Settings → Secrets and variables →
  Actions → Variables → New repository variable**, format `YYYY-MM-DD`). A manual
  run uses its own `since` input when you provide one, otherwise the same floor.
- **Skip already-uploaded files.** The upload ledger (`data/uploaded.json`) is
  cached across runs (date-based key, so it persists and keeps growing night to
  night), so repeated nightly runs skip files already sent. Garmin also rejects
  any true duplicate with HTTP 409, so a re-upload never creates a second
  activity even if the ledger is cold.

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
- Either way there is **no heart rate** or GPS/distance — board sessions don't
  record them. Calories, effort, and Training Effect are **estimates** derived
  from grades, volume, and duration (see [Effort metrics](#effort-metrics-no-heart-rate)),
  not measured values.

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
the effort metrics math, the summary text, and file generation — including
parsing every generated FIT back with the Garmin SDK and re-parsing every TCX as
XML to confirm validity. The Garmin upload path is tested with a fully mocked
`garth` client, so no network calls are made.

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
  ledger.py           export ledger: skip already-exported session days
  metrics.py          HR-free effort estimates: calories, effort, RPE, TE
  summary.py          human-readable title + notes
  fit_writer.py       FIT generation (fit-tool): per-climb laps, calories, TE
  tcx_writer.py       TCX generation
  garmin_upload.py    optional auto-upload to Garmin Connect (garth)
tests/                pytest suite + fixtures (no credentials needed)
```

## License

MIT
