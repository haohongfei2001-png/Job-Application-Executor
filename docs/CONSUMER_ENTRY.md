# Consumer Entry v1

Consumer Entry v1 is a thin macOS launcher for the existing local-first
Application Executor. It does not replace the executor, queue, browser adapter,
OTP broker or safety gates.

## One-time installation

From the repository root:

```bash
.venv/bin/python -m executor.autonomy.cli install-app
```

This creates:

```text
~/Applications/AI 投递经理.app
```

The app contains no applicant facts, API keys, OTPs or browser credentials. It
only stores the absolute path to this local repository so it can invoke the
existing virtual environment.

After installation, the normal user entry point is the app in Finder, Spotlight
or the Dock. Terminal commands are no longer required for ordinary startup.

## What double-click does

The launcher runs the equivalent of:

```bash
.venv/bin/python -m executor.autonomy.cli launch
```

The launch command:

1. refuses test/isolated browser mode;
2. reuses or starts the dedicated Chrome profile and local CDP endpoint;
3. reuses or starts the localhost supervisor;
4. runs the existing fail-closed live preflight;
5. opens the authenticated local manager UI only after readiness passes.

It does not create an application task, choose a job, fill a form, send an OTP,
or click any submit control merely because the app was opened.

## Failure behavior

Reversible startup prerequisites such as Chrome and the local supervisor are
started automatically. If a non-reversible prerequisite is missing, launch fails
closed and the app shows a short local macOS warning. Detailed output is written
only to:

```text
~/Library/Logs/AI投递经理/launcher.log
```

Typical causes are an unavailable canonical profile or DeepSeek credential. An
existing task remains durable in the local queue.

## User-facing status

The dashboard translates internal states into user-facing language, for example:

- `NEEDS_USER_INPUT` → 需要你回答
- `NEEDS_USER_ACTION` → 需要你操作
- `otp_waiting` → 正在等待验证码
- `security_challenge` → 需要安全验证
- `READY_TO_SUBMIT` → 等你最终确认

The underlying state values remain unchanged for audit and recovery.

## Safety boundary

Consumer Entry v1 changes only startup and presentation. DeepSeek still receives
only the existing redacted manager context. OTP and phone handling remain local.
CAPTCHA/password boundaries remain human-handled. The executor still has no
automated final-submit capability; `READY_TO_SUBMIT` requires the user's final
click.
