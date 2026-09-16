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
