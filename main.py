from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from jobspy import scrape_jobs

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
SEEN_PATH = ROOT / "data" / "seen_jobs.json"
CSV_PATH = ROOT / "jobs_latest.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chauffeur-jobs")


@dataclass
class Job:
    uid: str
    title: str
    company: str
    location: str
    market: str
    site: str
    date_posted: str
    salary: str
    url: str
    description: str


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen() -> dict[str, str]:
    if not SEEN_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_seen(seen: dict[str, str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = list(seen.items())[-5000:]
    SEEN_PATH.write_text(json.dumps(dict(items), indent=2, ensure_ascii=False), encoding="utf-8")


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def build_uid(row: pd.Series) -> str:
    direct_url = safe_text(row.get("job_url_direct")) or safe_text(row.get("job_url"))
    base = "|".join(
        [
            direct_url,
            safe_text(row.get("title")).lower(),
            safe_text(row.get("company")).lower(),
            safe_text(row.get("location")).lower(),
        ]
    )
    return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()[:24]


def salary_text(row: pd.Series) -> str:
    interval = safe_text(row.get("interval"))
    min_amount = safe_text(row.get("min_amount"))
    max_amount = safe_text(row.get("max_amount"))
    currency = safe_text(row.get("currency"))
    if min_amount or max_amount:
        if min_amount and max_amount:
            amount = f"{min_amount}–{max_amount}"
        else:
            amount = min_amount or max_amount
        return " ".join(x for x in [currency, amount, interval] if x)
    return "Not stated"


def title_is_relevant(title: str, cfg: dict[str, Any]) -> bool:
    t = title.lower()
    includes = [x.lower() for x in cfg["filter"].get("include_title_terms", [])]
    excludes = [x.lower() for x in cfg["filter"].get("exclude_title_terms", [])]
    if any(x in t for x in excludes):
        return False
    return any(x in t for x in includes)


def terms_for_market(market: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    all_terms = cfg["search"]["keywords"]
    if market.get("priority") == "primary":
        return all_terms
    preferred = ["chauffeur", "private chauffeur", "family chauffeur", "executive driver", "VIP driver", "security driver"]
    return [t for t in all_terms if t in preferred]


def scrape_one(term: str, market: dict[str, Any], cfg: dict[str, Any]) -> pd.DataFrame:
    sites = cfg["search"].get("sites", ["indeed", "linkedin", "google"])
    location = market["location"]
    kwargs = dict(
        site_name=sites,
        search_term=term,
        google_search_term=f'{term} jobs in {location} posted in the last 3 days',
        location=location,
        results_wanted=int(cfg["search"].get("results_per_search", 25)),
        hours_old=int(cfg["search"].get("hours_old", 72)),
        country_indeed=market["country_indeed"],
        linkedin_fetch_description=False,
        verbose=1,
    )
    try:
        frame = scrape_jobs(**kwargs)
        if frame is None or len(frame) == 0:
            return pd.DataFrame()
        frame["_market"] = market["name"]
        frame["_term"] = term
        return frame
    except Exception as exc:
        log.warning("Search failed: %s / %s: %s", market["name"], term, exc)
        return pd.DataFrame()


def collect_jobs(cfg: dict[str, Any]) -> list[Job]:
    frames: list[pd.DataFrame] = []
    for market in cfg["search"]["markets"]:
        for term in terms_for_market(market, cfg):
            log.info("Searching %s for %r", market["name"], term)
            frame = scrape_one(term, market, cfg)
            if not frame.empty:
                frames.append(frame)
            time.sleep(1.2)

    if not frames:
        return []

    data = pd.concat(frames, ignore_index=True)
    data.to_csv(CSV_PATH, index=False)

    dedup: dict[str, Job] = {}
    for _, row in data.iterrows():
        title = safe_text(row.get("title"))
        if not title or not title_is_relevant(title, cfg):
            continue
        uid = build_uid(row)
        url = safe_text(row.get("job_url_direct")) or safe_text(row.get("job_url"))
        job = Job(
            uid=uid,
            title=title,
            company=safe_text(row.get("company")) or "Not stated",
            location=safe_text(row.get("location")) or safe_text(row.get("_market")),
            market=safe_text(row.get("_market")),
            site=safe_text(row.get("site")) or "Job board",
            date_posted=safe_text(row.get("date_posted")) or "Recently posted",
            salary=salary_text(row),
            url=url,
            description=safe_text(row.get("description"))[:700],
        )
        dedup.setdefault(uid, job)

    return list(dedup.values())


def make_html(jobs: list[Job]) -> str:
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    cards = []
    for job in jobs:
        link = f'<a href="{html.escape(job.url)}" style="color:#0b57d0;font-weight:700">Open vacancy</a>' if job.url else "No direct link"
        cards.append(
            f"""
            <div style="border:1px solid #ddd;border-radius:10px;padding:16px;margin:0 0 14px 0;font-family:Arial,sans-serif">
              <div style="font-size:18px;font-weight:700">{html.escape(job.title)}</div>
              <div style="margin-top:5px"><strong>{html.escape(job.company)}</strong> · {html.escape(job.location)}</div>
              <div style="margin-top:5px;color:#555">Market: {html.escape(job.market)} · Source: {html.escape(job.site)} · Posted: {html.escape(job.date_posted)}</div>
              <div style="margin-top:5px">Salary: {html.escape(job.salary)}</div>
              <div style="margin-top:10px">{link}</div>
            </div>
            """
        )
    return f"""
    <html><body style="max-width:820px;margin:auto;padding:22px;background:#fafafa">
      <div style="font-family:Arial,sans-serif">
        <h1 style="margin-bottom:4px">Daily Chauffeur Jobs</h1>
        <p style="color:#555;margin-top:0">{today} · {len(jobs)} new matching vacancies</p>
        {''.join(cards)}
        <p style="font-size:12px;color:#777">Automated search from public job boards. Always confirm that a vacancy is still open before applying.</p>
      </div>
    </body></html>
    """


def make_text(jobs: list[Job]) -> str:
    lines = [f"Daily Chauffeur Jobs — {len(jobs)} new matching vacancies", ""]
    for i, job in enumerate(jobs, 1):
        lines += [
            f"{i}. {job.title}",
            f"   {job.company} — {job.location}",
            f"   {job.market} | {job.site} | {job.date_posted} | {job.salary}",
            f"   {job.url}",
            "",
        ]
    return "\n".join(lines)


def send_email(jobs: list[Job], cfg: dict[str, Any]) -> None:
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
    msg.attach(MIMEText(make_text(jobs), "plain", "utf-8"))
    msg.attach(MIMEText(make_html(jobs), "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipients, msg.as_string())


def main() -> int:
    cfg = load_config()
    seen = load_seen()
    jobs = collect_jobs(cfg)
    log.info("Collected %d relevant unique jobs", len(jobs))

    new_jobs = [job for job in jobs if job.uid not in seen]
    max_jobs = int(cfg["search"].get("max_email_jobs", 120))
    new_jobs = new_jobs[:max_jobs]

    if new_jobs:
        send_email(new_jobs, cfg)
        now = datetime.now(timezone.utc).isoformat()
        for job in new_jobs:
            seen[job.uid] = now
        save_seen(seen)
        log.info("Emailed %d new jobs", len(new_jobs))
    else:
        send_email([], cfg)
        log.info("No unseen matching jobs today; sent zero-result email")
    return 0


if __name__ == "__main__":
    sys.exit(main())
