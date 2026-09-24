# JCR-07 engineering evidence — independent review and human submit

State: ENGINEERING_READY_FOR_EXACT_HEAD_CI. This receipt does not claim real-account certification or authorize an automated final click.

## Acceptance mapping

| Gate | Automated evidence | Boundary |
| --- | --- | --- |
| H-01 complete certificate | `executor/review_certificate.py`, `tests/test_jcr07_review_certificate.py` | READY requires complete current server readback and zero mandatory uncertainty. |
| H-02 exact target/account/draft | Same certificate tests | Target, draft and active account digests bind to the certificate. |
| H-03 unknown/default/conflict | Same tests plus queue READY admission | Unknown field status, stale unresolved inventory, unconfirmed default and validation errors reject. |
| H-04 structured rows | Project row identity and value digest tests | Title-only matches do not certify a project. |
| H-05 attachments | Canonical digest and missing requested attachment tests | Filename alone is not a receipt. |
| H-06 invalidation | Same-document server edit and bound-document tests | Live full-value review re-reads the certified server draft; a changed draft discards the private review. |
| H-07 manual final submit | Adversarial Continue/Enter fixture and adapter guard | Generic automation makes zero final clicks. |
| H-08 post-click observation | Page-signal and certified server-receipt tests | Read-only tiers stay separate; server proof needs exact private target/draft/account/driver binding. User confirmation alone remains `user_confirmed`. |

Applicant values remain in bounded process memory and are shown only through the authenticated local review control. Durable task records and operational audit contain copy-safe counts/statuses, not raw answers, project titles, local file paths or submission IDs. The current generic adapter has no certified server readback and therefore cannot reach live READY. A future certified driver must implement read-only review and submission observers; no generic DOM success text can become server proof.

## Deferred and next steps

Real applicant/account/site evidence, real final human click and any real external side effects remain deferred under the package final-gate ledger. Final submit is permanently user-only. Before integration, record successful CI on the exact final PR head; after merging, run exact-main CI and write the main integration receipt. JCR-08 may start only after that main receipt.
