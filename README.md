# Chauffeur Job Alerts

A GitHub Actions job finder that runs every day, searches major job boards for chauffeur/private-driver roles, removes duplicates and emails only newly discovered vacancies.

## What it searches

- Indeed
- LinkedIn
- Glassdoor
- Google Jobs
- ZipRecruiter

Default terms include chauffeur, private chauffeur, family chauffeur, private driver, personal driver, executive driver, VIP driver and security driver.

The United Kingdom gets the broadest search. Additional searches cover the UAE, Hong Kong, Singapore, Switzerland, Saudi Arabia, Qatar, Australia, Canada and the United States.

## Daily schedule

The workflow runs at **07:00 UTC every day** and can also be run manually from GitHub Actions.

## One-time email setup

The repository never stores your email password in the code. Add these under:

**GitHub repository → Settings → Secrets and variables → Actions → Secrets**

1. `SMTP_USERNAME` — your Gmail address.
2. `SMTP_PASSWORD` — a Google **App Password**, not your normal Gmail password.

Required variable under **Variables**:

- `EMAIL_TO` — one or more recipient email addresses, separated by commas. This is required for the public repository. Put the recipient addresses in this private GitHub Actions variable rather than committing them to `config.yaml`.

For Gmail App Passwords, your Google account normally needs 2-Step Verification enabled. Generate the app password in your Google Account security settings and paste it directly into the GitHub secret. Do not send the app password in chat and do not commit it to the repository.

## Run it manually

Open **Actions → Daily chauffeur job search → Run workflow**.

The first run can return more jobs because the history is initially empty. After that, `data/seen_jobs.json` tracks vacancies already emailed to you.

## Change countries or keywords

Edit `config.yaml`. You can add/remove search terms, countries, job boards, the age of vacancies, or the maximum number of jobs included in each email.

## Notes

Public job boards can change their anti-bot protections at any time, so an individual source may occasionally fail while the other sources continue. The workflow logs each failed source/search rather than stopping the entire daily run.

## Privacy

The repository is currently public. Recipient addresses and SMTP credentials are intentionally not committed. Store recipients in the `EMAIL_TO` Actions variable and credentials only in Actions secrets.
