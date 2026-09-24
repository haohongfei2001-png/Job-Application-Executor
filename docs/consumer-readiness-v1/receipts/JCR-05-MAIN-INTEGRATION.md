# JCR-05 main integration receipt

- Canonical package: `JAE-CONSUMER-READINESS-v1`. Round status: `ADVANCE_ALLOWED_WITH_DEFERRED`; JCR-06 is READY. Consumer certification remains `NOT_CERTIFIED`.
- Sole writer: [PR #15](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/15), `feat/jcr05-auth-attempts`; final remote head `5318d5905ca3032e74009eaac09363fcc6f7b283`, tree `84e80841f1717f6528acd8718179aeb8f61c2f6e`.
- Exact-head CI: [run 35931676896](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35931676896), foundation and full test success. Local final-tree isolated/headless suite: **384 passed in 71.95 seconds**. Three automated review findings were fixed with direct regressions, answered and resolved; zero unresolved threads at merge.
- Squash merge: `476d19de4f4c6750cd1a46beeda20cbbd556579e`, parent `fc2ba2185b9dddfdec07e3dcd95b30489603ce73`. Remote main readback returned the same SHA and tree as the final PR head.
- Exact-main CI: [run 35931971776](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35931971776), foundation and full test success on `476d19de4f4c6750cd1a46beeda20cbbd556579e`.
- Remote main `STATUS.json` readback: JCR-01–05 `ADVANCE_ALLOWED_WITH_DEFERRED`, JCR-06 `READY`, consumer `NOT_CERTIFIED`. DFG-006 retains real account, SMS, device permission, security challenge and same-job evidence as `FINAL_LIVE_NOT_RUN`; C-class final-live cells are not PASS.
- Migration: additive, idempotent metadata-only auth-attempt schema; prior unknown-effect attempt and task survive restart. Rollback to code without the journal keeps any attempted SMS task read-only and cannot re-send. See `JCR-05-ENGINEERING.md`.
- No real account, profile, private Messages database, SMS, application write or final submit was used for this round.
