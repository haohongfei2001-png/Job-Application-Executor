# Local Autonomy v1

The local supervisor wraps `ApplicationExecutor`, the canonical profile resolver,
existing site adapters and Playwright browser runtime. It does not own or close
shared Chrome. The adapter's `submit()` and legacy final-submit helper both
raise unconditionally. No HTTP or CLI submit operation exists.

## Run locally

```sh
.venv/bin/python -m executor.autonomy.cli start
.venv/bin/python -m executor.autonomy.cli status
.venv/bin/python -m executor.autonomy.cli health
.venv/bin/python -m executor.autonomy.cli restart
.venv/bin/python -m executor.autonomy.cli stop
# Foreground debug (same exclusive worker lock)
.venv/bin/python -m executor.autonomy.cli serve
```

The reversible background process runs until stopped or logout/reboot. It does
not install a login item or change macOS settings. Run `start` after reboot;
SQLite tasks and checkpoints survive. SIGTERM requests a stop at the next safe
operation. An interrupted task becomes reclaimable after its 60-second lease
expires. `stop` reports a pending safe stop if an in-flight browser operation
needs longer; it never kills Chrome. `--runtime PATH --port PORT` precede the
command. The worker process lock is global to this repository even when queues
use different runtime directories. One live browser worker is supported.

Runtime state is under gitignored `runtime/autonomy/` (directory mode 0700;
files 0600): SQLite queue/events, private auth token, service metadata, redacted
reviews and service diagnostics. SQLite transactions atomically claim tasks;
random ownership tokens fence stale workers. Heartbeats renew leases every
10 seconds. Failed attempts retry with bounded backoff (maximum 3 by default,
configurable 1–5). Crashed attempts count against the same budget. Human input
pauses do not consume the failure budget. Cancellation prevents subsequent
operations, while a browser operation already in flight may finish.

## Task contract

```json
{
  "company": "Synthetic Company",
  "role": "Test Engineer",
  "target_url": "https://jobs.example.test/apply?postId=fake-position",
  "job_id": "fake-position",
  "campaign": "synthetic-2027",
  "profile_ref": "/absolute/private/fake-profile.json",
  "attachment_refs": {},
  "live_authorized": false,
  "max_attempts": 3
}
```

Enqueue a local JSON file with `enqueue --file PATH`. Use `tasks`, `get TASK_ID`,
`events`, `resume TASK_ID`, and `cancel TASK_ID` for supervision. Duplicate exact
targets return the original task regardless of display labels/profile changes.
Job identifiers are checked against the URL. Protected submitted targets are
refused at enqueue, resume, and immediately before execution.

Only references to applicant profiles/assets are persisted. Task rows contain
operational identity, stage, safe checkpoint, attempts, owner/expiry, blocker
codes, unresolved field keys, review reference and timestamps. Do not put
applicant facts in company/role/campaign metadata. URLs with credentials or
non-routing query parameters are rejected. SQLite never contains field answers,
passwords, cookies, auth tokens or OTPs.

States follow the existing sequence:
`DISCOVERED → PROFILE_RESOLVED → FORM_FILLED → VALIDATED → READY_TO_SUBMIT`.
Failures pause at `NEEDS_USER_INPUT`, `NEEDS_USER_ACTION`, `BLOCKED` or `ERROR`.
`CANCELLED` cannot be resumed. `READY_TO_SUBMIT`, `SUBMITTED`, and `VERIFIED`
cannot be resumed by the queue. Existing submitted/verified executions remain
read-only; the worker cannot manufacture these states.

Recovery reopens the last safe same-origin URL when available, re-resolves
current canonical facts and reads back the current form;
masked audit values are never replayed as applicant facts. Application-specific
answers are accepted only for pending field keys and held in memory. Submit an
`answers` mapping through stdin to `user-input TASK_ID`; this resumes immediately.
After a daemon restart, re-supply transient answers or update the canonical
private profile through the existing explicit profile workflow, then resume.

## Local API and Codex/Work handoff

Every route, including health, requires `Authorization: Bearer <local token>`.
The token comes from `APPLICATION_EXECUTOR_LOCAL_TOKEN` (minimum 32 characters)
or generated private `runtime/autonomy/auth.token`. Never print or commit it.
The server binds only `127.0.0.1:9344`, verifies the Host header and rejects
browser Origin headers. There is no CORS/public endpoint or arbitrary shell.

| Method | Path | Payload |
|---|---|---|
| GET | `/health` | Worker health |
| GET / POST | `/v1/tasks` | List / task contract above |
| GET | `/v1/tasks/{id}` | Status and pending keys |
| POST | `/v1/tasks/{id}/resume` | `{}` |
| POST | `/v1/tasks/{id}/cancel` | `{}` |
| POST | `/v1/tasks/{id}/user-input` | `{"answers":{"pending.key":"confirmed fact"}}` |
| POST | `/v1/otp` | `{"task_id":"...","message":"received message"}` |
| GET | `/v1/events?after=0` | Up to 200 ordered events |

Codex/Work writes one exact-target task, enqueues it, then checks task/events.
Only a task with `live_authorized:true` can attach to the existing live CDP
session. If 9333 is unavailable the daemon pauses; it does not launch a fresh
profile. Tests always select the existing isolated, temporary headless browser.
Final review is `runtime/autonomy/reviews/{task_id}/review.json`; the live form
holds the full applicant values for the user's final review.

## OTP and human boundaries

Ordinary user-received Chinese/English 4–8 digit codes may be entered in the
authenticated local task card, or through `otp push --task TASK_ID --attempt
AUTH_ATTEMPT_ID` with the message on stdin. Do not pass OTPs in command arguments,
chat, or shell history. A local Shortcut may POST to `/v1/otp` only with the
current task and attempt ID. An existing, explicitly enabled private iPhone
bridge may be reused; no new cloud relay is created.

Codes are in memory, expire after 300 seconds, and are consumed once.
Duplicate delivery is rejected for the remainder of the validity window. Both
multiple plausible codes and multiple waiting tasks are rejected. A code must
match the current task, authentication attempt, site and deadline. Expired codes,
old-attempt codes and cancelled-task codes are discarded. A local Messages source
requires an explicit per-site sender and body rule; the optional relay response
must return the matching attempt and origin. Source configuration is not treated
as proof that delivery is online.

Before entering the broker wait, the live generic adapter may prepare one proven
SMS-login request: one OTP field, one phone field and one initial send-code control
inside the same explicit auth context. It fills the canonical phone locally,
never exposes it to DeepSeek/audit, never auto-resends, and only checks standard
auth/privacy terms under the existing user-confirmed privacy policy (or if the
user already checked them). QR/face alternatives may coexist visually; they are
not automated. A CAPTCHA/password/ambiguous control after the request stops the
task before the broker is consumed. The task card can explicitly authorize one
resend after the cooldown; a durable unknown-effect marker is committed before
each click, and a crash never silently clicks again.

Ingestion resumes the waiting task. A configured source may keep listening after
the short browser wait without holding a worker lease. A code is only filled into the adapter's
unique ordinary OTP field. Existing scoped authentication-confirmation rules
still apply. CAPTCHA, slider/image challenges, QR login, face/hardware prompts
and passwords remain `NEEDS_USER_ACTION`. Unknown facts, contradictory facts, salary choices
and unsupported family/compliance answers remain `NEEDS_USER_INPUT`. Canonical
standing privacy/truthfulness consents can be handled automatically. Final
submission/update confirmation always requires the user to click personally.
`submit_authorized` never overrides this boundary.

## Synthetic end-to-end and troubleshooting

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/test_autonomy_v1.py
.venv/bin/python -m compileall -q executor tests
git diff --check
```

The synthetic tests include a temporary `file://` form and a real supervisor
subprocess communicating with a local fake recruitment site. They cover OTP
ingestion through HTTP, a required unknown fact, a memory-only answer, the final
review and process restart. The fake site counts final submission requests and
asserts zero. Isolated subprocesses use their own lock and disable the real OTP
relay. No live profile or 9333 is used.

`session_unavailable`: restore the existing dedicated Chrome session and resume.
`live_not_authorized`: the task did not include exact-target live authorization;
do not change targets to work around that pause. `security_challenge`: complete
the displayed security control personally, then resume. `unknown_facts`: supply
all pending keys together. `retry_exhausted`: inspect the safe status and repair
the underlying issue; automatic retries stop. No error responses echo request
bodies or browser exception text. A service startup failure may indicate the
exclusive worker lock is held, a port conflict, or unsafe auth-token permissions.


## DeepSeek manager dashboard

The DeepSeek Application Agent adds a user-facing chat/control layer on top of
this daemon. It does not replace the queue, OTP broker, protected-target checks
or manual final-submit gate.

Open the local panel with:

    .venv/bin/python -m executor.autonomy.cli ui

Send one chat turn from stdin with:

    printf '%s' '继续这个岗位' | .venv/bin/python -m executor.autonomy.cli chat

Task-level pause is available through the CLI/API and stores BLOCKED with the
private blocker user_paused while preserving the last safe checkpoint. Resume
returns to that checkpoint. See docs/DEEPSEEK_APPLICATION_AGENT.md for the
manager decision schema, UI session security and privacy boundaries.
