# Run it in the cloud (GitHub Actions)

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
     `GARMIN_TOKEN` secret; see [auto-upload](auto-upload.md)).
   - `weight_lb` — body weight in lb for the calorie estimate (optional).
3. **Download the artifact.** When the run finishes, open it and download the
   **`kilter-activities`** artifact (a zip of `out/**`). Unzip it to get one
   `.fit`/`.tcx` per session. Thanks to the ledger (cached across runs), a later
   run's artifact contains only **newly-exported** days, so you won't re-import
   duplicates.
4. **Import to Garmin** using the steps in the [README](../README.md#importing-into-garmin-connect).

## Nightly auto-upload

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

## Email attachments for manual import

GitHub's built-in workflow notification emails cannot be customized to attach
files. The workflow can send a **separate email** through your own SMTP mail
provider, containing the newly generated `.fit` files (or `.tcx` when selected).
This is optional and disabled until configured.

Under **Settings → Secrets and variables → Actions**, add these repository
**secrets**:

| Secret | Value |
| --- | --- |
| `SMTP_HOST` | Your provider's SMTP hostname |
| `SMTP_USERNAME` | SMTP login username |
| `SMTP_PASSWORD` | SMTP password or provider-issued app password |
| `EMAIL_FROM` | Sender address authorized by your provider |
| `EMAIL_TO` | Recipient email address |

Add repository **variables**:

| Variable | Value |
| --- | --- |
| `EMAIL_ENABLED` | `true` to enable sending; unset or `false` to disable |
| `SMTP_PORT` | `465` (default, implicit TLS), or your provider's STARTTLS port (usually `587`) |

For example, Gmail uses `smtp.gmail.com`, port `465`, your Gmail address as
username and sender, and an **app password**, not your normal account password.
App passwords require two-step verification and may be restricted by account
policy. Never put passwords in the workflow or commit them.

Email runs on both manual and scheduled exports when new files exist, **even if
the Garmin upload step fails or remains pending**. No new files means no email;
diagnostic runs do not send mail. The email includes a workflow link and does
not claim the activity was imported into Garmin. Existing sessions skipped by
the export ledger are not attached.

Mail configuration/authentication/delivery errors fail the email step instead
of reporting success. Check the run logs and download the `kilter-activities`
artifact if mail delivery fails. Server acceptance does not guarantee inbox
delivery; check spam filtering too. If export fails before producing a file
(for example, on login or FIT encoding), there is no attachment to send; the
normal GitHub failure notification still applies.

Generated activity files and the Kilter database are produced only inside the
runner; activity files are uploaded as artifacts and optionally emailed. They
are **not** committed to the repo.
