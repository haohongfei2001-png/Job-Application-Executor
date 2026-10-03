# Adopted db39 lifecycle observation: interrupted, not completed

## Adopted code and hosted gates

PR #30 merged as `db39a758b48be1dc110543891585641e3afca9a0`.
[Post-merge run 37086541698](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37086541698)
completed **SUCCESS** at 2026-10-03 02:21:45 UTC, with all eight selected jobs
successful. Full Linux was **3387 passed, 93 skipped, 2 xpassed, 132 subtests
passed** in 2817.46 seconds. These hosted results do not establish long-duration
soak or consumer certification; raw-CDP comparator XPASS is not guarded-backend
certification.

## Actual partial observation

- Clean adopted Git checkout: `db39a758b48be1dc110543891585641e3afca9a0`
- Frozen manifested source digest:
  `4abde5d4f28fed7d67449259296dd06c05f53209fda0912ee13eea396d98baaf`
- Harness digest:
  `8dd6c170816abc3d54338380bf553c9df6acb02bf2170353fcfec22bb221e515`
- Observation started: `2026-10-03T01:34:40.008313+00:00`
- Preparation was separately recorded as **0.336 seconds**
- Requested observation: **22,254 seconds**, intended to finish before 08:00 UTC
- Last real observation: **1,361.510 seconds**, approximately **22m42s**
- Last checkpoint write: **2026-10-03 01:57:21 UTC**
- **22 completed cycles**, **269 authenticated health observations**, 20 paused
  synthetic tasks; the last active cycle is not counted as complete
- **270** readable sequential checkpoints (`000000` through `000269`), with
  monotonic observations and maximum successive observation gap **5.999 seconds**
  inside the recorded window
- Exact cumulative checkpoint bytes: **489,269**, matching files on disk
- Last checkpoint SHA-256:
  `fd0422b55c1823fee9a91ee29b7ffb65234f7d5e5e6bdc1d8e7a4ada8cb79548`
- Current-service RSS samples: **52,760,576–56,475,648 bytes**, open descriptors
  **5–6**. This short, restarting workload is not leak-trend certification.

## Interruption evidence and limits

On reinspection at 2026-10-03 07:13–07:15 UTC, the last report still said
`RUNNING`, there was **no terminal report**, and the original execution session
was unavailable. No process matching the exact runner/workspace or its service
runtime remained. The recorded service PID was absent and the worker lock was
available. A stale service registry remained and was left untouched with the
checkpoints. No process was killed and no cleanup or restart was invented.

The interruption cause is **UNVERIFIED**. The absence of a terminal report does
not identify a signal, executor lifecycle event, crash or resource failure.
Registry/process absence observed hours later is not evidence of the original
controlled shutdown. The observation is therefore **INTERRUPTED / INCOMPLETE**,
not `COMPLETED_PARTIAL_WINDOW` and not a six-hour result. The unobserved gap is
not counted or added to another run. Original checkpoint bytes remain unchanged.

This receipt contains only value-free engineering metadata. Runtime credentials,
keys, logs, database, registry contents and applicant data are not included or
uploaded. No real website/application, user Mac, model call or submission was
part of the workload. Full Z-04, 24-hour soak, five real usage days and consumer
certification remain unverified / `NOT_CERTIFIED`.

## Read-only observability follow-up

A separate inspector now classifies this unchanged chain as
`INCOMPLETE_NO_TERMINAL / STALE`, without reading any runtime credential, registry,
log or database, probing a service, writing a checkpoint or restarting anything.
It validates owned private regular files, contiguous sequence, exact cumulative
bytes, fixed identities, monotonic progress and consistent terminal/24h claims.
Its output explicitly remains self-reported evidence, not independent execution
verification or certification.

Local validation: **43 complete owning-file cases passed**, including all 19
previous runner cases and appended incomplete/damaged-chain/privacy canaries;
existing workflow guards **18 + 2 passed**. The original runner/test prefix is
unchanged. Exact inspector-candidate hosted checks remain pending. No new long
observation was started to conceal or accumulate the missing interval.
