"""Email newly generated activity files from the CI runner over TLS."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Mapping


def send_activity_email(
    out_dir: Path, *, environ: Mapping[str, str] | None = None
) -> int:
    """Send one email containing the generated FIT/TCX files; return their count."""
    paths = sorted(
        path for path in out_dir.glob("*")
        if path.is_file() and path.suffix.lower() in (".fit", ".tcx")
    )
    if not paths:
        return 0

    env = os.environ if environ is None else environ
    required = (
        "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"
    )
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise ValueError(f"Missing email configuration: {', '.join(missing)}")
    port = int(env.get("SMTP_PORT") or "465")
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")

    message = EmailMessage()
    message["From"] = env["EMAIL_FROM"]
    message["To"] = env["EMAIL_TO"]
    message["Subject"] = f"Kilter activities: {len(paths)} file(s) for Garmin"
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    body = (
        "Your newly generated Kilter activity files are attached.\n\n"
        "To import manually, open Garmin Connect, select + > Import Data, "
        "and upload the attached FIT or TCX files.\n\n"
        "This email does not confirm automatic upload to Garmin. If an activity "
        "already appears in Garmin, you do not need to import it again.\n"
    )
    if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
        server = env.get("GITHUB_SERVER_URL", "https://github.com")
        body += (
            f"\nWorkflow run: {server}/{env['GITHUB_REPOSITORY']}"
            f"/actions/runs/{env['GITHUB_RUN_ID']}\n"
        )
    message.set_content(body)
    for path in paths:
        message.add_attachment(
            path.read_bytes(), maintype="application", subtype="octet-stream",
            filename=path.name,
        )

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(
            env["SMTP_HOST"], port, timeout=30, context=context
        ) as client:
            client.login(env["SMTP_USERNAME"], env["SMTP_PASSWORD"])
            refused = client.send_message(message)
    else:
        with smtplib.SMTP(env["SMTP_HOST"], port, timeout=30) as client:
            client.starttls(context=context)
            client.login(env["SMTP_USERNAME"], env["SMTP_PASSWORD"])
            refused = client.send_message(message)
    if refused:
        raise smtplib.SMTPException("Email delivery refused for some recipients")
    return len(paths)


def main() -> None:
    count = send_activity_email(Path("out"))
    if count:
        print(f"Mail server accepted email with {count} activity attachment(s).")
    else:
        print("No new activity files to email.")


if __name__ == "__main__":
    main()
