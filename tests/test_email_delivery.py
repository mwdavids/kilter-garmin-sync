"""Activity attachments and encrypted SMTP delivery, without real credentials."""

import smtplib
from unittest.mock import MagicMock

import pytest

from kilter_garmin_sync import email_delivery


@pytest.fixture
def mail_env():
    return {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_USERNAME": "sender",
        "SMTP_PASSWORD": "test-password",
        "EMAIL_FROM": "sender@example.com",
        "EMAIL_TO": "recipient@example.com",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "example/repo",
        "GITHUB_RUN_ID": "123",
    }


@pytest.mark.parametrize("port", ["465", "587"])
def test_sends_activity_bytes_over_tls(tmp_path, monkeypatch, mail_env, port):
    mail_env["SMTP_PORT"] = port
    files = {"kilter-2026-11-10.fit": b"\x00FIT\xff", "other.tcx": b"<xml/>"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "private.json").write_text("not an attachment")
    ssl_factory = MagicMock()
    smtp_factory = MagicMock()
    monkeypatch.setattr(email_delivery.smtplib, "SMTP_SSL", ssl_factory)
    monkeypatch.setattr(email_delivery.smtplib, "SMTP", smtp_factory)
    factory = ssl_factory if port == "465" else smtp_factory
    client = factory.return_value.__enter__.return_value
    client.send_message.return_value = {}

    assert email_delivery.send_activity_email(tmp_path, environ=mail_env) == 2
    factory.assert_called_once()
    assert factory.call_args.args == ("smtp.example.com", int(port))
    if port == "465":
        smtp_factory.assert_not_called()
        assert factory.call_args.kwargs["context"].check_hostname
    else:
        ssl_factory.assert_not_called()
        client.starttls.assert_called_once()
        assert [call[0] for call in client.method_calls] == [
            "starttls", "login", "send_message"
        ]
    client.login.assert_called_once_with("sender", "test-password")
    message = client.send_message.call_args.args[0]
    assert message["To"] == mail_env["EMAIL_TO"]
    assert message["From"] == mail_env["EMAIL_FROM"]
    assert "https://github.com/example/repo/actions/runs/123" in message.get_body().get_content()
    assert {
        part.get_filename(): part.get_payload(decode=True)
        for part in message.iter_attachments()
    } == files


def test_no_files_needs_no_configuration(tmp_path):
    assert email_delivery.send_activity_email(tmp_path, environ={}) == 0


def test_missing_configuration_fails_before_connecting(tmp_path, monkeypatch):
    (tmp_path / "session.fit").write_bytes(b"FIT")
    factory = MagicMock()
    monkeypatch.setattr(email_delivery.smtplib, "SMTP_SSL", factory)
    with pytest.raises(ValueError, match="SMTP_PASSWORD"):
        email_delivery.send_activity_email(tmp_path, environ={})
    factory.assert_not_called()


@pytest.mark.parametrize("refused", [False, True])
def test_delivery_failure_is_not_reported_as_success(
    tmp_path, monkeypatch, mail_env, refused
):
    (tmp_path / "session.fit").write_bytes(b"FIT")
    factory = MagicMock()
    monkeypatch.setattr(email_delivery.smtplib, "SMTP_SSL", factory)
    client = factory.return_value.__enter__.return_value
    if refused:
        client.send_message.return_value = {"recipient@example.com": (550, b"Rejected")}
    else:
        client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Rejected")
    with pytest.raises(smtplib.SMTPException):
        email_delivery.send_activity_email(tmp_path, environ=mail_env)
