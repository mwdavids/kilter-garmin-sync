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
