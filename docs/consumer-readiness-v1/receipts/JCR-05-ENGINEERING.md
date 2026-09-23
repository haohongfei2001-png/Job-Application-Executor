# JCR-05 engineering receipt

- Package: `JAE-CONSUMER-READINESS-v1`; round: Auth & OTP Lifecycle.
- Sole writer: PR [#15](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/15), `feat/jcr05-auth-attempts`.
- Evidence environment: synthetic job/profile, fake SMS/relay, localhost service, isolated headless Chromium. No real account, phone delivery, private Messages database, profile, job application or final submit was used.
- Consumer certification remains `NOT_CERTIFIED`; every C-class `FINAL_LIVE` cell remains `NOT_RUN` under DFG-006.

## Automated evidence and safe degradation

| Cases | Engineering evidence | Automated disposition |
|---|---|---|
| C-01 | Authenticated fake site reaches unresolved fact with zero SMS sends and zero submits. Account identity on a real site remains unverified. | `AUTO_PASS` for no-send synthetic path; `FINAL_LIVE_NOT_RUN` |
| C-02–03 | Unique SMS context uses a confirmed local phone, durable task/attempt/origin/deadline metadata, fake SMS request, local code ingestion and exact-job return. No phone/code value is stored in attempt metadata. | `AUTO_PASS`; `FINAL_LIVE_NOT_RUN` |
| C-04–05 | Late fake source resumes a waiting task without owning a worker lease. Old, expired, repeated, ambiguous and cross-task/attempt/origin codes fail closed. | `AUTO_PASS`; `FINAL_LIVE_NOT_RUN` |
| C-06–07 | Local one-use resend command requires a matching revision and elapsed cooldown; browser re-proves the one resend control. Pre-click unknown effect survives restart and cannot silently click again. | `AUTO_PASS`; `FINAL_LIVE_NOT_RUN` |
| C-08 | A proven SMS form may coexist with a separate password or QR mode; password in the same form and ambiguous controls stop. | `AUTO_PASS`; `FINAL_LIVE_NOT_RUN` |
| C-09–11 | Existing isolated challenge/password/QR negative tests remain; security actions and passwords are never automated or sent to a model. | `AUTO_PASS` for synthetic boundaries; `FINAL_LIVE_NOT_RUN` |
| C-12 | Fake site redirect to a different job path becomes `auth_return_unverified`, blocks direct resume and does not re-send SMS or submit. | `AUTO_PASS` for wrong-route fault; `FINAL_LIVE_NOT_RUN` |
| C-13 | Broken relay may fall back only to explicitly enabled local Messages with site sender and body rules. Relay results need matching attempt and origin; source configuration is not reported as delivery proof. | `AUTO_PASS` with fake sources; `FINAL_LIVE_NOT_RUN` |
| C-14 | Authenticated local task card and CLI use attempt-bound ingestion; browser/API tests reject unauthenticated input and scan SQLite, task events, UI state, diagnostics and runtime files for the code. | `AUTO_PASS`; `FINAL_LIVE_NOT_RUN` |
| C-15 | Generic segmented controls have no certified driver. The browser rejects filling all segments and hands the challenge to the user. | `UNSUPPORTED_SAFE`; `FINAL_LIVE_NOT_RUN` |

The isolated fake site confirms that normal authentication and an explicitly authorized resend return to the exact task; its final-submit counter remains zero. The one-time code never appears in model requests because the manager provider context receives bounded task metadata only; the local OTP endpoint bypasses chat.

## Migration and rollback

`auth_attempts` is a new metadata-only table. The resend columns are additive and migrated idempotently. A test starts with the previous schema and an unknown-effect send, opens it with the new store, verifies the old wait and task survive, and reopens it after process restart. No code, phone value, low-entropy code hash or private Messages copy is migrated.

Rollback to code without the attempt journal must keep any task with an attempted SMS in read-only recovery. Stop the worker, preserve the queue backup and the JCR-05 database, and do not use the older executable to resume or send on those tasks. Re-enable execution only after a certified forward version reconciles the exact task/attempt outcome. No automatic rollback resends SMS.

## Remaining final-live evidence

DFG-006 retains real account identity, actual delivery and permission state, password/CAPTCHA/QR/face/security-key actions, genuine job return and owner review. The exact-head CI, review closure, merge and exact-main readback are recorded in the subsequent main integration receipt.
