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
- **Type / status:** `ENGINEERING_DEBT` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Treating free-form chat as a supported applicant-fact entry or certifying durable fact recovery. It remains disabled as a fact entry; the local task-card input is the only supported path for pending values until JCR-04 persistence.
- **Non-blocked work:** Command/state controls, browser ownership, SyntheticATS, migration compatibility, discovery, fact-store and UI development.
- **Safe degradation:** Model requests now receive only fixed local intent flags and bounded task state. The mapper receives fixed field meaning hints, never raw DOM labels/options. Structured local task creation preserves an entry path without exporting target values. New tasks remain `live_authorized=false` until JCR-03 target verification. Local task-card fact answers remain memory-only until JCR-04. No privacy PASS is claimed by this ledger update.
- **Existing evidence:** `tests/test_manager_v1.py`, `tests/test_jcr01_privacy.py` with novel name/family/URL/DOM and provider-retry canaries, local fact-input browser/API tests, and SyntheticATS runs on PR #11. Final PR-head and merged-main `foundation`/full `test` CI passed at `f44fc2a` and `f09f419` respectively; see `receipts/JCR-01-MAIN-INTEGRATION.md`.
- **Missing final evidence:** Durable private fact storage and restart recovery in JCR-04; per-case F-01 through F-09 certification remains pending and is not inferred from the integration run.
- **Final convergence condition:** Close this engineering debt only after durable fact storage and the related JCR-04 recovery tests. No owner input is needed to implement the fix.
- **Related acceptance IDs:** F-01, F-02, F-03; E-class fact-store cases in JCR-04.
- **Evidence / PR / commit:** PR #11; merge `f09f419af4eb6d9cda7b4a5efd0641b8eaf790c7`; no acceptance PASS claimed.

### DFG-002 — Browser page ownership after uncertain action

- **Source round:** JCR-02
- **Type / status:** `ENGINEERING_DEBT` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Automatic replay or manual resume of a task whose browser page/popup lineage is ambiguous or whose selected page left the task origin. That task remains blocked until a read-only reconciliation path proves the same target and draft.
- **Non-blocked work:** Owned-page selection, isolated popup tests, service/bootstrap recovery, action-attempt journal, other tasks and later independent rounds.
- **Safe degradation:** The production generic adapter no longer takes the global latest tab. Live connection does not adopt or close any existing blank/target tab; an unbound exact target tab blocks instead of being reused. Ambiguous, closed or cross-origin successors raise a typed ownership error; worker records `browser_ownership_unknown`, disables automatic retry, and refuses resume even after pause. A second live run with a prior unverified attempt blocks until durable task/tab reconciliation exists. Live worker mutations are fenced when the owned CDP process fingerprint changes. A durable run-attempt journal marks a crashed or exception-interrupted possible write `UNKNOWN_OUTCOME` and blocks automatic replay.
- **Existing evidence:** `tests/test_browser_runtime_v1.py` covers unrelated tab, unique owned popup, multiple popups, closed page, cross-origin page, process-epoch change, persisted crash attempt and no blind worker replay. One case uses actual isolated headless Chromium and a loopback HTTP fixture. `tests/test_consumer_entry_v1.py` verifies an independent loopback recovery page, its token/origin guard and isolated service restart without user Chrome. Local full suite after bootstrap: 284 passed; final targeted suite: 34 passed. PR #12 head `34639e9` foundation and full test CI passed; bootstrap head CI pending.
- **Missing final evidence:** Durable task/tab target binding and read-only draft reconciliation after crash, browser process restart and human handoff race tests; per-action observed outcome rather than coarse runner-return state; G-01 through G-13 per-case evidence. Rollback to a worker that ignores unresolved attempts must remain disabled until release compatibility is proven.
- **Final convergence condition:** Resolve through JCR-02 production recovery and exact-head synthetic/fault CI; no owner input is needed.
- **Related acceptance IDs:** G-01, G-02, G-04, G-06, G-10, G-11, G-12.
- **Evidence / PR / commit:** JCR-02 writer branch `feat/jcr02-owned-browser-recovery`; PR and merge SHA pending.

## Invariants

1. A deferred item is never silently converted to PASS.
2. Runtime paths with unknown or unsafe external effects remain disabled, read-only, or explicitly unsupported.
3. Owner interaction is not requested while automatable independent work remains.
4. An `ENGINEERING_DEBT` item must be resolved before consumer certification; it may not be reclassified as a live-owner issue merely to avoid engineering work.
5. JCR-09 is responsible for exhausting all remaining automatable work, resolving engineering debt, and then presenting only the irreducible final owner/live/external set.
