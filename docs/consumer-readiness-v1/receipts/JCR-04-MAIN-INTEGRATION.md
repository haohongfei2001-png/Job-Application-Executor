# JCR-04 main integration receipt

- Canonical package: `JAE-CONSUMER-READINESS-v1`. Round status: `ADVANCE_ALLOWED_WITH_DEFERRED`; JCR-05 is READY. Consumer certification remains `NOT_CERTIFIED`.
- Sole writer PR: [#14](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/14), branch `feat/jcr04-durable-facts`; final head `466fed1946fe74d8252c31869fe75f53b85a7435`, tree `0f4974aa88046a935af40b615663d1cc26fcc42c`.
- Exact-head CI: [run 35924391280](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35924391280), foundation and full test success. Local isolated/headless final-tree suite: 351 passed in 51.69 seconds. Eleven automated review findings from four reviews were fixed with direct regressions, answered and resolved; zero unresolved review threads at merge.
- Merge: squash commit `fc2ba2185b9dddfdec07e3dcd95b30489603ce73`, parent `7be271f709a31db2bddc32ba877aeaa1fba9758f`. Remote main readback returned the same SHA and the same tree as the final PR head.
- Exact-main CI: [run 35924692517](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/35924692517), foundation and full test success on `fc2ba2185b9dddfdec07e3dcd95b30489603ce73`.
- Remote `docs/consumer-readiness-v1/STATUS.json` readback on main: JCR-01–04 `ADVANCE_ALLOWED_WITH_DEFERRED`, JCR-05 `READY`; no consumer certification. DFG-004/005 retain real applicant/reuse and private migration proof for JCR-09. E/F final-live cases remain NOT_RUN. No real profile, account, SMS, application write or final submit was used.
- Rollback: `.pre-jcr04` canonical snapshot plus encrypted answer journal compatibility; old executables that cannot read the journal are read-only recovery targets. The JCR-05 writer starts from this exact merged tree.
