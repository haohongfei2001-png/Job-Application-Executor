# Packaged setup/test budget verification

- Exact tested head: `fc83d79483c209f932879ab53793a5b97bbbc1cc`
- [Application Executor CI 36770667312](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36770667312), attempt 1: completed **SUCCESS** on 2026-09-30
- All eight selected jobs passed. The complete non-Draft test gate remained skipped under the existing Draft policy

## Verified results

- Linux packaged consumer/compatibility: 623 passed in 584.03 seconds
- Linux packaged recovery: 40 passed in 417.69 seconds
- Linux packaged release/distribution: 247 passed, 16 declared platform skips
- Hosted Mac consumer/compatibility: 623 passed in 497.78 seconds
- Hosted Mac recovery: 40 passed; runtime/distribution: 149 passed
- Hosted Mac native integration: 198 passed in 1124.83 seconds, plus 7 retired-updater entrypoint cases passed with 87 existing deselections
- Foundation: 25 privacy/contract cases (1 existing deselection), 2 state-sequence cases, 358 browser/review cases, 163 local operations/window cases, and 55 dashboard cases (2 existing deselections) passed
- Python compilation, dashboard JavaScript syntax and PR whitespace checks passed

The preceding failure was a 12-minute Linux job timeout after slow dependency setup plus unavailable hosted Mac runners. The tested commit changed only the packaged job budget from 12 to 20 minutes and added a separate 12-minute test-step ceiling. All selected test files, matrices, runner platforms, guards and final complete-suite policy were retained. No rerun was needed; all four Mac shards obtained their original runner platform and executed.

This receipt certifies only the stated hosted engineering checks on the exact head. It does not certify a later commit, full consumer readiness, real-device acceptance, external ATS support, live/private state transfer, signing, a 24-hour soak or final submission. Final application submission remains user-only.
