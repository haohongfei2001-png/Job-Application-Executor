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

No deferred gates have been recorded yet.

## Invariants

1. A deferred item is never silently converted to PASS.
2. Runtime paths with unknown or unsafe external effects remain disabled, read-only, or explicitly unsupported.
3. Owner interaction is not requested while automatable independent work remains.
4. An `ENGINEERING_DEBT` item must be resolved before consumer certification; it may not be reclassified as a live-owner issue merely to avoid engineering work.
5. JCR-09 is responsible for exhausting all remaining automatable work, resolving engineering debt, and then presenting only the irreducible final owner/live/external set.
