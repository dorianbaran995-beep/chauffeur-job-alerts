from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
    priority: str
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
    items = list(seen.items())[-10000:]
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
        amount = f"{min_amount}–{max_amount}" if min_amount and max_amount else (min_amount or max_amount)
        return " ".join(x for x in [currency, amount, interval] if x)
    return "Not stated"


def title_is_relevant(title: str, priority: str, cfg: dict[str, Any]) -> bool:
    t = title.lower()
    base_excludes = [x.lower() for x in cfg["filter"].get("exclude_title_terms", [])]
    if any(x in t for x in base_excludes):
        return False

    if priority == "local":
        local_excludes = [x.lower() for x in cfg["filter"].get("local_exclude_title_terms", [])]
        if any(x in t for x in local_excludes):
            return False
        local_includes = [x.lower() for x in cfg["filter"].get("local_include_title_terms", ["driver", "chauffeur"])]
        return any(x in t for x in local_includes)

    includes = [x.lower() for x in cfg["filter"].get("include_title_terms", [])]
    return any(x in t for x in includes)


def is_chauffeur_job(job: Job) -> bool:
    """Primary result type: any vacancy with chauffeur in the job title."""
    return "chauffeur" in job.title.lower()


def is_doncaster_local_job(job: Job) -> bool:
    """Broader non-chauffeur driving jobs from the Doncaster-area scan."""
    return job.priority == "local" and not is_chauffeur_job(job)


def job_sort_key(job: Job) -> tuple[Any, ...]:
    """Chauffeur first, then Doncaster local, then other UK premium driver roles."""
    if is_chauffeur_job(job):
        section = 0
    elif is_doncaster_local_job(job):
        section = 1
    else:
        section = 2

    return (
        section,
        job.title.lower(),
        job.location.lower(),
        job.company.lower(),
    )


def terms_for_market(market: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    all_terms = cfg["search"]["keywords"]
    priority = market.get("priority", "major")

    if priority == "local":
        return cfg["search"].get("local_keywords", ["driver", "chauffeur"])

    if priority == "primary":
        return all_terms

    preferred = {
        "chauffeur",
        "private chauffeur",
        "family chauffeur",
        "executive chauffeur",
        "private driver",
        "executive driver",
        "VIP driver",
        "security driver",
    }
    return [term for term in all_terms if term in preferred]


def scrape_one(term: str, market: dict[str, Any], cfg: dict[str, Any]) -> pd.DataFrame:
    sites = cfg["search"].get("sites", ["indeed", "linkedin", "google"])
    location = market["location"]
    hours_old = int(cfg["search"].get("hours_old", 168))
    days_old = max(1, math.ceil(hours_old / 24))

    kwargs = dict(
        site_name=sites,
        search_term=term,
        google_search_term=f'{term} jobs in {location} posted in the last {days_old} days',
        location=location,
        distance=int(market.get("distance", 50)),
        results_wanted=int(cfg["search"].get("results_per_search", 35)),
        hours_old=hours_old,
        country_indeed=market["country_indeed"],
        linkedin_fetch_description=False,
        verbose=1,
    )

    try:
        frame = scrape_jobs(**kwargs)
        if frame is None or len(frame) == 0:
            return pd.DataFrame()
        frame["_market"] = market["name"]
        frame["_priority"] = market.get("priority", "major")
        frame["_term"] = term
        return frame
    except Exception as exc:
        log.warning("Search failed: %s / %s: %s", market["name"], term, exc)
        return pd.DataFrame()


def collect_jobs(cfg: dict[str, Any]) -> list[Job]:
    frames: list[pd.DataFrame] = []
    markets = cfg["search"]["markets"]
    total_searches = sum(len(terms_for_market(market, cfg)) for market in markets)
    completed = 0

    log.info("Starting UK-wide chauffeur + Doncaster driver scan: %d markets, %d searches", len(markets), total_searches)

    for market in markets:
        terms = terms_for_market(market, cfg)
        log.info("Market %s: %d search terms", market["name"], len(terms))
        for term in terms:
            completed += 1
            log.info("[%d/%d] Searching %s for %r", completed, total_searches, market["name"], term)
            frame = scrape_one(term, market, cfg)
            if not frame.empty:
                frames.append(frame)
            time.sleep(0.8)

    if not frames:
        return []

    data = pd.concat(frames, ignore_index=True)
    data.to_csv(CSV_PATH, index=False)

    dedup: dict[str, Job] = {}
    for _, row in data.iterrows():
        title = safe_text(row.get("title"))
        priority = safe_text(row.get("_priority")) or "major"
        if not title or not title_is_relevant(title, priority, cfg):
            continue

        uid = build_uid(row)
        url = safe_text(row.get("job_url_direct")) or safe_text(row.get("job_url"))
        job = Job(
            uid=uid,
            title=title,
            company=safe_text(row.get("company")) or "Not stated",
            location=safe_text(row.get("location")) or safe_text(row.get("_market")),
            market=safe_text(row.get("_market")),
            priority=priority,
            site=safe_text(row.get("site")) or "Job board",
            date_posted=safe_text(row.get("date_posted")) or "Recently posted",
            salary=salary_text(row),
            url=url,
            description=safe_text(row.get("description"))[:700],
        )
        dedup.setdefault(uid, job)

    jobs = list(dedup.values())
    jobs.sort(key=job_sort_key)
    return jobs


def _job_card(job: Job) -> str:
    link = (
        f'<a href="{html.escape(job.url)}" style="color:#0b57d0;font-weight:700">Open vacancy</a>'
        if job.url
        else "No direct link"
    )
    return f"""
    <div style="border:1px solid #ddd;border-radius:10px;padding:16px;margin:0 0 14px 0;font-family:Arial,sans-serif;background:#fff">
      <div style="font-size:18px;font-weight:700">{html.escape(job.title)}</div>
      <div style="margin-top:5px"><strong>{html.escape(job.company)}</strong> · {html.escape(job.location)}</div>
      <div style="margin-top:5px;color:#555">Search area: {html.escape(job.market)} · Source: {html.escape(job.site)} · Posted: {html.escape(job.date_posted)}</div>
      <div style="margin-top:5px">Salary: {html.escape(job.salary)}</div>
      <div style="margin-top:10px">{link}</div>
    </div>
    """


def make_html(jobs: list[Job]) -> str:
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    chauffeur_jobs = [job for job in jobs if is_chauffeur_job(job)]
    doncaster_jobs = [job for job in jobs if is_doncaster_local_job(job)]
    additional_jobs = [job for job in jobs if not is_chauffeur_job(job) and not is_doncaster_local_job(job)]

    sections: list[str] = []
    sections.append(
        f'<h2 style="font-family:Arial,sans-serif;margin-top:26px">🚘 UK Chauffeur Jobs — {len(chauffeur_jobs)} new jobs</h2>'
    )
    sections.append(
        '<p style="font-family:Arial,sans-serif;color:#555;margin-top:-4px">UK-wide coverage: a national search plus a grid of major cities across England, Scotland, Wales and Northern Ireland.</p>'
    )
    if chauffeur_jobs:
        sections.extend(_job_card(job) for job in chauffeur_jobs)
    else:
        sections.append('<p style="font-family:Arial,sans-serif;color:#666">No new chauffeur-titled vacancies found in this run.</p>')

    sections.append(
        f'<h2 style="font-family:Arial,sans-serif;margin-top:34px">📍 Doncaster Area Driving Jobs Worth a Look — {len(doncaster_jobs)} new jobs</h2>'
    )
    sections.append(
        '<p style="font-family:Arial,sans-serif;color:#555;margin-top:-4px">Broader driving vacancies within roughly 30 miles of Doncaster. Delivery, courier, HGV/LGV, taxi, bus/coach and similar low-relevance roles are filtered out.</p>'
    )
    if doncaster_jobs:
        sections.extend(_job_card(job) for job in doncaster_jobs)
    else:
        sections.append('<p style="font-family:Arial,sans-serif;color:#666">No new Doncaster-area driving vacancies passed the quality filters in this run.</p>')

    sections.append(
        f'<h2 style="font-family:Arial,sans-serif;margin-top:34px">➕ Other UK Private / Executive / Security Driver Roles — {len(additional_jobs)} new jobs</h2>'
    )
    sections.append(
        '<p style="font-family:Arial,sans-serif;color:#555;margin-top:-4px">Private driver, executive driver, VIP driver, security driver and closely related UK roles.</p>'
    )
    if additional_jobs:
        sections.extend(_job_card(job) for job in additional_jobs)
    else:
        sections.append('<p style="font-family:Arial,sans-serif;color:#666">No additional matching UK premium-driver roles in this run.</p>')

    return f"""
    <html><body style="max-width:860px;margin:auto;padding:22px;background:#fafafa">
      <div style="font-family:Arial,sans-serif">
        <h1 style="margin-bottom:4px">UK Chauffeur + Doncaster Driving Jobs</h1>
        <p style="color:#555;margin-top:0">{today} · {len(jobs)} new matching vacancies · chauffeur jobs first</p>
        {''.join(sections)}
        <p style="font-size:12px;color:#777;margin-top:30px">Automated search from public job boards. Always confirm that a vacancy is still open before applying.</p>
      </div>
    </body></html>
    """


def make_text(jobs: list[Job]) -> str:
    chauffeur_jobs = [job for job in jobs if is_chauffeur_job(job)]
    doncaster_jobs = [job for job in jobs if is_doncaster_local_job(job)]
    additional_jobs = [job for job in jobs if not is_chauffeur_job(job) and not is_doncaster_local_job(job)]

    lines = [
        f"UK Chauffeur + Doncaster Driving Jobs — {len(jobs)} new matching vacancies",
        "UK chauffeur vacancies are listed first, followed by Doncaster-area driving jobs worth a look, then other UK premium driver roles.",
        "",
        f"UK CHAUFFEUR JOBS — {len(chauffeur_jobs)} new jobs",
        "",
    ]

    for i, job in enumerate(chauffeur_jobs, 1):
        lines += [
            f"{i}. {job.title}",
            f"   {job.company} — {job.location} ({job.market})",
            f"   {job.site} | {job.date_posted} | {job.salary}",
            f"   {job.url}",
            "",
        ]

    lines += ["", f"DONCASTER AREA DRIVING JOBS WORTH A LOOK — {len(doncaster_jobs)} new jobs", ""]
    for i, job in enumerate(doncaster_jobs, 1):
        lines += [
            f"{i}. {job.title}",
            f"   {job.company} — {job.location}",
            f"   {job.site} | {job.date_posted} | {job.salary}",
            f"   {job.url}",
            "",
        ]

    lines += ["", f"OTHER UK PRIVATE / EXECUTIVE / SECURITY DRIVER ROLES — {len(additional_jobs)} new jobs", ""]
    for i, job in enumerate(additional_jobs, 1):
        lines += [
            f"{i}. {job.title}",
            f"   {job.company} — {job.location} ({job.market})",
            f"   {job.site} | {job.date_posted} | {job.salary}",
            f"   {job.url}",
            "",
        ]

    return "\n".join(lines)


def send_email(jobs: list[Job], cfg: dict[str, Any]) -> None:
    # smtp_runner.py replaces this function with the Hostinger SMTP + Telegram sender.
    raise RuntimeError("Run smtp_runner.py so the configured notification transport is used")


def main() -> int:
    cfg = load_config()
    seen = load_seen()
    jobs = collect_jobs(cfg)
    log.info("Collected %d relevant unique jobs", len(jobs))

    new_jobs = [job for job in jobs if job.uid not in seen]
    max_jobs = int(cfg["search"].get("max_email_jobs", 300))
    # collect_jobs is already section-sorted: chauffeur first, then Doncaster local,
    # then other UK premium driver vacancies.
    new_jobs = new_jobs[:max_jobs]

    if new_jobs:
        send_email(new_jobs, cfg)
        now = datetime.now(timezone.utc).isoformat()
        for job in new_jobs:
            seen[job.uid] = now
        save_seen(seen)
        log.info("Sent %d new jobs", len(new_jobs))
    else:
        send_email([], cfg)
        log.info("No unseen matching jobs today; sent zero-result email")

    return 0


if __name__ == "__main__":
    sys.exit(main())
