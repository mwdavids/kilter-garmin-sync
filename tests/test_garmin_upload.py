"""Tests for the optional Garmin upload path (garth fully mocked, no network)."""

from __future__ import annotations

import pytest

from kilter_garmin_sync.garmin_upload import (
    GarminAuthError,
    UploadLedger,
    make_token,
    resume_client,
    upload_file,
    upload_files,
)


class FakeClient:
    """Stand-in for garth.client: records uploaded filenames, scriptable responses."""

    def __init__(self, responses=None, raise_exc=None):
        self.uploaded: list[str] = []
        self._responses = list(responses or [])
        self._raise = raise_exc

    def upload(self, fp):
        self.uploaded.append(fp.name.split("\\")[-1].split("/")[-1])
        if self._raise is not None:
            raise self._raise
        if self._responses:
            return self._responses.pop(0)
        return {"detailedImportResult": {"successes": [{"internalId": 12345}]}}


class FakeGarth:
    """Stand-in for the garth module."""

    def __init__(self):
        self.client = FakeClient()
        self.logged_in_with = None
        self._token = None

    def login(self, email, password, prompt_mfa=None):
        self.logged_in_with = (email, password)

    # client.loads/dumps live on the client in real garth; mirror that.


class _TokenClient(FakeClient):
    def dumps(self):
        return "BASE64TOKEN"

    def loads(self, token):
        self.loaded_token = token


def _fit(tmp_path):
    path = tmp_path / "kilter-2026-03-01.fit"
    path.write_bytes(b"FITDATA")
    return path


def test_upload_file_success(tmp_path):
    client = FakeClient()
    result = upload_file(client, _fit(tmp_path))
    assert result.status == "uploaded"
    assert result.activity_id == 12345
    assert client.uploaded == ["kilter-2026-03-01.fit"]


def test_upload_file_duplicate_via_409(tmp_path):
    class Boom(Exception):
        pass

    exc = Boom("HTTP 409 Conflict")
    result = upload_file(FakeClient(raise_exc=exc), _fit(tmp_path))
    assert result.status == "duplicate"


def test_upload_file_duplicate_via_response(tmp_path):
    resp = {
        "detailedImportResult": {
            "successes": [],
            "failures": [
                {"messages": [{"code": 409, "content": "Duplicate Activity"}]}
            ],
        }
    }
    result = upload_file(FakeClient(responses=[resp]), _fit(tmp_path))
    assert result.status == "duplicate"


def test_upload_files_skips_ledger_entries(tmp_path):
    ledger = UploadLedger(tmp_path / "uploaded.json")
    a = _fit(tmp_path)
    b = tmp_path / "kilter-2026-03-02.fit"
    b.write_bytes(b"FITDATA2")

    client = FakeClient(responses=[{"detailedImportResult": {"successes": [{"internalId": 1}]}}])
    ledger.add(a.name)  # pretend a was already uploaded

    results = upload_files([a, b], client=client, ledger=ledger)
    statuses = {r.path.name: r.status for r in results}
    assert statuses[a.name] == "skipped"
    assert statuses[b.name] == "uploaded"
    # Only the new file hit the network.
    assert client.uploaded == [b.name]
    # Ledger persisted and now contains both.
    reloaded = UploadLedger.load(tmp_path / "uploaded.json")
    assert reloaded.contains(a.name) and reloaded.contains(b.name)


def test_resume_client_prefers_token():
    garth = FakeGarth()
    garth.client = _TokenClient()
    client = resume_client(token="TOK", garth_module=garth)
    assert client.loaded_token == "TOK"


def test_resume_client_env_email(monkeypatch):
    garth = FakeGarth()
    client = resume_client(
        email="me@example.com", password="pw", garth_module=garth
    )
    assert garth.logged_in_with == ("me@example.com", "pw")
    assert client is garth.client


def test_resume_client_without_creds_raises():
    garth = FakeGarth()
    with pytest.raises(GarminAuthError):
        resume_client(garth_module=garth)


def test_make_token_returns_dump():
    garth = FakeGarth()
    garth.client = _TokenClient()
    token = make_token("me@example.com", "pw", garth_module=garth)
    assert token == "BASE64TOKEN"
    assert garth.logged_in_with == ("me@example.com", "pw")


class _RecordingClient:
    """Records the headers the underlying request() actually receives."""

    last: dict | None = None

    def request(self, method, subdomain, path, *args, **kwargs):
        type(self).last = {
            "method": method,
            "path": path,
            "headers": kwargs.get("headers"),
        }
        return "ok"


def _fake_garth_with_http():
    from types import SimpleNamespace

    client_cls = type("Client", (_RecordingClient,), {})
    return SimpleNamespace(http=SimpleNamespace(Client=client_cls))


def test_patch_strips_browser_headers_on_mobile_login():
    from garth.sso import SSO_PAGE_HEADERS

    from kilter_garmin_sync.garmin_upload import patch_garth_mobile_headers

    garth = _fake_garth_with_http()
    assert patch_garth_mobile_headers(garth) is True

    client = garth.http.Client()
    client.request("POST", "sso", "/mobile/api/login", headers=dict(SSO_PAGE_HEADERS))
    sent = client.last["headers"]
    # The browser SSO headers (desktop User-Agent etc.) must be gone.
    assert "User-Agent" not in sent
    for key in SSO_PAGE_HEADERS:
        assert key not in sent


def test_patch_strips_browser_headers_on_mfa_verify():
    from garth.sso import SSO_PAGE_HEADERS

    from kilter_garmin_sync.garmin_upload import patch_garth_mobile_headers

    garth = _fake_garth_with_http()
    patch_garth_mobile_headers(garth)
    client = garth.http.Client()
    client.request(
        "POST", "sso", "/mobile/api/mfa/verifyCode", headers=dict(SSO_PAGE_HEADERS)
    )
    assert "User-Agent" not in client.last["headers"]


def test_patch_keeps_browser_headers_on_sso_get():
    from garth.sso import SSO_PAGE_HEADERS

    from kilter_garmin_sync.garmin_upload import patch_garth_mobile_headers

    garth = _fake_garth_with_http()
    patch_garth_mobile_headers(garth)
    client = garth.http.Client()
    # The sign-in GET and /portal/sso/embed GET still need the browser headers.
    client.request("GET", "sso", "/sso", headers=dict(SSO_PAGE_HEADERS))
    assert client.last["headers"]["User-Agent"] == SSO_PAGE_HEADERS["User-Agent"]


def test_patch_is_idempotent():
    from kilter_garmin_sync.garmin_upload import patch_garth_mobile_headers

    garth = _fake_garth_with_http()
    assert patch_garth_mobile_headers(garth) is True
    # Second call must not re-wrap; it should report the patch is present.
    assert patch_garth_mobile_headers(garth) is True


def test_patch_noop_without_http_module():
    from kilter_garmin_sync.garmin_upload import patch_garth_mobile_headers

    # A garth stand-in without an http.Client (as in the other fakes) is a no-op.
    assert patch_garth_mobile_headers(FakeGarth()) is False

