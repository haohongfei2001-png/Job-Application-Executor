# Deferred Final Gates

Canonical package: `JAE-CONSUMER-READINESS-v1`

Purpose: centralize every issue or evidence requirement that cannot be safely completed during unattended engineering. A ledger entry blocks only its dependent action/evidence. It MUST NOT stop unrelated development and MUST NOT be counted as PASS.

Final owner/live/permission/paid/external convergence happens in JCR-09 after all automatable work is exhausted.

## Status vocabulary

- `OPEN` — unresolved, dependent action remains unavailable.
- `MITIGATED_FOR_ENGINEERING` — unsafe/live path is disabled or substituted by synthetic/isolated evidence so development can continue.
- `READY_FOR_FINAL_CONVERGENCE` — all automatable preparation is done; only final live/owner/external evidence remains.
- `RESOLVED` — final evidence or engineering resolution exists and is linked.

## Types

- `FINAL_LIVE` — real account/job/device/SMS/private-data/owner acceptance evidence.
- `EXTERNAL` — third-party availability, permission, signing account, paid prerequisite.
- `ENGINEERING_DEBT` — unresolved technical issue that can be safely isolated while independent work proceeds.
- `REAL_DATA_MIGRATION` — real legacy/private data proof not yet available; synthetic/compatibility work proceeds.

## Required entry fields

| Field | Meaning |
| --- | --- |
| ID | Stable `DFG-###` identifier |
| Source round | JCR round that discovered the item |
| Type | One of the types above |
| Status | Ledger status |
| Blocking scope | Exact action/evidence that may not proceed |
| Non-blocked work | What development continues |
| Safe degradation | disabled/read-only/unsupported/synthetic/isolated behavior |
| Existing evidence | What is already proven |
| Missing final evidence | What remains |
| Final convergence condition | Exact proof/action needed in JCR-09 |
| Related acceptance IDs | Matrix IDs |
| Evidence/PR/commit | Links or SHAs |

## Open ledger

### DFG-001 — Free-form applicant facts in manager chat

- **Source round:** JCR-01
- **Type / status:** `ENGINEERING_DEBT` / `OPEN`
- **Blocking scope:** Certifying F-02 and treating free-form chat as a safe entry for arbitrary new applicant facts. The current PR remains unmerged while this production path is unresolved.
- **Non-blocked work:** Command/state controls, browser ownership, SyntheticATS, migration compatibility, discovery, fact-store and UI development.
- **Safe degradation:** Current branch blocks credential/OTP and obvious personal-value patterns locally, removes raw pending-answer text from provider requests, omits site options from field-mapper requests, and offers a local task-card fact input. The local answer is still memory-only until JCR-04; arbitrary new facts in free-form chat are not yet proven safe. No privacy PASS is claimed.
- **Existing evidence:** `tests/test_manager_v1.py`, `tests/test_jcr01_privacy.py`, local fact-input browser/API tests, and SyntheticATS runs on PR #11.
- **Missing final evidence:** Strict provider-payload minimization that covers ordinary names, family facts and novel personal values in all free-form chat paths; durable private fact storage and canary tests for every model/export path.
- **Final convergence condition:** Resolve engineering debt with production code and exact-head privacy regressions before JCR-01 closure or any consumer certification. No owner input is needed to implement the fix.
- **Related acceptance IDs:** F-01, F-02, F-03; E-class fact-store cases in JCR-04.
- **Evidence / PR / commit:** PR #11; no PASS or merge SHA yet.

## Invariants

1. A deferred item is never silently converted to PASS.
2. Runtime paths with unknown or unsafe external effects remain disabled, read-only, or explicitly unsupported.
3. Owner interaction is not requested while automatable independent work remains.
4. An `ENGINEERING_DEBT` item must be resolved before consumer certification; it may not be reclassified as a live-owner issue merely to avoid engineering work.
5. JCR-09 is responsible for exhausting all remaining automatable work, resolving engineering debt, and then presenting only the irreducible final owner/live/external set.
