# JCR-01 main integration readback

Canonical package: `JAE-CONSUMER-READINESS-v1`.

- PR: [#11](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/11), merged 2026-09-23 17:47 UTC.
- PR head: `f44fc2a8261fae3815e50e461c6ada601e0311a8`.
- Main merge SHA: `f09f419af4eb6d9cda7b4a5efd0641b8eaf790c7`; verified by GitHub branch and local fast-forward readback.
- Main exact-head CI: [run 35897981884](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35897981884), `foundation` and full `test` both success. The PR's final head also passed both checks in [run 35897644870](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35897644870).
- Local isolated macOS regression before merge: 274/275 passed; the single failure was an outdated dashboard copy assertion after replacing unsupported chat-created targets with a structured form. The corrected assertion passed targeted rerun, and final PR and main CI passed the complete suite. No test was skipped or weakened.
- Synthetic UI → service → worker → headless browser → SyntheticATS draft oracle matched expected name/email and observed zero submit requests. Real applicant, account, SMS, target or user Chrome profile: never used.
- Main `STATUS.json` readback: `JCR-01 ADVANCE_ALLOWED_WITH_DEFERRED`, `JCR-02 READY`. `DFG-001` remains `MITIGATED_FOR_ENGINEERING` for durable local applicant-fact recovery in JCR-04. Acceptance matrix cases remain `NOT_RUN` pending per-case certification; this integration readback does not convert them to PASS.
- Main branch has no other open PR/writer at the JCR-02 start readback. JCR-02 continues on `feat/jcr02-owned-browser-recovery`.

The detailed implementation, migration/backup, rollback and local F-class evidence map are in `JCR-01-WIP-001.md`. The legacy external CLI path is intentionally disabled; reverting the new command entry must preserve the no-submit, private fact, protected-target and owned-browser safeguards.
