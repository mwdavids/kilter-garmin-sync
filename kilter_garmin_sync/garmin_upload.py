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
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

TOKEN_ENV = "GARMIN_TOKEN"
EMAIL_ENV = "GARMIN_EMAIL"
PASSWORD_ENV = "GARMIN_PASSWORD"


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
    status: str  # "uploaded", "duplicate", or "skipped"
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


def upload_file(client: Any, path: str | Path) -> UploadResult:
    """Upload one FIT/TCX file, treating a 409 duplicate as success."""
    path = Path(path)
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
