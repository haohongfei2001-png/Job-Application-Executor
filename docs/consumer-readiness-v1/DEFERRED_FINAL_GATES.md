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
- **Type / status:** `ENGINEERING_DEBT` / `RESOLVED` by JCR-04 engineering and main integration.
- **Blocking scope:** None for durable local task-card fact recovery. Free-form manager chat remains intentionally unsupported for private fact entry; this is a product boundary, not an unresolved engineering shortcut.
- **Non-blocked work:** Command/state controls, browser ownership, SyntheticATS, migration compatibility, discovery, fact-store and UI development.
- **Safe degradation:** Model requests receive fixed local intent flags and bounded task state; private answers stay in the local UI/API and encrypted task-only journal. Reuse requires an explicit local checkbox and a known canonical key. OTP/authentication and one-time policy values cannot enter reusable facts. No F-class final-live PASS is claimed.
- **Existing evidence:** `tests/test_manager_v1.py`, `tests/test_jcr01_privacy.py` with novel name/family/URL/DOM and provider-retry canaries, local fact-input browser/API tests, and SyntheticATS runs on PR #11. Final PR-head and merged-main `foundation`/full `test` CI passed at `f44fc2a` and `f09f419` respectively; see `receipts/JCR-01-MAIN-INTEGRATION.md`.
- **Existing JCR-04 evidence:** `tests/test_jcr04_facts.py` covers encrypted task answer restart and cross-task isolation, explicit reuse in the isolated local browser, concurrent atomic canonical writes, stale READY review invalidation, encrypted legacy CLI migration and privacy canaries. PR #14 final head `466fed1`, merged main `fc2ba21`, exact-head CI 35924391280 and exact-main CI 35924692517 passed; see `receipts/JCR-04-MAIN-INTEGRATION.md`.
- **Missing final evidence:** Per-case F-01 through F-09 final-live certification remains pending and is not inferred from these automatic tests; see DFG-004.
- **Final convergence condition:** Engineering resolution and integration are complete. Separate real applicant evidence remains DFG-004/005 for JCR-09.
- **Related acceptance IDs:** F-01, F-02, F-03; E-class fact-store cases in JCR-04.
- **Evidence / PR / commit:** PR #11 merge `f09f419af4eb6d9cda7b4a5efd0641b8eaf790c7`; PR #14 merge `fc2ba2185b9dddfdec07e3dcd95b30489603ce73` and JCR-04 integration receipt; no final-live PASS claimed.

### DFG-002 — Browser page ownership after uncertain action

- **Source round:** JCR-02
- **Type / status:** `ENGINEERING_DEBT` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Automatic replay or manual resume of a task whose browser page/popup lineage is ambiguous or whose selected page left the task origin. That task remains blocked until a read-only reconciliation path proves the same target and draft.
- **Non-blocked work:** Owned-page selection, isolated popup tests, service/bootstrap recovery, action-attempt journal, other tasks and later independent rounds.
- **Safe degradation:** The production generic adapter no longer takes the global latest tab. Live connection does not adopt or close any existing blank/target tab; an unbound exact target tab blocks instead of being reused. Each first-run task now records its owned CDP target ID, process epoch and document epoch under the active lease before writes; a popup transition updates that binding only from the prior target, and each mutation rechecks the live document. An observed process/tab/document change blocks. Ambiguous, closed or cross-origin successors raise a typed ownership error; worker records `browser_ownership_unknown`, disables automatic retry, and refuses resume even after pause. A pause during a possible write revokes the lease, prevents the next action and leaves an unresolved attempt that Resume cannot bypass. A second live run with a prior unverified attempt still blocks. The new read-only observe path may report process/tab/document continuity but always reports draft identity and write outcome as unverified and refuses replay. A durable run-attempt journal marks a crashed or exception-interrupted possible write `UNKNOWN_OUTCOME` and blocks automatic replay.
- **Existing evidence:** `tests/test_browser_runtime_v1.py` covers unrelated tab, unique owned popup, multiple popups, closed page, cross-origin page, process-epoch change, persisted crash attempt, durable tab/document binding, read-only page observation and no blind worker replay. Real isolated headless Chromium tests cover popup lineage and a separate temporary-profile CDP process with loopback SyntheticATS: queued task ID and run attempt retained across replacement worker, one server-side draft write, reconnect without writing, browser kill/restart, `UNKNOWN_OUTCOME` block, one retained draft value, one total write and zero submit calls; the old epoch refuses continuation. The pause-during-run race revokes the lease and an unresolved attempt cannot be resumed through a generic pause marker. `tests/test_consumer_entry_v1.py` verifies an independent loopback recovery page, its token/origin guard and isolated service restart without user Chrome. Local full suite: 301 passed. PR #12 final head `022ba1d` and merged main `959cd9d` passed foundation and full test CI; see `receipts/JCR-02-MAIN-INTEGRATION.md`.
- **Missing final evidence:** Actual draft identity/read-back and safe continuation after crash, browser process restart and human handoff race tests; per-action observed outcome rather than coarse runner-return state; G-01 through G-13 per-case evidence. Process/tab/document continuity does not prove draft identity or the outcome of a prior write. Rollback to a worker that ignores unresolved attempts must remain disabled until release compatibility is proven.
- **Final convergence condition:** Resolve in follow-on browser/form recovery work before JCR-09 consumer certification with independently observed draft identity, per-action outcome, safe continuation and exact-head synthetic/fault CI. No owner input is needed for the engineering fix.
- **Related acceptance IDs:** G-01, G-02, G-04, G-06, G-10, G-11, G-12.
- **Evidence / PR / commit:** PR #12, final head `022ba1df79ffeb7a0e8816b07750439a8fbcbca3`, merge `959cd9d2faf22b2620f5055362e391b7f3b899ad`.

### DFG-003 — Schneider public listing coverage unproven

- **Source round:** JCR-03
- **Type / status:** `ENGINEERING_DEBT` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Declaring a unique Schneider Electric target, or treating a zero parsed result as a proven no-match, while its public result count/page/card contract is unverified.
- **Non-blocked work:** OPPO official read-only discovery, synthetic shared-ATS identity contracts, UI candidate choice, facts/auth/forms work and other independent rounds.
- **Safe degradation:** Schneider discovery returns `INCOMPLETE` whenever public count/page coverage is not proven. It cannot create a verified or live-authorized Schneider task on that evidence.
- **Existing evidence:** The isolated headless synthetic Schneider search returned zero parsed candidates with `complete=False`; an external read of `/jobs` returned 403. The official careers site links to its jobs portal. See `receipts/JCR-03-PUBLIC-RECON.md`.
- **Missing final evidence:** A versioned Schneider public listing/detail contract with reliable total, pagination, tenant/job identity and exact role/location/campaign proof, plus independent synthetic and public read-only regressions.
- **Final convergence condition:** Resolve this engineering debt before claiming Schneider support or JCR-09 consumer certification; if public access remains unavailable, keep Schneider unsupported and record the external limitation without fabricating a B-case PASS.
- **Related acceptance IDs:** B-01, B-02, B-04, B-06, B-07, B-08, B-11.
- **Evidence / PR / commit:** PR #13 final head `5e72685ffad7532fad632dde45751cc1a6b18b31`, merge `7be271f709a31db2bddc32ba877aeaa1fba9758f`, exact-main CI 35914239049; see `receipts/JCR-03-MAIN-INTEGRATION.md`.

### DFG-004 — Real applicant fact review and reuse consent

- **Source round:** JCR-04
- **Type / status:** `FINAL_LIVE` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Claiming that the owner's real MAX/resume, conflicts, project inventory, reuse choices and local review are complete or accurate.
- **Non-blocked work:** Synthetic fact store, isolated local UI, profile migration, auth, forms, final review and all later engineering rounds.
- **Safe degradation:** Unknown/conflicting real facts remain UNKNOWN. No private profile is read for unattended tests. Task-only answers never become global facts without explicit local opt-in. Site option mismatch does not rewrite canonical truth.
- **Existing evidence:** JCR-04 synthetic restart, consent, conflict, parser, exclusion, atomic-write and privacy tests; `receipts/JCR-04-ADVANCE.md`.
- **Missing final evidence:** Owner review of actual canonical facts and project/research inventory, any desired cross-application reuse consent, and real-source conflict resolution.
- **Final convergence condition:** JCR-09 owner reviews the private profile locally, confirms or corrects only necessary facts and reuse choices, and the final-live E/F cases receive evidence; no final submit is delegated.
- **Related acceptance IDs:** E-01 through E-09, F-01 through F-09.
- **Evidence / PR / commit:** JCR-04 PR #14 merged as `fc2ba2185b9dddfdec07e3dcd95b30489603ce73`; final-live NOT_RUN.

### DFG-005 — Real legacy/private profile migration proof

- **Source round:** JCR-04
- **Type / status:** `REAL_DATA_MIGRATION` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Declaring a real private legacy profile or application-answer migration complete without reading, reconciling and backing up that owner's data.
- **Non-blocked work:** Synthetic old/new profile compatibility, encrypted legacy answer migration, old-version snapshots, rollback guard tests and later independent rounds.
- **Safe degradation:** A noncanonical existing output or malformed legacy answer file fails closed; imports do not silently overwrite it. Existing canonical profiles receive a one-time `.pre-jcr04` snapshot before atomic versioned replacement. An older binary that cannot read encrypted answers is not a safe write rollback target. JCR-08 refuses manual rollback to an identity-only legacy app bundle; exact head 0ae30ca passed CI 36204390392. The next coherent candidate holds the daemon's state lock and initializes only the candidate queue against a disposable WAL-aware SQLite snapshot, requiring existing task authority and every journal/receipt/binding table to remain intact before either app bundle moves. It never starts a candidate worker with copied applicant state. Targeted CI is pending; real private migration and older worker-fence compatibility remain unverified.
- **Existing evidence:** Synthetic malformed/stale/concurrent write tests, CLI answer migration and old task-spec restart regressions in JCR-04.
- **Missing final evidence:** Actual private source inventory, sanitized migration comparison, and owner acceptance of any conflicts; no real private data is accessed unattended.
- **Final convergence condition:** JCR-09 reviews the real migration snapshot and differences locally with the owner, or keeps the affected path read-only if proof is unavailable.
- **Related acceptance IDs:** E-04, E-06, E-08, E-09, G-03, G-04.
- **Evidence / PR / commit:** JCR-04 PR #14 merged as `fc2ba2185b9dddfdec07e3dcd95b30489603ce73`; real migration UNVERIFIED.

### DFG-006 — Real SMS transport, device permission and security challenge evidence

- **Source round:** JCR-05
- **Type / status:** `FINAL_LIVE` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Claiming real-account SMS delivery, Mac Messages permission, owner-enabled relay identity, CAPTCHA/password/QR/face/security-key handoff, or a real return to an intended job after authentication.
- **Non-blocked work:** Durable non-secret AuthAttempt metadata, attempt-bound in-memory OTP, fake SMS and relay transport fixtures, isolated browser login, explicit resend and cooldown contract, crash recovery, handoff UI, return-target checks and all later independent engineering.
- **Safe degradation:** No real SMS, account, private Messages database, new permission or security challenge is used during unattended development. Local push requires a current task/attempt token; relay responses without matching attempt and origin are ignored. Mac Messages is read only when private configuration explicitly enables it and provides site-specific sender and body rules; a configured source is never reported as proven online. Wrong-job return blocks direct resume. A live form write without certified active-account identity proof is blocked before the first field. Unsupported or ambiguous real authentication remains human-handled.
- **Existing evidence:** JCR-05 isolated fake SMS, guarded resend, late-source, local UI, secret exit-scan and wrong-job tests are recorded in `receipts/JCR-05-ENGINEERING.md`. PR #15 final head `5318d590`, merge `476d19d`, exact-head CI 35931676896 and exact-main CI 35931971776 passed; see `receipts/JCR-05-MAIN-INTEGRATION.md`.
- **Missing final evidence:** Authorized real account/phone, existing or owner-enabled transport and device permission, ordinary delivered SMS matched to one attempt, real security handoff and same-job return proof.
- **Final convergence condition:** In JCR-09, the owner enables or confirms only the chosen real transport and performs unavoidable security actions on the real site; the system records redacted attempt and return-target evidence without retaining code or granting automated final submit.
- **Related acceptance IDs:** C-01 through C-15 and G-class auth recovery cases.
- **Evidence / PR / commit:** PR #15 merged as `476d19de4f4c6750cd1a46beeda20cbbd556579e`; final-live NOT_RUN.

### DFG-007 — Real applicant structured-form and draft evidence

- **Source round:** JCR-06
- **Type / status:** `FINAL_LIVE` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Claiming that an authenticated real applicant's fields, repeated records, attachments, autosave, multi-page draft and site validation satisfy D-01 through D-22 on a real target. No real applicant profile, account, job application or upload is used for unattended proof.
- **Non-blocked work:** Production form drivers, typed representation, row recovery, isolated React/Vue and fake-service fixtures, independent server draft oracles, fault injection, rollback and later independent engineering rounds.
- **Safe degradation:** The generic driver blocks unproven upload, unsupported components, ambiguous repeated rows and drafts without independent readback. A failed or unknown intermediate save cannot advance or blindly Resume. No automated final submit is available.
- **Existing evidence:** JCR-06 draft PR #16 has isolated synthetic structural, attachment-preflight, dependency, multi-page and server-draft tests; `receipts/JCR-06-WIP.md` records exact local results. These are AUTO subevidence only, not final-live PASS.
- **Missing final evidence:** Owner-authorized real session and exact target, applicant fact/record review, permitted file/version proof, site-specific saved draft and attachment readback, and per-case D-01 through D-22 real-site results. Engineering gaps listed in the JCR-06 WIP receipt remain engineering work and are not deferred by this entry.
- **Final convergence condition:** After automatable JCR-06 engineering and later rounds are complete, the owner performs only unavoidable real account, file, draft and final acceptance actions in JCR-09. Unsupported site paths remain disabled if proof is unavailable; no false D-case PASS or automated final click is permitted.
- **Related acceptance IDs:** D-01 through D-22; G-class browser recovery where the same real draft is involved.
- **Evidence / PR / commit:** JCR-06 draft PR #16; final-live NOT_RUN.

### DFG-008 — JCR-06 unsupported form capability and question context

- **Source round:** JCR-06
- **Type / status:** `ENGINEERING_DEBT` / `MITIGATED_FOR_ENGINEERING`
- **Blocking scope:** Claiming general ATS structured-form compatibility, asking an unknown site question through a context-free key, or continuing an uncertain browser/draft write as if it succeeded. These paths cannot contribute D-class PASS or a consumer READY certificate.
- **Non-blocked work:** JCR-07 independent review and certificate, JCR-08 app/release engineering, JCR-09 synthetic/fault certification, and supported synthetic JCR-06 drivers.
- **Safe degradation:** Generic repeated rows, file uploads, iframe/open-shadow/custom controls and virtualized choices remain unsupported unless a certified site driver supplies exact identity and readback. Unknown task answers bind to target/page/question digests, preventing cross-field reuse; a consumer UI must present the local question context before accepting one. Unknown browser/draft effects stay blocked under DFG-002.
- **Existing evidence:** PR #16 implements scoped row journals, independent draft and attachment receipts, a composed browser/API fixture, exact target and question answer binding, auth-field refusal and 448 passing isolated/headless local tests. `receipts/JCR-06-WIP.md` lists the synthetic scope and negative cases. PR #20 adds the local contextual question workbench and `SUPPORTED_DRIVER_MATRIX.md`, backed by a registry diagnostic and route-to-adapter regression; current candidate CI remains required before counting this new evidence.
- **Missing final evidence:** Worker-level read-only browser/draft recovery with per-action outcome and complete automatic D-case evidence for any supported production driver. The contextual local unknown-question workbench and declared production-driver matrix now exist; neither certifies unsupported external site components. Those components need a dedicated capability decision and fixtures before support is claimed.
- **Final convergence condition:** Resolve automatable debt and run affected D/G/H-class safety and fault tests by JCR-09; leave any still unproven external component explicitly unsupported. Real applicant/site evidence is separately DFG-007 and cannot resolve this engineering debt.
- **Related acceptance IDs:** D-03 through D-11, D-15, D-16, D-17 through D-22; G-01/G-02/G-10/G-11.
- **Evidence / PR / commit:** PR #16 candidate; automatic subevidence only, no D-class aggregate PASS.

## Invariants

1. A deferred item is never silently converted to PASS.
2. Runtime paths with unknown or unsafe external effects remain disabled, read-only, or explicitly unsupported.
3. Owner interaction is not requested while automatable independent work remains.
4. An `ENGINEERING_DEBT` item must be resolved before consumer certification; it may not be reclassified as a live-owner issue merely to avoid engineering work.
5. JCR-09 is responsible for exhausting all remaining automatable work, resolving engineering debt, and then presenting only the irreducible final owner/live/external set.
