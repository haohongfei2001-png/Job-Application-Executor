# JCR-01 execution receipt — in progress

Status: ADVANCE_ALLOWED_WITH_DEFERRED on the PR branch, subject to exact-head CI and main readback. This is a checkpoint, not round certification.

## Source of truth

- Remote main base: `b2c1e0b37435c12d8a7e951dd43f60f8ce5b9eb5` (verified via GitHub and `git ls-remote` on 2026-09-24 Asia/Shanghai).
- Open PRs at start: none. Active JCR writer at start: none.
- Contract: `05_ROUNDS.md` JCR-01, `06_EXECUTION_PROTOCOL.md`, `STATUS.json` and `DEFERRED_FINAL_GATES.md`.

## Implemented production path

- Task rows now expose a durable revision, phase, run state, wait reason and paused origin. Existing task IDs survive additive schema migration; the old SQLite schema gets a private one-time consistent backup before alteration.
- Authenticated local command and UI paths accept typed pause/resume/cancel with expected revision and idempotent receipt lookup. UI controls are independent of a waiting model request. A stale model proposal is refused after the task changes.
- Pending facts have a local task-card input with revision checks and scoped field keys; values are kept out of UI responses, queue SQLite and model payloads. Persistence across restart remains JCR-04 work.
- Manager provider requests now contain only fixed local intent flags and bounded task state; arbitrary chat text, target URL, company/role and applicant values remain local. The field mapper receives fixed semantic hints in place of raw DOM labels/options. A structured UI form allows adding an explicit target without sending those values to the model; these newly created tasks remain unauthorized for external writes until JCR-03 target verification.
- Generic select readback is a validation error on wrong value or missing observed field. Project coverage uses exact normalized titles, and uncovered canonical projects block READY. OPPO city filtering rejects different or unknown cities when a city is requested.
- Isolated tests use Playwright's bundled headless Chromium; the live Chrome/CDP path remains separate.

## Automated evidence

- 1,000 seeded control steps, migration/backup readback, duplicate command replay and cross-process CAS tests: local AUTO tests passed.
- Local UI → supervisor → worker → headless browser → SyntheticATS server draft: expected synthetic name/email matched actual server state; automated submission count was 0.
- Wrong-city select after rerender, project-title prefix and location mismatch counterexamples: targeted AUTO tests passed.
- Historical suite plus current JCR-01 tests: 269 passed in an isolated macOS run after the review, task-reference, local fact-input, lease-state, strict provider-minimization and legacy-path safety fixes. Exact-head CI for the latest branch head and post-merge main integration are pending.
- PR review found a multi-page selector regression and an update/command admission race. Both received production fixes and targeted regressions before this 264-test run.
- New negative-control and A/B task-reference regressions deny a model proposal when the user says not to pause or names another task; the user-facing mutation reply is built from deterministic receipts.
- A push after the prior exact-head CI was interrupted because `github.com:443` timed out while `api.github.com` remained reachable. The connection recovered during this checkpoint; new work must be pushed to the same PR branch and receive fresh exact-head CI before integration. No merge or PASS was inferred from earlier CI.
- PR head `b8db941330bc099765cb41c059bf1dfc839ca0cf` had the full `test` job pass, but `foundation` failed because that job lacked the Playwright browser binary required by its new UI test. This is a CI setup fault, not a test assertion failure; the workflow now installs isolated Chromium in that job. Copy-safe diagnostics were also tightened to omit user-entered company, role, host and blocker strings after a novel-value canary exposed the export risk. These follow-up changes await a new head and CI.
- Later heads `38cb38c` and `0c6cfe7` passed both exact-head CI jobs. The current local follow-up disables the legacy CLI engines on external websites because their old runtime files may contain raw page text; it also disables default screenshots in the older application audit store. Isolated synthetic paths remain available. The SyntheticATS fixture now has a copy-safe manifest, and a provider HTTP retry canary verifies that neither initial nor fallback requests contain novel family facts.

## Boundaries and remaining work

### F-class automatic evidence map (local, pending latest exact-head CI and main readback)

| Case | Production path and direct regression |
| --- | --- |
| F-01 | Manager rejects OTP/credentials locally; `test_sensitive_chat_is_rejected_before_provider` plus `test_manager_http_retry_payload_is_minimized_for_novel_fact` inspect initial and fallback model request bodies. |
| F-02 | Manager context contains fixed flags/task state only; field mapper sends fixed semantic hints, not raw DOM labels/options. `tests/test_jcr01_privacy.py` covers novel names, family facts, URL values, site options and injected instructions. |
| F-03 | Scoped local answers are absent from queue SQLite/UI responses; OTP broker remains memory-only; diagnostics now omit arbitrary target strings. `test_ui_private_fact_input_is_local_scoped_and_revision_checked`, `test_diagnostics_are_copy_safe_and_never_emit_buffered_otp`, historical audit/queue tests. |
| F-04 | Local HTTP binds loopback and validates Host, Origin, bearer token, one-use ticket, session expiry and replay. `test_api_auth_local_binding_routes_and_redacted_errors`, `test_dashboard_http_ticket_cookie_and_same_origin_chat`, `test_ui_ticket_and_session_expire_without_auth_relaxation`. |
| F-05 | Worker and legacy audit screenshots are disabled by default; original CLI engines now reject external sites before connecting. `test_audit_redacts_sensitive_payloads` and `test_legacy_engine_rejects_external_site_before_browser_connect`. CI publishes no browser trace/HAR artifacts. |
| F-06 | Private runtime directories and real applicant files are absent from the Git index; the SyntheticATS fixture has a copy-safe manifest and loopback-only network allowlist. `tests/test_jcr01_artifact_policy.py`. |
| F-07 | The same protected-target predicate is called by enqueue, resume, retarget and worker before any browser write. `test_protected_target_guard_covers_enqueue_resume_retarget_and_worker`. Alias normalization beyond the current predicate belongs to JCR-03 B-12. |
| F-08 | Untrusted DOM instructions cannot map into profile values or final-submit authority; model receives fixed hints and never browser capabilities. `test_prompt_injection_label_cannot_map_to_profile_fact`, `test_submit_authorized_still_requires_manual_final_click`. |
| F-09 | Live CDP refuses an unrelated process or non-dedicated profile; isolated tests use their own Chromium. `tests/test_browser_runtime_v1.py`. Session/task/tab lineage remains JCR-02 scope. |

This table records local automatic evidence only. Matrix statuses remain `NOT_RUN` until the corresponding exact-head and main integration evidence is attached; later-round identity, durable facts, and live proof are not inferred from these tests.

- Real applicant data accessed: no. Real account, SMS, application or website side effects: no. Owner interaction: no.
- No acceptance matrix case is marked PASS by this checkpoint. F-01 through F-09 require exact-head CI and main integration evidence before final per-case disposition. Real local profile migration was not attempted. DFG-001 is mitigated for engineering through strict provider minimization and disabled chat fact entry; durable recovery is still JCR-04 work. JCR-01 is eligible for safe independent JCR-02 work after PR integration without falsely closing the deferred fact recovery.
- Next: complete F-class privacy/HTTP/browser ownership evidence, full exact-head regression and CI, review, merge, main readback, then close JCR-01 and advance to JCR-02.
