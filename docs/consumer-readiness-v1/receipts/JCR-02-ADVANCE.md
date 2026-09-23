# JCR-02 advance receipt — deferred recovery gates

Canonical package: `JAE-CONSUMER-READINESS-v1`

Status: `ADVANCE_ALLOWED_WITH_DEFERRED`, **not COMPLETE and not consumer certified**. JCR-03 may proceed only on independent discovery/target surfaces. DFG-002 remains engineering debt and must be resolved before final consumer certification.

## Authority and writer

- Remote main base: `f09f419af4eb6d9cda7b4a5efd0641b8eaf790c7` after JCR-01 merge and exact-main CI.
- Sole writer: PR [#12](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/12), `feat/jcr02-owned-browser-recovery`. No parallel JCR-02 PR.
- Contract: JCR-02 in `05_ROUNDS.md`, browser/recovery ADRs in `02_ARCHITECTURE.md`, G-01–G-13 in `04_ACCEPTANCE_MATRIX.md`, and continuous-execution policy in `06_EXECUTION_PROTOCOL.md`.
- Final PR head, merge SHA and exact-main readback are recorded in the following main-integration receipt once they exist; this receipt does not claim them early.

## Implemented and independently checked

- Production task page selection follows one owned popup lineage and rejects unrelated, closed, ambiguous and cross-origin pages. Live connection does not close or adopt unbound user tabs.
- A task under an active lease persists the owned CDP process epoch, target ID and document epoch. Page and document identity are rechecked immediately before browser writes; popup transfer is fenced by the prior target ID.
- A durable run-attempt journal records possible external work before the runner starts. Expired leases and exceptions leave unknown outcomes blocked. Pause during a possible write revokes the lease and cannot bypass the journal on Resume. A second live run after an unverified result remains blocked.
- A read-only observer can report continuity of the bound page without navigation, filling or submission. Its result always says draft identity and write outcome are unverified and replay is disallowed.
- The consumer launcher opens an authenticated local panel despite missing model, profile or Chrome. If the supervisor fails, an independent loopback bootstrap page reports a safe cause/version and can retry the owned service through a token/origin-protected action. The panel distinguishes service liveness from live readiness.
- Diagnostics no longer call an unreadable Git checkout clean. Existing task IDs and pre-migration backup survive the additive attempt/binding schema; new metadata does not contain field values or OTP.
- Local complete isolated/headless suite after review repair: **301 passed in 48.70 seconds**. A real temporary-profile Chromium CDP test plus loopback SyntheticATS persists one server-side draft write across worker lease expiry and browser kill/restart: same task ID, one draft value, one total write, zero submits, no blind replay. The independent bootstrap service restart and pause race have separate targeted coverage.
- Review feedback on PR #12 is addressed with regression tests: an unmatched select option remains a deterministic fill failure without a browser write; final service health, rather than a stale start timeout, decides launcher readiness. First live navigation also rejects a cross-origin successor before using it.
- PR #12 earlier exact heads `34639e9`, `99e4bb3`, `60114c2`, `731d297`, `414c084` and `da5418b` passed foundation and full test CI. Exact-head CI for this receipt/status commit must pass before merge.

## Deferred acceptance and rollback

- **No G-01–G-13 case is marked PASS by this receipt.** Browser/process/command tests give partial automatic evidence; real draft identity, safe continuation, ordinary fact recovery, sleep/wake, complete human handoff, multi-record form restoration, UI reconnection and final-live evidence remain UNVERIFIED/NOT_RUN according to their cases.
- DFG-002 records the exact blocked path: a prior unknown or unverified external write cannot be resumed until an independent read-back proves the same target/draft and per-action outcome. Missing proof keeps that task blocked; it does not stop JCR-03 discovery, JCR-04 facts, later isolated form work or other tasks.
- Migration is additive and leaves the existing dedicated profile untouched. Old code that ignores unresolved attempts is **not** a safe runtime rollback target; a later release must prove compatibility or keep that downgrade disabled. Returning to the old global-last-page behavior is prohibited.
- No real account, job application, private Chrome profile, SMS, upload, paid service, new system permission or final submit was used by these tests.

## Next integration step

Require exact-head foundation/full test CI, close any review feedback, merge PR #12, verify exact-main CI and remote STATUS/ledger readback. Then begin JCR-03 on the one current writer branch; keep DFG-002 visible and return to it as its dependent recovery/forms work becomes executable.
