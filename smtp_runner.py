from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import main as job_app


def send_email(jobs: list[job_app.Job], cfg: dict[str, Any]) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.hostinger.com").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_security = os.environ.get("SMTP_SECURITY", "ssl").strip().lower()
    smtp_user = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "").strip()

    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP_USERNAME and SMTP_PASSWORD GitHub secrets are required")

    configured_to = os.environ.get("EMAIL_TO", "").strip() or str(cfg["email"]["to"]).strip()
    recipients = [address.strip() for address in configured_to.replace(";", ",").split(",") if address.strip()]
    if not recipients:
        raise RuntimeError("At least one recipient is required in EMAIL_TO or config.yaml")

    prefix = cfg["email"].get("subject_prefix", "Daily Chauffeur Jobs")
    subject = f"{prefix}: {len(jobs)} new jobs"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(job_app.make_text(jobs), "plain", "utf-8"))
    msg.attach(MIMEText(job_app.make_html(jobs), "html", "utf-8"))

    if smtp_security == "ssl":
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30, context=context) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
    elif smtp_security == "starttls":
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
    else:
        raise RuntimeError("SMTP_SECURITY must be either 'ssl' or 'starttls'")


job_app.send_email = send_email


if __name__ == "__main__":
    sys.exit(job_app.main())
