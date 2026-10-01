# Startup and profile-import engineering verification

- Exact tested head: `a6bff6840e7c0a0981bff48b51ab4b84939b2b67`
- [Application Executor CI 36774630094](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36774630094), attempt 1: completed **SUCCESS** on 2026-09-30
- All eight selected jobs passed; no rerun was needed

The candidate adds a 15-second monotonic startup retry budget with at-most-one-second socket-inactivity timeouts, replacing the repeated 50-by-10-second wait pattern. This is not a hard wall-time limit for arbitrary slow-drip responses or the entire application launch. Exact owned-service retirement and process-signaling boundaries are unchanged.

Profile import now validates supported canonical and recognized legacy structures before publication. It retains original facts, confirmation flags and extension metadata. It does not make incomplete profiles application-ready or change generic settings durability semantics.

## Exact-head evidence

- 12 new startup cases, including an actual stalled loopback listener, and 82 new profile-import cases ran in both Linux foundation and hosted Mac native integration
- Foundation: 25 privacy/contract cases (1 existing deselection), 2 state sequences, 358 browser/review cases, 257 operations/window/reliability cases, and 55 dashboard cases (2 existing deselections) passed
- Linux consumer/compatibility: 623 passed in 605.83 seconds; recovery: 40 passed; release/distribution: 247 passed and 16 declared platform skips
- Hosted Mac consumer/compatibility: 623 passed in 645.40 seconds; recovery: 40 passed; runtime/distribution: 149 passed
- Hosted Mac native integration: 292 passed in 1014.24 seconds, plus 7 retired-updater cases passed with 87 existing deselections
- Python compilation, dashboard JavaScript syntax and PR whitespace checks passed

Local focused checks passed before publication. Broad local packaging/browser attempts were limited by the cloud interpreter layout and unavailable pinned Chromium; those attempts were not counted as passes. The hosted results above supply the exact-head selected-suite evidence.

The complete non-Draft suite and unsigned Mac artifact lanes remained skipped by the unchanged Draft policy. This receipt does not certify a later commit, full consumer readiness, external ATS support, real-device/private-state acceptance, signing or a 24-hour soak. Final application submission remains user-only.
