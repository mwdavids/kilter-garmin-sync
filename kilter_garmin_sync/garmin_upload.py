"""Optional auto-upload of generated activity files to Garmin Connect.

Uploading is off by default; the primary workflow stays "generate files, import
manually". When enabled (``--upload``), we use `garth
<https://github.com/matin/garth>`_ to talk to Garmin's upload service.

Auth model (MFA-safe):
  * Preferred: a **saved token**. Run ``kilter-garmin-sync garmin-login`` once
    interactively; it handles any MFA prompt and prints a base64 token blob you
    store as the ``GARMIN_TOKEN`` env var / CI secret. Non-interactive runs
    (including CI) then resume from that token and never see MFA.
  * Fallback: ``GARMIN_EMAIL`` / ``GARMIN_PASSWORD`` env vars for a direct
    login (only works for accounts without MFA, so it's best-effort).

Duplicate protection: Garmin itself rejects a re-upload of the same activity
file with HTTP 409 (``detailedImportResult`` status), which we treat as
"already uploaded" rather than an error. We also keep a small local ledger of
uploaded filenames so repeat runs skip the network call entirely.

Async confirmation: Garmin frequently accepts an upload for asynchronous
processing, returning an ``uploadId`` with empty successes/failures. That is
NOT a confirmed import, so we poll the activity list for a bounded time to
confirm a new activity materialized. If it doesn't, the file is reported
``"pending"`` (not recorded in the ledger) so the next run retries it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

TOKEN_ENV = "GARMIN_TOKEN"
EMAIL_ENV = "GARMIN_EMAIL"
PASSWORD_ENV = "GARMIN_PASSWORD"

# Garmin's upload endpoint often returns an ``uploadId`` with EMPTY
# successes/failures: the file was accepted for ASYNC processing, not
# confirmed imported. We poll this activity-search endpoint to confirm a new
# activity actually materialized before recording success.
ACTIVITY_SEARCH_PATH = "/activitylist-service/activities/search/activities"


class GarminAuthError(RuntimeError):
    """Raised when no usable Garmin credentials/token are available."""


def _import_garth():
    try:
        import garth  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via message
        raise GarminAuthError(
            "garth is not installed; run 'pip install garth' (it is listed in "
            "requirements.txt) to enable --upload."
        ) from exc
    return garth


# garth 0.8.0 sends its browser ``SSO_PAGE_HEADERS`` (a desktop User-Agent) on
# the mobile login and MFA-verify POSTs. Garmin's mobile API expects the default
# session UA (``GCM-iOS-...``) and rejects the browser UA with HTTP 429 (garth
# PR #218 / issue #217). We strip those headers from just those two POSTs.
_MOBILE_POST_PATHS = ("/mobile/api/login", "/mobile/api/mfa/verifyCode")
_PATCH_FLAG = "_kgs_mobile_header_patched"


def patch_garth_mobile_headers(garth_module: Any) -> bool:
    """Remove browser SSO headers from garth's mobile login/MFA POSTs.

    This runtime monkeypatch wraps ``garth.http.Client.request`` so that the
    ``/mobile/api/login`` and ``/mobile/api/mfa/verifyCode`` POSTs no longer
    carry garth's browser ``SSO_PAGE_HEADERS``; without them the client's
    default mobile User-Agent is used and Garmin stops returning 429. The
    browser headers are left untouched on the sign-in GET and the
    ``/portal/sso/embed`` GET, where they are still required. Idempotent;
    returns ``True`` if a patch is now in place.
    """
    http = getattr(garth_module, "http", None)
    client_cls = getattr(http, "Client", None)
    if client_cls is None:
        return False
    if getattr(client_cls, _PATCH_FLAG, False):
        return True
    try:
        from garth.sso import SSO_PAGE_HEADERS  # noqa: PLC0415

        browser_keys = set(SSO_PAGE_HEADERS)
    except Exception:  # pragma: no cover - garth internals changed
        browser_keys = {"User-Agent"}

    original_request = client_cls.request

    def request(self, method, subdomain, path, *args, **kwargs):
        if method == "POST" and path in _MOBILE_POST_PATHS:
            headers = kwargs.get("headers")
            if headers:
                headers = dict(headers)
                for key in browser_keys:
                    headers.pop(key, None)
                kwargs["headers"] = headers
        return original_request(self, method, subdomain, path, *args, **kwargs)

    client_cls.request = request
    client_cls._kgs_original_request = original_request
    setattr(client_cls, _PATCH_FLAG, True)
    return True


def make_token(
    email: str,
    password: str,
    *,
    prompt_mfa: Callable[[], str] | None = None,
    garth_module: Any | None = None,
) -> str:
    """Log in interactively and return a base64 token blob for ``GARMIN_TOKEN``.

    ``prompt_mfa`` is called (returning the MFA code as a string) only if Garmin
    requires MFA. The returned string is what the user saves as the
    ``GARMIN_TOKEN`` secret; it contains OAuth tokens, not the password.
    """
    garth = garth_module or _import_garth()
    patch_garth_mobile_headers(garth)
    if prompt_mfa is not None:
        garth.login(email, password, prompt_mfa=prompt_mfa)
    else:
        garth.login(email, password)
    return garth.client.dumps()


def resume_client(
    *,
    token: str | None = None,
    email: str | None = None,
    password: str | None = None,
    garth_module: Any | None = None,
) -> Any:
    """Return an authenticated garth client from a token or email/password.

    Resolution order: explicit ``token`` -> ``GARMIN_TOKEN`` env -> explicit
    email/password -> ``GARMIN_EMAIL``/``GARMIN_PASSWORD`` env. Raises
    :class:`GarminAuthError` if nothing usable is present.
    """
    garth = garth_module or _import_garth()

    token = token or os.environ.get(TOKEN_ENV)
    if token:
        garth.client.loads(token)
        return garth.client

    email = email or os.environ.get(EMAIL_ENV)
    password = password or os.environ.get(PASSWORD_ENV)
    if email and password:
        garth.login(email, password)
        return garth.client

    raise GarminAuthError(
        f"no Garmin credentials: set {TOKEN_ENV} (from 'kilter-garmin-sync "
        f"garmin-login'), or {EMAIL_ENV}/{PASSWORD_ENV}."
    )


@dataclass
class UploadResult:
    path: Path
    # "uploaded"/"duplicate"/"skipped" are terminal successes; "pending" means
    # Garmin queued the file but no activity has confirmed yet (ok=False, so the
    # ledger does not record it and the next run retries).
    status: str
    activity_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("uploaded", "duplicate", "skipped")


class UploadLedger:
    """Tiny JSON record of filenames already uploaded to Garmin."""

    def __init__(self, path: Path, names: set[str] | None = None):
        self.path = Path(path)
        self.names = names or set()

    @classmethod
    def load(cls, path: str | Path) -> UploadLedger:
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            names = set(data.get("uploaded", []))
        except (json.JSONDecodeError, OSError):
            names = set()
        return cls(path, names)

    def contains(self, name: str) -> bool:
        return name in self.names

    def add(self, name: str) -> None:
        self.names.add(name)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"uploaded": sorted(self.names)}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _parse_upload_response(resp: dict[str, Any]) -> UploadResult | None:
    """Extract (status, activity_id) from Garmin's detailedImportResult, if any."""
    detail = resp.get("detailedImportResult") if isinstance(resp, dict) else None
    if not isinstance(detail, dict):
        return None
    successes = detail.get("successes") or []
    failures = detail.get("failures") or []
    if successes:
        activity_id = successes[0].get("internalId")
        return UploadResult(Path("."), "uploaded", activity_id)
    # A 409/duplicate surfaces as a failure with a specific message code.
    for failure in failures:
        for message in failure.get("messages", []):
            code = message.get("code")
            content = str(message.get("content", "")).lower()
            if code == 409 or "duplicate" in content or "already" in content:
                return UploadResult(Path("."), "duplicate")
    return None


def _is_async_queued(resp: Any) -> bool:
    """True if Garmin accepted the file for async processing (uploadId, no result).

    An ``uploadId`` with empty ``successes`` and ``failures`` means "queued",
    NOT imported: the activity can still silently fail to materialize.
    """
    detail = resp.get("detailedImportResult") if isinstance(resp, dict) else None
    if not isinstance(detail, dict):
        return False
    if detail.get("successes") or detail.get("failures"):
        return False
    return detail.get("uploadId") is not None


def _recent_activity_ids(client: Any, limit: int = 10) -> set[int]:
    """Return the internal ids of the most recent Garmin activities (best effort)."""
    try:
        resp = client.connectapi(
            ACTIVITY_SEARCH_PATH, params={"limit": limit, "start": 0}
        )
    except Exception:  # noqa: BLE001 - network/attr errors just mean "unknown"
        return set()
    ids: set[int] = set()
    if isinstance(resp, list):
        for item in resp:
            if isinstance(item, dict) and item.get("activityId") is not None:
                ids.add(item["activityId"])
    return ids


def _confirm_async_upload(
    client: Any,
    before_ids: set[int],
    *,
    timeout: float,
    poll_interval: float,
    sleeper: Callable[[float], Any],
    clock: Callable[[], float],
) -> int | None:
    """Poll the activity list until a new activity appears; return its id or None."""
    start = clock()
    delay = poll_interval
    while True:
        new_ids = _recent_activity_ids(client) - before_ids
        if new_ids:
            return max(new_ids)
        if clock() - start >= timeout:
            return None
        sleeper(delay)
        delay = min(delay * 2, 30.0)


def upload_file(
    client: Any,
    path: str | Path,
    *,
    confirm: bool = True,
    confirm_timeout: float = 180.0,
    confirm_poll_interval: float = 5.0,
    sleeper: Callable[[float], Any] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> UploadResult:
    """Upload one FIT/TCX file, treating a 409 duplicate as success.

    When Garmin returns an ``uploadId`` with no successes/failures (the common
    async case), we poll the activity list for up to ``confirm_timeout`` seconds
    to confirm a new activity materialized. If it doesn't, we return a
    ``"pending"`` result (ok=False) so the caller does not record it and retries.
    """
    path = Path(path)
    before = _recent_activity_ids(client) if confirm else set()
    try:
        with path.open("rb") as fp:
            resp = client.upload(fp)
    except Exception as exc:  # noqa: BLE001 - inspect for a duplicate signal
        if _looks_like_conflict(exc):
            return UploadResult(path, "duplicate")
        raise
    parsed = _parse_upload_response(resp) if isinstance(resp, dict) else None
    if parsed is not None:
        return UploadResult(path, parsed.status, parsed.activity_id)
    if confirm and _is_async_queued(resp):
        activity_id = _confirm_async_upload(
            client,
            before,
            timeout=confirm_timeout,
            poll_interval=confirm_poll_interval,
            sleeper=sleeper,
            clock=clock,
        )
        if activity_id is None:
            return UploadResult(path, "pending")
        return UploadResult(path, "uploaded", activity_id)
    return UploadResult(path, "uploaded")


def _looks_like_conflict(exc: Exception) -> bool:
    """True if the exception looks like an HTTP 409 (already uploaded)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 409:
        return True
    return "409" in str(exc) or "conflict" in str(exc).lower()


def upload_files(
    paths: list[Path],
    *,
    client: Any | None = None,
    ledger: UploadLedger | None = None,
    token: str | None = None,
    email: str | None = None,
    password: str | None = None,
    garth_module: Any | None = None,
) -> list[UploadResult]:
    """Upload several files, skipping any already recorded in ``ledger``."""
    if client is None:
        client = resume_client(
            token=token, email=email, password=password, garth_module=garth_module
        )
    results: list[UploadResult] = []
    for path in paths:
        path = Path(path)
        if ledger is not None and ledger.contains(path.name):
            results.append(UploadResult(path, "skipped"))
            continue
        result = upload_file(client, path)
        results.append(result)
        if ledger is not None and result.ok:
            ledger.add(path.name)
    if ledger is not None:
        ledger.save()
    return results
