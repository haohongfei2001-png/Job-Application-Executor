# JCR-03 main integration receipt

Canonical package: `JAE-CONSUMER-READINESS-v1`.

- Sole writer PR [#13](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/13), `feat/jcr03-discovery-identity`, finished at remote head `5e72685ffad7532fad632dde45751cc1a6b18b31` and tree `0df86622a34c404d54c1effd8dfe5b038691914a`. That tree matched the local 321-test tree byte for byte.
- Exact-head CI [35913921850](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35913921850) passed `foundation` and full `test`. The two automated review findings (Schneider coverage and OPPO employment detail consistency) were fixed in that head, answered, regression tested and both threads resolved.
- Squash merge `7be271f709a31db2bddc32ba877aeaa1fba9758f` on remote `main` has the same tree. Exact-main push CI [35914239049](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35914239049) passed both jobs. Remote STATUS readback: JCR-03 `ADVANCE_ALLOWED_WITH_DEFERRED`, JCR-04 `READY`; no parallel open PR at merge.
- Local full isolated/headless regression after review repair: 321 passed in 51.02 seconds. Public read-only, synthetic browser, target identity, protected target alias, and migration/rollback limits are recorded in `JCR-03-ADVANCE.md`.

This is engineering integration evidence. B-01–B-13 final-live evidence remains NOT_RUN. DFG-003 keeps Schneider unique-target verification incomplete until its public listing/detail contract is proven; unsupported routes remain disabled. JCR-04 proceeds on independent durable facts work.
