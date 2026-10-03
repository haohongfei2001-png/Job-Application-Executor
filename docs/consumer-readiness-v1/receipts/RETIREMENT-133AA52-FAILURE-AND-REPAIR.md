# Service-registry retirement race: observed failure and repair candidate

## Preserved hosted failure

Candidate: `133aa524e128a1ce13c663234a08ee6f9f9c3daf`, PR #29.
The earlier Draft [run 37065657353](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37065657353)
passed all ten selected jobs; its full Linux/build lanes were skipped.
The later ready-for-review [run 37068070773](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37068070773)
completed **FAILURE**, with seven selected jobs successful and the Mac
consumer-transactions job failed; it must not be called green:

- [Job 111040720443](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37068070773/job/111040720443):
  **1 failed, 625 passed** in 647.23 seconds, completed 2026-10-02 21:50:29 UTC.
- Failing case:
  `test_actual_isolated_service_retires_without_pid_signals_or_private_replay[False]`.
- At the authenticated retirement assertion, `cli.lifecycle("stop", root, port)`
  returned `{"ok": false, "reason": "service_identity_unverified"}` rather than
  the expected `{"ok": true, "running": false}`.
- The failed candidate changed only documentation, so its runtime and owning
  tests are identical to the prior tested main `9fc2202e`. That fact does not
  dismiss the failure or make this candidate pass. No unchanged-head rerun was
  requested to replace the failed evidence.

## Reproduced matching race

After an authenticated service-stop acknowledgement, the retiring service removes
its owned registry. The caller concurrently polls that registry. If removal
occurs after `os.open` but before `os.fstat`, the caller holds a real descriptor
whose link count has become zero. The old reader treats that count as ambiguous
identity, even when the registry pathname is genuinely absent. This reproduces
the same `service_identity_unverified` result during an otherwise acknowledged
retirement.

The hosted log has no syscall-level trace, so it does not uniquely establish
that this was the failed job's exact interleaving. The deterministic experiment
proves a matching production-code race; it is not a claim that the hosted failure
was harmless or merely flaky.

The bounded repair checks the zero-link descriptor's pathname without following
symlinks. Actual absence follows the existing missing-record path: before the
authenticated stop it still refuses; after the acknowledgement, the existing
absence recheck can confirm retirement. Any replacement, same-content new inode,
symlink, dangling symlink or hardlink remains refused. No PID signal, process
inspection, additional HTTP stop, replay, permission change or retry is added.

## Deterministic evidence and outstanding gate

Eighteen appended owning regressions use actual unlink/inodes at each of three
registry reads: before identity, before stop, and after acknowledgement. Each
tests absence plus five replacement/alias forms, exact HTTP call counts and
unchanged full synthetic private task/answer/key authority and file inventory.
All prior owning tests and fixtures remain a byte-for-byte prefix.

Local Linux checks on 2026-10-02:

- With the original `_service_record` restored only in the test process:
  **3 failed, 15 passed**. All three real-absence timings reproduce the original
  classification; the post-ack case reproduces the hosted assertion mismatch.
- With the repair: **18 passed**.
- Existing retirement/legacy-writer checks plus new cases:
  **71 passed, 425 deselected**; existing service-lifecycle file: **17 passed**.
- Existing engineering-closure routing guards: **18 passed**.
- A complete local consumer/compatibility attempt was **553 passed, 91 failed**
  in 75.99 seconds, not a full pass. Browser failures report the missing pinned
  Playwright headless-shell executable. Packaged-install tests fail candidate
  startup; an independent reproduction of their copied interpreter exits before
  application code with `ModuleNotFoundError: No module named 'encodings'`,
  because this cloud interpreter's relocated stdlib prefix is unavailable.
  Original-environment dependency validation succeeds. The hosted runner has the
  required setup; its new-head result remains necessary, not assumed.

These local checks do not replace the complete owning suites or exact new-head
Linux and hosted Mac gates. Those gates remain required before merge. The
fixed-version delivery receipt for `9fc2202e` remains a historical successful
run, not evidence for this new runtime repair. Consumer certification, real-user
Mac/site acceptance, signing/notarization, 24-hour soak and five real usage days
remain unverified; final submission remains human-only.

The failed candidate's full Linux job separately completed **3350 passed,
93 skipped, 2 xpassed, 132 subtests passed** in 2413.05 seconds. This preserves
the passing scope without overriding the Mac failure or certifying the repair.

## Corrected-head hosted closure, 2026-10-02

The narrow repair head `1c3aa80541256a15865edc6db56ec0df23103807` passed
[complete run 37074833221](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37074833221):
all eight selected jobs succeeded. Full Linux: **3368 passed, 93 skipped,
2 xpassed, 132 subtests passed** in 2062.88 seconds. Hosted Mac consumer:
**644 passed** in 607.66 seconds, including all 18 appended real-unlink cases.
The earlier failed run and local environment limitations above remain preserved.

PR #29 merged as `4954eb2fe80c84456e22e386483f7ed877e1a1b5`. Its normal
post-merge run **37078259613** was in progress when this follow-up was written;
that new run is not assumed passed. The repair does not promote consumer or
full Z-04 certification or establish a live/physical-device result.
