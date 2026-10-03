# Mac partial-soak setup-clock failure and correction

## Preserved hosted result

PR #30 head: `7718dee64bf011fd72e11e83adf0ba3220a807ff`.
[Run 37078626720](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37078626720)
had an observed failure in
[Mac runtime job 111074033757](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37078626720/job/111074033757):

- The original complete runtime/distribution file selection: **149 passed**.
- The new complete partial-soak contract: **17 passed, 1 failed** in 24.52 seconds.
- Failing case: `test_actual_two_cycle_fresh_home_soak_is_partial_and_private`.
- The runner returned `FAILED / no_complete_observed_cycle` after **4.141 seconds**
  for the requested four-second window, with **zero** cycles/health observations
  and cleanup `not_needed`. No service had started.
- The runner recorded checkout `9ae78b6ccc8bb595cf9c0828bda5e3b9edfe93f8`,
  the PR merge checkout, not the PR head above; harness digest
  `4b640ff4837d5b96d9379bfcac52d6e2936abc7096e562c661f4827bdad9199c`.
- Other selected jobs passing cannot override this failure. No unchanged-head
  retry, skipped case or relaxed original CI gate was used.

The four-second clock started before Git identity, interpreter hashing, synthetic
queue preparation and baseline admission. Preparation consumed the window before
its first service launch. The refusal itself was correct; the runner must not
call this zero-observation result success or treat preparation as soak evidence.

## Bounded correction and proof

Preparation now has separate monotonic duration and UTC start fields. The actual
observation clock starts only after the fixed paused queue and independent
baseline are ready. Setup failure retains zero observed duration. Continuous
24-hour evidence can never include preparation or a previous run.

A deterministic regression introduces a **real five-second** preparation delay,
then requires an additional real three-second observation window with actual
service/health evidence. Restoring the original `run` function only in a negative
control test process reproduces `FAILED / no_complete_observed_cycle`, zero
cycles and **5.047 seconds** incorrectly consumed by setup. The corrected test
passes and checks that preparation is excluded from observation.

The original positive smoke now observes **ten real seconds**, retaining its
at-least-two-cycle, privacy, source/identity and byte-accounting assertions.
The existing too-short/no-observation refusal and every prior canary remain.
No production queue/service code, original CI selector or runner/job budget
changed.

Local correction validation: **19 runner + 17 existing lifecycle tests passed**
in 32.42 seconds; **18 existing engineering-closure guards passed**; syntax and
whitespace passed. Corrected-head hosted Linux/Mac results remain pending. The
600-second earlier rehearsal retains its separate earlier digest and does not
validate this correction. Full Z-04, 24h, native-device, live-site and consumer
certification remain unverified.
