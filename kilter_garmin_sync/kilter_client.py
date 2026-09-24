"""Client for the new Kilter backend (kiltergrips.com).

Kilter split from Aurora and removed the old Aurora API that BoardLib targeted;
``kilterboardapp.com`` is dead. The new backend uses:

* **Keycloak** for auth (OAuth2 *password* grant).
* **PowerSync** for data, streamed as newline-delimited JSON from
  ``/sync/stream``.

This module speaks the PowerSync HTTP stream protocol directly with the Python
standard library (no third-party HTTP or ``@powersync/node`` dependency), so the
tool stays single-language and CI-friendly. The raw protocol was verified to
work against the live backend; a Node helper would only be needed if that ever
stops being practical.

Credentials come from the caller (env vars ``KILTER_USERNAME`` /
``KILTER_PASSWORD`` in the CLI). Nothing here logs or persists tokens.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

TOKEN_URL = "https://idp.kiltergrips.com/realms/kilter/protocol/openid-connect/token"
SYNC_URL = "https://sync1.kiltergrips.com/sync/stream"
KEYCLOAK_CLIENT_ID = "kilter"
KEYCLOAK_SCOPE = "openid offline_access"

USER_BUCKET_PREFIX = "user_buckets"


class KilterAuthError(RuntimeError):
    """Raised when the Keycloak token request fails."""


class KilterSyncError(RuntimeError):
    """Raised when the PowerSync stream request fails."""


def get_access_token(
    username: str,
    password: str,
    *,
    url: str = TOKEN_URL,
    timeout: float = 60.0,
) -> str:
    """Exchange username/password for a Keycloak access token (JWT).

    Uses the OAuth2 *password* grant with ``client_id=kilter`` and
    ``scope=openid offline_access`` (mirroring the reference client). Returns the
    ``access_token`` string; raises :class:`KilterAuthError` on failure.
    """
    if not username or not password:
        raise KilterAuthError(
            "Missing credentials: set KILTER_USERNAME and KILTER_PASSWORD."
        )
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT_ID,
            "scope": KEYCLOAK_SCOPE,
            "username": username,
            "password": password,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=form,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        detail = _safe_error_body(exc)
        raise KilterAuthError(
            f"Keycloak token request failed ({exc.code}). {detail}"
        ) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network path
        raise KilterAuthError(f"Could not reach Keycloak: {exc.reason}") from exc

    token = payload.get("access_token")
    if not token:
        raise KilterAuthError("Keycloak response did not include an access_token.")
    return token


def stream_sync(
    token: str,
    *,
    client_id: str | None = None,
    url: str = SYNC_URL,
    timeout: float = 600.0,
) -> Iterator[dict[str, Any]]:
    """Yield parsed PowerSync messages from the ``/sync/stream`` endpoint.

    Sends a full-sync request (``buckets: []``) and yields each newline-delimited
    JSON object as a dict. The caller is responsible for stopping once a
    ``checkpoint_complete`` message is seen; otherwise PowerSync keeps the
    connection open waiting for further changes.
    """
    body = json.dumps(
        {
            "buckets": [],
            "include_checksum": True,
            "raw_data": True,
            "client_id": client_id or str(uuid.uuid4()),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        detail = _safe_error_body(exc)
        raise KilterSyncError(
            f"PowerSync stream request failed ({exc.code}). {detail}"
        ) from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network path
        raise KilterSyncError(f"Could not reach PowerSync: {exc.reason}") from exc

    try:
        for raw in resp:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:  # pragma: no cover - defensive
                continue
    finally:
        resp.close()


def iter_data_ops(
    messages: Iterable[dict[str, Any]],
    *,
    bucket_prefix: str | None = None,
    stop_at_checkpoint_complete: bool = True,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(bucket, op)`` tuples from PowerSync ``data`` messages.

    Each ``op`` is a single row operation with keys such as ``op``
    (``PUT``/``REMOVE``), ``object_type``, ``object_id`` and ``data``. When
    ``raw_data`` is requested the ``data`` field is a JSON *string*; this helper
    decodes it into a dict in place. Pass ``bucket_prefix`` to keep only matching
    buckets (e.g. ``"user_buckets"``).
    """
    for msg in messages:
        if stop_at_checkpoint_complete and "checkpoint_complete" in msg:
            break
        data_msg = msg.get("data")
        if not isinstance(data_msg, dict):
            continue
        bucket = data_msg.get("bucket", "")
        if bucket_prefix is not None and not bucket.startswith(bucket_prefix):
            continue
        for op in data_msg.get("data", []) or []:
            row = op.get("data")
            if isinstance(row, str):
                try:
                    op = {**op, "data": json.loads(row)}
                except json.JSONDecodeError:  # pragma: no cover - defensive
                    pass
            yield bucket, op


def _safe_error_body(exc: urllib.error.HTTPError, limit: int = 300) -> str:
    """Return a short, secret-free snippet of an HTTP error body."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive
        return ""
    body = body.strip().replace("\n", " ")
    return body[:limit]
