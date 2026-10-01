# JCR09 controlled divergent-draft fault gate — exact-head CI receipt

Date: 2026-09-29 UTC
Scope: synthetic JCR09 Z-03 engineering evidence on sole Draft PR #20. No live applicant or external ATS action.

## Exact tested code heads

| Code head | Change | GitHub Actions evidence |
| --- | --- | --- |
| `7e4efb4995f61fb462d7c62ce4129a8e558b3d45` | React controlled fixture: ten complete local UI → manager → durable queue → worker → isolated browser tasks where HTTP 204 acknowledges a draft write but independent server readback holds a different nonempty applicant name | [Application Executor CI 36607350963](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36607350963) completed SUCCESS. Foundation 357 forms/review/browser cases; hosted Mac native integration 197 cases. Every selected packaged/Mac job succeeded. |
| `30e113b0ed3f40bc81b166b7d2035561312fa3c0` | Corresponding Vue controlled fixture: ten additional complete tasks with the same divergent persisted-value fault | [Application Executor CI 36609624513](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36609624513) completed SUCCESS. Foundation 358 forms/review/browser cases; hosted Mac native integration 198 cases. The Vue owning test explicitly passed on hosted Mac; every selected packaged/Mac job succeeded. |

The two fixture tests require independent server-draft divergence after HTTP 204, a durable `BLOCKED/draft_persistence_unverified` task after queue reopen, exact task/target binding, no review admission and zero final submit. Existing complete fixture tests and assertions remain selected. A later documentation commit is not itself a new code certification.

## Limits

This is repeated synthetic Z-03 fault evidence for controlled React and Vue mechanisms. It does not complete frozen Z-02's 100 diverse golden tasks, the full three-repeat/platform Z-03 matrix, 24-hour Z-04 soak, external ATS support, JCR08/JCR09 certification or final personal acceptance. The full non-Draft closure lane was skipped by these Draft CI runs. JCR08 remains IN_PROGRESS/NOT_CERTIFIED; JCR09 formal status remains NOT_STARTED. Live/private/device/signing/security gates remain deferred by dependency, and final submit remains human-only.
