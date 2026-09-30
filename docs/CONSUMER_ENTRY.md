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

## Edit future-task profile and resume

Open **资料设置 → 编辑基本资料与简历** to review the allowlisted basic
fields in the authenticated local window. The main promise is:
**仅用于以后新任务，已有任务仍使用原资料**.

- Editing or choosing a file does not save automatically. **保存给以后新任务**
  creates a new private profile version, then switches the future-task selection
  only after checking the source profile and settings versions. A successful
  message requires a second local read of the new versions.
- Only changed fields are patched. Unchanged metadata, evidence and fields
  outside this editor (including an existing identity-number field) are
  preserved but not displayed here. Emptying a supported field explicitly
  clears its value. Unsupported formats are read-only. Existing repeated
  education records make the highest-education fields read-only to avoid
  contradictory flat and row values; this editor does not rebuild those rows.
- A selected PDF, DOCX or DOC is copied as opaque bytes to a random private
  local filename. The total request, including metadata and framing, is capped
  at 20 MiB. Extensions and MIME types are advisory, not proof of file safety
  or content. The app does not preview, parse, execute or upload the file.
  Replacing the resume does not rebuild old extracted facts; review them before
  using a future task. Prior profile and asset versions remain intact.
- A busy or changed source is rejected rather than overwritten. If a save
  might have reached disk but cannot be confirmed, use **重新读取并核对**.
  The app does not replay the save; uncertain future-task admission remains
  fenced until a safe local read can reconcile it. Existing task controls are
  not turned into new application permission.
- Closing the editor, pressing Escape, refreshing the page or expiring the UI
  session clears its private inputs and pending file selection. Unsent edits
  are not stored in browser local/session storage. Reopen to read the current
  local version; an earlier delayed response cannot restore a dismissed form.

Legacy profiles without the canonical `fields` structure remain import-only;
this editor does not silently migrate them. The existing JSON import remains
available, and a pending JSON selection must be cleared before switching to
field editing. This is an engineering-candidate local preparation feature, not
proof of a supported live application driver, website persistence or permission
to submit. No ID, CAPTCHA, privacy agreement, live upload or final-submit action
is added by this editor.

## Safety boundary

Consumer Entry v1 changes only startup and presentation. DeepSeek still receives
only the existing redacted manager context. OTP and phone handling remain local.
CAPTCHA/password boundaries remain human-handled. The executor still has no
automated final-submit capability; `READY_TO_SUBMIT` requires the user's final
click.
