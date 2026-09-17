# Job Application Executor

Local-first application form executor. It opens a dedicated Chrome profile, fills deterministic fields from a local candidate profile, uploads a local resume, advances safe multi-page forms, and hard-stops before final submission.

## Safety boundary
- Never auto-clicks final Submit / 提交 / 投递 controls.
- Passwords and OTPs are forbidden in candidate-profile JSON.
- Salary, relocation, visa/work authorization, EEO/demographic, legal declarations, signatures and similar decision fields are surfaced for user input.
- CAPTCHA / QR / Touch ID / human verification require the user.

## Run
`./scripts/run.sh '<job-url>' '/absolute/resume.pdf' '/absolute/candidate-profile.json'`

Dedicated Chrome profile: `~/Job-Application-Executor/chrome-profile`, CDP port `9333`.

## Schneider Electric / BOSS Campus adapter

Schneider 2027 campus applications on BOSS use a dedicated API adapter instead of generic DOM filling. The browser is used only to establish the authenticated BOSS session and anti-request token; job/schema/dictionaries/resume data are then read through the site's own APIs and draft changes use `saveResumeData` only.

Commands:
- `python -m executor.cli schneider-probe` — read-only login/job/draft check.
- `python -m executor.cli schneider-plan` — read-only deterministic fill plan, unresolved decisions, and local asset validation.
- `python -m executor.cli schneider-save-safe` — save deterministic profile fields to the draft; never submit.
- `python -m executor.cli schneider-upload-assets --confirm UPLOAD_ASSETS` — upload the configured photo/PDF to the draft; never submit.
- `python -m executor.cli schneider-complete --decisions <local-json>` — apply explicitly reviewed city/salary/compliance answers; never submit.

The final application submission remains outside the adapter and still requires the executor's explicit final-submit confirmation barrier. Local Schneider profile/decision files are gitignored.
