# Auto-upload to Garmin Connect (optional)

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
uploaded filenames so repeat runs skip the network call entirely. Garmin often
accepts an upload *asynchronously* (returning an `uploadId` with no immediate
result), so the tool polls the activity list for a bounded time to confirm the
activity actually materialized before recording it; if it can't confirm, the
file is reported **pending** and is *not* written to the ledger, so the next run
retries it rather than silently dropping it. If neither a token nor
email/password is available, `--upload` prints a clear message and the generated
files are still written for manual import.

## Login rate-limits & the garth 429 header fix

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
