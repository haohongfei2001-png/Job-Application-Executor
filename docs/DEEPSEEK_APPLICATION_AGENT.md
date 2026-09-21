# DeepSeek Application Agent v1

This layer turns Local Autonomy v1 into a user-facing AI application manager.

Architecture:

    User
      ↓
    Local dashboard + chat
      ↓
    DeepSeek Application Manager
      ↓
    typed decisions + deterministic policy gate
      ↓
    Local Autonomy / Application Executor
      ↓
    existing Chrome CDP session
      ↓
    recruiting site

The user talks to one manager. Queueing, checkpoints, OTP handling, browser adapters, protected targets and audit remain internal infrastructure.

## Allowed manager decisions

The DeepSeek manager may propose only REPORT, RESUME, CANCEL, ANSWER_PENDING, or CREATE_TASK. It never receives arbitrary shell, JavaScript, CDP, filesystem, or final-submit capabilities.

Deterministic policy rules:

- CANCEL requires explicit cancel/stop intent in the current user message.
- RESUME requires explicit continue/resume intent.
- ANSWER_PENDING is accepted only for a currently pending field and only when the proposed value is explicitly present in the current user message.
- CREATE_TASK requires explicit application intent and the exact target URL in the current user message.
- READY_TO_SUBMIT can never be resumed by the manager.
- no manager/API/CLI route can perform final submission.

DeepSeek can interpret meaning and choose among safe tools; it cannot invent applicant facts.

## DeepSeek configuration

The manager reuses the existing executor DeepSeek configuration and macOS Keychain lookup. No second API key is created or stored. The existing private settings file provides profile_path plus the deepseek configuration already documented in config/settings.example.json.

The manager sends only a redacted task view to DeepSeek: task id, company, role, target hostname, job id/campaign, stage/checkpoint/blocker, unresolved canonical keys and attempt count. It does not send profile paths, attachment paths, cookies, OTPs, tokens, stored applicant values or browser credentials.

The current user chat message is sent to DeepSeek because natural-language control is the requested interface. Do not put passwords, OTPs, cookies or tokens in chat.

## Live field interpretation

Local Autonomy v1 previously forced DeepSeek off inside the worker. Application Agent v1 enables the existing semantic field mapper for live tasks using the same DeepSeek configuration. Synthetic, isolated and headless test modes always force DeepSeek off, so tests never call an external model or Keychain.

The field mapper receives labels/options and canonical keys, not applicant values. Deterministic source precedence still decides the actual value.

## Open the dashboard

Start the existing daemon:

    .venv/bin/python -m executor.autonomy.cli start

Open the manager UI:

    .venv/bin/python -m executor.autonomy.cli ui

The CLI requests a 60-second one-time ticket from the authenticated localhost supervisor and opens the local UI. The ticket is exchanged once for an in-memory one-hour HttpOnly SameSite session cookie and then discarded. The persistent supervisor bearer token is never inserted into HTML or JavaScript.

The dashboard shows the task list, simple status counts, DeepSeek chat, and task stage/blocker visibility. It intentionally does not expose profile paths, attachment paths or applicant PII.

## CLI and API chat

Terminal usage:

    printf '%s' '继续腾讯这个岗位' | .venv/bin/python -m executor.autonomy.cli chat

Codex/Work may call the existing authenticated localhost API:

    POST /v1/chat
    {"message":"继续这个岗位"}

No submit endpoint exists.

## Task creation

Application Agent v1 deliberately requires an exact target URL for a chat-created task. Example:

    投递 Example 的 AI 产品经理：https://jobs.example.test/apply?postId=123

Job discovery from PJSDAS/Gmail is an upstream capability and should resolve an exact target before enqueueing it.

## OTP and human boundaries

OTP stays outside DeepSeek. The raw code remains memory-only, single-use and expires under the existing broker TTL. It is never sent to DeepSeek.

CAPTCHA, slider/puzzle, image verification, QR login, face verification, hardware/security-device prompts and passwords remain NEEDS_USER_ACTION. Unknown objective facts remain NEEDS_USER_INPUT.

## Final submission boundary

The manager is not a submitter. READY_TO_SUBMIT remains the hard automated boundary. Task resume is denied at that stage and the user personally clicks the site's final irreversible control.

## Failure behavior

If the DeepSeek API is unavailable, times out, lacks balance, returns invalid JSON or fails schema validation, no proposed state change is applied. Queue/checkpoint state remains intact and the manager fails closed.

## Verification

Before merge, run:

    .venv/bin/python -m pytest -q
    .venv/bin/python -m compileall -q executor tests
    git diff --check

Manager-specific coverage includes redacted context, explicit-intent gates, pending-field/value checks, exact-URL task creation, manual-submit protection, fail-closed provider behavior, one-time UI sessions and the absence of any submit route.
