# Effort metrics, timezone & FIT internals

How the HR-free effort estimates are derived, how timezones are handled, and the
exact FIT fields used for bouldering routes and grades.

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
- **Session lap** — one FIT *lap* spanning the whole session, matching what a
  Garmin watch emits for a bouldering activity. Per-climb structure lives in the
  `split` messages instead (see below).
- **Per-climb splits & grades** — the tool emits one FIT `split` message
  (`split_type = climb_active`) per logged climb, each carrying that climb's
  **V-grade** and send/attempt status, plus a session `split_summary`. In Garmin
  Connect the activity shows the individual routes with their grades. See
  [Bouldering routes & grades](#bouldering-routes--grades-fit-fields) below for
  the exact FIT fields and an important note on Garmin's aggregate "Total Routes
  / Max Grade" tiles.

All of these are **estimates**, labelled as such in the notes. The only measured
facts are the timestamps, grades, angles, and send/attempt flags from Kilter.

## Local time & timezone

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

## Bouldering routes & grades (FIT fields)

Getting Garmin to **import** a manually-uploaded bouldering FIT at all turned out
to require reproducing the structure a real Garmin watch emits — a from-scratch
file (manufacturer `DEVELOPMENT`, one lap per climb) is accepted at upload with an
`uploadId` but silently never becomes an activity. So the generated file:

- identifies as a Garmin **Enduro 2** (`file_id.manufacturer = garmin`,
  `product = enduro2`) with a `file_creator`, `sport` (`rock_climbing` /
  `bouldering`), a single session-spanning `lap`, one `session`, and an
  `activity` message;
- emits one `split` message (`split_type = climb_active`, global mesg 312) per
  logged climb, plus one `split_summary`.

The bundled `fit_tool` / `garmin-fit-sdk` profiles don't name the climbing grade
fields (they're newer than those profiles), so the tool writes them by their FIT
field number on the `split` message:

| Field | # | Meaning |
| ----- | - | ------- |
| `climb_grading_scale` | 69 | grade system enum; `8` = V-scale (vermin) |
| `climb_grade_value`   | 70 | grade on that scale; for V-scale it's the `vermin` enum, `V + 1` (V0 → 1, V4 → 5) |
| `status`              | 71 | `3` = climb completed (send), `2` = attempted |

Each split also carries `253` (session start, FIT-epoch seconds) and a set of
constant envelope fields copied from a real watch file. Unrated climbs still emit
a split (so they count as a route) but carry no `climb_grade_value`. The
`split_summary` records `num_splits` (route count) and the max grade. The tool
deliberately does **not** emit field `73` (`climb_send`) — the watch never sets it
and it isn't needed.

> **Garmin limitation — aggregate tiles don't populate on manual upload.**
> Garmin Connect's **Total Routes** and **Max Grade** summary tiles come from a
> `splitSummaries` block that Garmin computes only in its *device-sync* ingestion
> pipeline, **not** for manually-uploaded FIT files. This was proven by
> re-uploading a real watch file byte-for-byte through the upload API: it still
> produced `splitSummaries: None`, while the device-synced copy had it. So an
> uploaded file shows the **individual routes with their grades** (which works
> great) but not the aggregate tiles. The tool still writes the clean
> `split_summary` (it's harmless and correct); this is a Garmin platform
> limitation of manual FIT upload, not something the file can fix.

The generated file decodes cleanly with `garmin-fit-sdk` (verified in the tests),
and the recipe is upload-verified end-to-end against a live Garmin account.
