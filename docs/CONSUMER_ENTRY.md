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

## Local preparation checklist

The engineering candidate provides **查看本地准备清单** on a task card. This is
an explicit local read, not permission to execute or submit that task.

1. Open the checklist for the exact task you want to review. It uses that task's
   original profile reference, even if you selected another profile for future
   tasks.
2. Check the profile and resume's last observed local versions. A field marked
   **本机已记录** only means a local value exists; it does not prove correctness,
   eligibility, website acceptance or a saved application draft.
3. Review missing records and manual steps. The dated Qiyunfang campus-form
   checklist appears only for its exact official source and the observed
   **应用实施工程师（武汉）** role. It is a cached observation, not a fresh scan of
   the website or a complete certification of required fields. A second role
   is not selected or inferred; confirm on the official form whether it is
   needed.
4. Use **刷新检查** to read local state again. Closing the dialog, switching tasks,
   refreshing the app or restarting the service clears the old observation.
   The task and its history remain unchanged.

The checklist never displays personal field values, identity numbers, file
paths or file contents, and does not copy them to the clipboard or send them to
a model or website. A matching resume digest proves only a local file version,
not upload, format acceptance, content quality or server persistence. Identity
entry, CAPTCHA, privacy decisions, uploads and final submission remain manual.
The fixed official source link is never opened automatically. The native Mac
window intentionally blocks external navigation; if its link does not open,
use the selectable public URL shown in the checklist in your own browser. The
application does not copy that address or any private data to the clipboard.

Other targets still show local profile/resume availability and honest capability
limits. Viewing or refreshing this report does not change the task's stage,
authorization, account verification, server-draft verification or final-review
certificate. It cannot turn an unsupported target into `READY_TO_SUBMIT`.

Adding the same target again does not overwrite its original task, profile or
authorization. If the requested binding differs, the app explicitly says the
existing task was retained and offers **查看已有任务**. That button only selects
the original card for review; it does not resume, retry, rebind or authorize it.
The newly typed form remains available for review instead of being silently
cleared. New unsupported tasks retain submitted location, campaign and
employment-type details, but those fields do not make the target verified.

## Safety boundary

Consumer Entry v1 changes only startup and presentation. DeepSeek still receives
only the existing redacted manager context. OTP and phone handling remain local.
CAPTCHA/password boundaries remain human-handled. The executor still has no
automated final-submit capability; `READY_TO_SUBMIT` requires the user's final
click.
