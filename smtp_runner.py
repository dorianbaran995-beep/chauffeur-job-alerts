from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib import parse, request

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


def _telegram_api(token: str, method: str, data: dict[str, str] | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = parse.urlencode(data or {}).encode("utf-8") if data is not None else None
    req = request.Request(url, data=payload, method="POST" if payload is not None else "GET")
    with request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result.get('description', result)}")
    return result


def _discover_telegram_chats(token: str) -> dict[str, dict]:
    updates = _telegram_api(
        token,
        "getUpdates",
        {"timeout": "0", "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member"])},
    )
    chats: dict[str, dict] = {}
    for update in updates.get("result", []):
        candidates: list[dict] = []
        for key in ("message", "channel_post"):
            obj = update.get(key)
            if isinstance(obj, dict) and isinstance(obj.get("chat"), dict):
                candidates.append(obj["chat"])
        member = update.get("my_chat_member")
        if isinstance(member, dict) and isinstance(member.get("chat"), dict):
            candidates.append(member["chat"])

        for chat in candidates:
            chat_id = str(chat.get("id", "")).strip()
            if chat_id and chat.get("type") in {"group", "supergroup", "channel"}:
                chats[chat_id] = chat
    return chats


def _telegram_credentials() -> tuple[str, str]:
    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or os.environ.get("BOT_TOKEN", "").strip()
    )
    chat_id = (
        os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        or os.environ.get("CHAT_ID", "").strip()
    )

    if token and not chat_id:
        chats = _discover_telegram_chats(token)
        if len(chats) == 1:
            chat_id = next(iter(chats))
            chat = chats[chat_id]
            title = chat.get("title") or chat.get("username") or "Telegram destination"
            job_app.log.info("Telegram destination auto-detected: %s (%s)", title, chat_id)
        elif len(chats) > 1:
            job_app.log.warning(
                "More than one Telegram destination was found; set TELEGRAM_CHAT_ID to choose one. Found: %s",
                ", ".join(chats.keys()),
            )
        else:
            job_app.log.warning(
                "No Telegram channel/group found. Add the bot to the destination and post a new message after adding it."
            )

    return token, chat_id


def _telegram_job_text(job: job_app.Job) -> str:
    icon = "🚘" if job_app.is_chauffeur_job(job) else "➕"
    lines = [
        f"{icon} {job.title}",
        f"🏢 {job.company}",
        f"📍 {job.location} ({job.market})",
        f"💰 {job.salary}",
        f"🗓 {job.date_posted} · {job.site}",
    ]
    if job.url:
        lines.append(f"🔗 {job.url}")
    return "\n".join(lines)


def _telegram_chunks(jobs: list[job_app.Job], max_chars: int = 3500) -> list[str]:
    if not jobs:
        return []

    chauffeur_count = sum(1 for job in jobs if job_app.is_chauffeur_job(job))
    header = (
        f"🚘 CHAUFFEUR JOB ALERTS\n"
        f"{len(jobs)} new matching vacancies · {chauffeur_count} chauffeur-titled\n\n"
    )

    chunks: list[str] = []
    current = header
    for job in jobs:
        block = _telegram_job_text(job) + "\n\n"
        if len(current) + len(block) > max_chars and current.strip():
            chunks.append(current.rstrip())
            current = ""
        current += block

    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def send_telegram(jobs: list[job_app.Job]) -> None:
    token, chat_id = _telegram_credentials()
    if not token:
        job_app.log.info("TELEGRAM_BOT_TOKEN is not configured; skipping Telegram delivery")
        return
    if not chat_id:
        job_app.log.info("Telegram destination is not configured or uniquely discoverable; skipping Telegram delivery")
        return

    if not jobs:
        job_app.log.info("No new jobs; skipping Telegram zero-result message")
        return

    for chunk in _telegram_chunks(jobs):
        _telegram_api(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": "true",
            },
        )


def send_notifications(jobs: list[job_app.Job], cfg: dict[str, Any]) -> None:
    send_email(jobs, cfg)
    try:
        send_telegram(jobs)
    except Exception as exc:
        job_app.log.error("Telegram delivery failed: %s", exc)


job_app.send_email = send_notifications


if __name__ == "__main__":
    sys.exit(job_app.main())
