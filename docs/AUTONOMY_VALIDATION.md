# Local Autonomy v1 — validation record

Date: 2026-09-21. Implementation branch: `codex/local-autonomy-v1`.

The local queue, worker, OTP broker and supervisor API extend the existing
executor and Playwright adapters. Both generic and legacy final-submit methods
refuse automated submission. Runtime storage is ignored by Git and permissioned
0700/0600.

Validation completed:

- Baseline: 84 existing tests passed in 30.34 seconds. The first sandboxed run
  could not launch Chrome; the same isolated headless suite passed with the
  necessary execution permission. Browser startup now releases Playwright after
  a launch failure, preventing cascading event-loop failures.
- Final full suite: **116 passed in 40.81 seconds**. Existing tests were preserved.
- Synthetic subprocess E2E: authenticated HTTP enqueue, ordinary OTP ingestion,
  required factual input, automatic resume, final review, restart recovery.
  The fake site's final submission request count remained **zero**.
- Queue concurrency, stale ownership fencing, paused-owner recovery, duplicate
  suppression, bounded retries and cancellation passed.
- OTP extraction, expiry, single use, replay rejection, ambiguous matching and
  no-value persistence passed. Real DOM security challenge checks passed.
- API auth, localhost-only binding, browser-origin rejection, no final-submit
  route, and generic error responses passed.
- `python -m compileall -q executor tests`, `python -m pip check`, and
  `git diff --check` passed.
- Changed/new text was scanned against canonical private applicant identifiers,
  existing private credential values, private-key patterns and token patterns:
  zero matches. Runtime token and queue files remain Git-ignored.
- Background start/restart/health succeeded. The production supervisor is
  listening on `127.0.0.1:9344`, idle with an empty queue. Runtime file permissions
  were verified.

Southern Fund recovery was stopped when the user confirmed the application was
already completed. Its exact position was added to the existing private
protected-target registry; no Southern Fund task is queued. No applicant fields
were modified or final submission performed during the read-only recovery check.
The completion status comes from the user's confirmation, not a newly verified
portal receipt.

Application-specific answers and OTPs are intentionally memory-only. Resupply
transient answers after a process restart, or use the existing explicit canonical
profile workflow for durable facts. The background service is reversible and
must be started again after logout/reboot; no login item was installed.

Usage: [Local Autonomy v1](LOCAL_AUTONOMY.md).

## Final closeout audit

The closeout request expected 10 changed files. The actual existing implementation
contains **16 files: 7 modified and 9 new**. Directory-collapsed Git output must
not be treated as an individual-file count. No feature or implementation changes
were made during this closeout; only this validation record was updated.

Fresh verification on 2026-09-21:

- Full suite: **116 passed in 41.66 seconds**.
- Separate synthetic daemon E2E and API/CLI/queue submit-boundary checks:
  **5 passed in 8.54 seconds**. The fake task completed enqueue, claim, OTP/factual
  blockers, automatic resume and READY_TO_SUBMIT. Its status survived a process
  restart. The fake site's final submission counter remained zero; synthetic OTP
  and answer canaries were absent from runtime artifacts and logs.
- `compileall`, dependency consistency and `git diff --check`: passed.
- Real applicant identifiers and private credential values: zero matches across
  the 16 candidate files. Private-key/token-pattern scan: zero matches. No real
  profile, cookie store or runtime state is tracked. Fixed fake OTPs remain test
  fixtures, not real authentication data.
- Production daemon restarted once, with a changed PID and authenticated healthy
  response at 127.0.0.1:9344. Queue and events were empty before and after restart
  and remained empty after synthetic verification.
- All 3 existing CDP targets retained the same hashed identities and URLs; there
  were zero pytest tabs. Private application artifacts and protected-target config
  retained their hashes. No real application was enqueued or executed.
- Southern Fund remains protected from mutation using the user's previously
  confirmed completion. This closeout did not reopen or interact with its form.

Exact candidate file inventory:

- `.gitignore`
- `README.md`
- `docs/AUTONOMY_VALIDATION.md`
- `docs/LOCAL_AUTONOMY.md`
- `executor/adapters/generic_web.py`
- `executor/application.py`
- `executor/autonomy/__init__.py`
- `executor/autonomy/cli.py`
- `executor/autonomy/otp.py`
- `executor/autonomy/queue.py`
- `executor/autonomy/supervisor.py`
- `executor/autonomy/worker.py`
- `executor/browser.py`
- `executor/field_classifier.py`
- `executor/models.py`
- `tests/test_autonomy_v1.py`
