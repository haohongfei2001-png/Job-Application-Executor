# Live E2E Acceptance v1

This is the first real-machine acceptance gate for DeepSeek Application Agent v1.
It validates readiness before any live application task is created.

## 1. Run the read-only readiness gate

From the repository root:

```bash
.venv/bin/python -m executor.autonomy.cli preflight --start
```

`--start` only starts the reversible localhost supervisor if needed. It does
not create an application task, open a recruiting target, fill a form, or submit
anything.

A passing result has:

- `"ready_for_live_e2e": true`
- every item in `checks` equal to `true`
- `"final_click_actor": "user"`
- `"submit_capability": false`

The preflight output never includes the private profile path or any credential.

## 2. Open the local manager

```bash
.venv/bin/python -m executor.autonomy.cli ui
```

The UI should show the local task panel and manager chat.

## 3. Create exactly one live acceptance task

Use a real, not-yet-submitted job URL and send one explicit instruction such as:

```text
投递 Example 的 AI 产品经理：https://jobs.example.com/apply?postId=123
```

Acceptance requires the exact URL in the user's current message. Do not use a
protected/already-submitted target for this test.

## 4. Required live checkpoints

The run is accepted only if all applicable checkpoints behave as follows:

1. Existing canonical facts are filled without asking again.
2. Unknown objective facts stop at `NEEDS_USER_INPUT`.
3. OTP is handled only through the local memory-only OTP path and is never sent
   to DeepSeek.
4. CAPTCHA, slider, QR, face, password, or hardware/security prompts stop at
   `NEEDS_USER_ACTION`.
5. Pause/resume preserves the safe checkpoint and retry budget.
6. The executor reaches `READY_TO_SUBMIT` with the mandatory final review.
7. Automation stops there. The user personally performs any final submit click.

A live E2E run is not considered passed merely because the form was filled.

## 5. Failure handling

The preflight remediation codes are deliberately machine-readable:

- `use_live_browser_mode`
- `install_google_chrome`
- `start_dedicated_chrome_cdp`
- `configure_profile_path`
- `restore_profile_file`
- `repair_profile_file`
- `configure_deepseek_key`
- `start_supervisor`

Fix only the reported prerequisite and rerun preflight. Do not bypass a failed
check by changing the task or weakening the final-submit boundary.
