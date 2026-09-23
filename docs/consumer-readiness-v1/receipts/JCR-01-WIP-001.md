# JCR-01 execution receipt — in progress

Status: IMPLEMENTING. This is a checkpoint, not round certification.

## Source of truth

- Remote main base: `b2c1e0b37435c12d8a7e951dd43f60f8ce5b9eb5` (verified via GitHub and `git ls-remote` on 2026-09-24 Asia/Shanghai).
- Open PRs at start: none. Active JCR writer at start: none.
- Contract: `05_ROUNDS.md` JCR-01, `06_EXECUTION_PROTOCOL.md`, `STATUS.json` and `DEFERRED_FINAL_GATES.md`.

## Implemented production path

- Task rows now expose a durable revision, phase, run state, wait reason and paused origin. Existing task IDs survive additive schema migration; the old SQLite schema gets a private one-time consistent backup before alteration.
- Authenticated local command and UI paths accept typed pause/resume/cancel with expected revision and idempotent receipt lookup. UI controls are independent of a waiting model request. A stale model proposal is refused after the task changes.
- Generic select readback is a validation error on wrong value or missing observed field. Project coverage uses exact normalized titles, and uncovered canonical projects block READY. OPPO city filtering rejects different or unknown cities when a city is requested.
- Isolated tests use Playwright's bundled headless Chromium; the live Chrome/CDP path remains separate.

## Automated evidence

- 1,000 seeded control steps, migration/backup readback, duplicate command replay and cross-process CAS tests: local AUTO tests passed.
- Local UI → supervisor → worker → headless browser → SyntheticATS server draft: expected synthetic name/email matched actual server state; automated submission count was 0.
- Wrong-city select after rerender, project-title prefix and location mismatch counterexamples: targeted AUTO tests passed.
- Historical suite plus current JCR-01 tests: 261 passed in an isolated macOS run after the review and task-reference fixes. Exact-head CI and post-merge main integration are pending.
- PR review found a multi-page selector regression and an update/command admission race. Both received production fixes and targeted regressions before this 261-test run.
- New negative-control and A/B task-reference regressions deny a model proposal when the user says not to pause or names another task; the user-facing mutation reply is built from deterministic receipts.

## Boundaries and remaining work

- Real applicant data accessed: no. Real account, SMS, application or website side effects: no. Owner interaction: no.
- No acceptance matrix case is marked PASS by this checkpoint. F-01 through F-09 require complete per-case evidence. Real local profile migration was not attempted. JCR-01 remains IMPLEMENTING.
- Next: complete F-class privacy/HTTP/browser ownership evidence, full exact-head regression and CI, review, merge, main readback, then close JCR-01 and advance to JCR-02.
