# JCR-02 main integration receipt

Canonical package: `JAE-CONSUMER-READINESS-v1`

- Sole writer PR: [#12](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/12), `feat/jcr02-owned-browser-recovery`.
- Final PR head: `022ba1df79ffeb7a0e8816b07750439a8fbcbca3`; its tree `27a0cff3642a29ce1d388da5f8b6158048c38a7f` matched the local 301-test tree byte for byte.
- Exact-head CI: runs [35907835089](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35907835089) and [35907835893](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35907835893), both `foundation` and full `test` successful. The two review comments were fixed with regressions and both review threads resolved.
- Merge: `959cd9d2faf22b2620f5055362e391b7f3b899ad` on remote `main`; merge tree is the same `27a0cff` tree. Exact-main push CI [35908127690](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35908127690) passed both jobs. Remote `main` STATUS readback: JCR-02 `ADVANCE_ALLOWED_WITH_DEFERRED`, JCR-03 `READY`.
- Local isolated/headless regression: 301 passed in 48.70 seconds. The receipt `JCR-02-ADVANCE.md` describes synthetic CDP, popup, crash/lease, browser restart, one retained server draft write, zero submit, and bootstrap evidence.

This is engineering integration evidence, not G-01–G-13 certification. DFG-002 remains engineering debt: the affected task stays blocked when an external write outcome or draft identity cannot be independently proven. Unknown writes must never be replayed. JCR-03 proceeds on independent target discovery while later form/recovery work closes that debt before final certification.
