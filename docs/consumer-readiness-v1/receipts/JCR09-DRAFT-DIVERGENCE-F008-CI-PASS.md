# JCR09 repeated draft-divergence refusal: f0082fa8 CI PASS

- Sole Draft writer: PR #20, `feat/jcr08-consumer-app`
- Exact tested head: `f0082fa8bcb951981eae56d2f4e25f761babe63b`
- Application Executor CI: https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36563313828
- Result: COMPLETED / SUCCESS

The Draft foundation job passed 349 complete forms/review/browser cases in 399.97 seconds. The new repeated fault case ran 20 distinct tasks through the actual authenticated local UI, manager, durable queue, worker and isolated Chromium adapter. A conflicting server draft after browser filling was observed independently for every task; each ended BLOCKED with `draft_persistence_unverified`, and the synthetic ATS received zero submit requests. The prior 100 distinct successful golden tasks, 1000-task/6000-control state sequence gate and 50 lost-response restarts remained selected and passed. Foundation also passed 25 privacy cases (one existing deselection), 163 local operations, 55 workspace cases (two existing deselections), syntax and whitespace.

All selected Linux packaged candidate and hosted Mac jobs succeeded; full Linux was skipped by the declared Draft condition. The prior full CI pass on `7f1d8fa16e6bd7e2eed4d6735a300d04a724f3ca` is separately recorded. This is one repeated synthetic fault class only, not JCR08/JCR09 certification or proof of live ATS compatibility. Other fault classes, 24-hour soak, full matrix, private/live/device/security/signing and final human submit remain open, deferred or prohibited.