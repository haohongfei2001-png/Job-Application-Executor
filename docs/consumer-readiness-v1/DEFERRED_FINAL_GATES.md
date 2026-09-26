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
- **Safe degradation:** A noncanonical existing output or malformed legacy answer file fails closed; imports do not silently overwrite it. Existing canonical profiles receive a one-time `.pre-jcr04` snapshot before atomic versioned replacement. An older binary that cannot read encrypted answers is not a safe write rollback target. JCR-08 refuses manual rollback to an identity-only legacy app bundle; exact head 0ae30ca passed CI 36204390392. The next coherent candidate holds the daemon's state lock and initializes only the candidate queue against a disposable WAL-aware SQLite snapshot, requiring existing task authority and every journal/receipt/binding table to remain intact before either app bundle moves. It never starts a candidate worker with copied applicant state. Rollback compatibility passed exact-head targeted foundation/hosted Mac CI 36209087091 at d9afc7b34b3ac626dba77f2506197b2a5c4673a1. The installation/update candidate applies the same fence and snapshot compatibility before activation, with actual packaged service tests for committed WAL and destructive migration refusal; its targeted foundation/hosted Mac CI 36210075275 passed at 53df0ead516c76389749a46d9ac9fe1a8557ec43. Real private migration, transactional legacy state transfer and older worker-fence compatibility remain unverified.
- **Existing evidence:** Synthetic malformed/stale/concurrent write tests, CLI answer migration and old task-spec restart regressions in JCR-04.
- **Current compatibility engineering:** PR #20 additionally verifies an existing journal's SQLite schema floor, including original declarations/constraints, column contracts, indexes, foreign keys and fence triggers. Row-preserving schema weakening must fail before app activation/rollback. Additive migration remains supported. Candidate-process and hosted packaged-update regressions are pending exact-head targeted CI; this is automatic compatibility subevidence, not real private migration or transactional legacy state transfer.
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
- **Current candidate engineering:** PR #20 re-observes each supported native control after reactive rendering before the next write. Value/choice reversion or control-contract changes raise ownership uncertainty and preserve the existing UNKNOWN_OUTCOME/no-replay worker fence. Passing per-field results are explicitly DOM_READBACK only, not server persistence or draft identity. Synthetic browser and real-worker regression coverage passed targeted CI 36212739717 at 0a0adffc50d88b302e2a6f2a7565db92e018d4b8; this does not resolve DFG-002 or certify general ATS compatibility.
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


### JCR-08 startup provenance engineering batch — 2026-09-26

Prior exact-head targeted Ubuntu/hosted-Mac evidence: `a8fdb2da42204ae500b96be2fd10670a1cfc8729`, [CI 36213656240](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36213656240), PASS. Source review found that candidate health already used isolated Python, while the shipped shell and service/recovery subprocesses still inherited Python startup configuration. This batch aligns those actual entrypoints with `-I -B` and explicit owned-source loading. Production startup no longer depends on ambient PYTHONHOME/PYTHONPATH/user site/current-directory imports. Cloud tests exercise the actual packaged shell after removing the development checkout and the actual bootstrap starter/retry/authenticated service handshake under poisoned startup variables; hosted Mac remains mandatory and existing privacy/final-user-only assertions remain.

Upgrade recognition retains the exact previous packaged launcher template; arbitrary edited templates remain refused. A historical non-isolated launcher is not eligible for manual write rollback. Failed update restores/preserves that owned old bundle but reports recovery required rather than claiming healthy isolated startup. New regressions cover upgrade, refused rollback with both versions intact, and failed-candidate restoration without a false healthy claim. New candidate cloud evidence is pending. This closes an interpreter-mode mismatch only: the venv runtime still relies on a host base Python/stdlib/native dependencies. Independent runtime packaging/provenance, real legacy-state transfer and old-worker compatibility remain ENGINEERING_DEBT under DFG-008; DFG-002 independent durable server action reconciliation remains open. No external/private account access, actual job actions or owner permissions used; final submit remains user-only.


Startup batch targeted diagnosis: exact head `369b4c4a59b5d5ab770c7a07e8056e1c4d56e7b5`, [36215114340](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36215114340), Ubuntu and hosted Mac each 106 PASS / 1 FAIL. The failure occurred after real retry and authenticated health: a parent-only Popen recorder was incorrectly expected to observe the supervisor launched inside the independent bootstrap process. Classification TEST oracle process boundary. The oracle now verifies actual service.json PID/port plus untruncated OS process arguments (-I -B -c, module, owned source/runtime), alongside the original recovery single-writer, poison-import canary, token/origin and final-user-only assertions. Other tests, realistic fixtures, startup implementation and timeouts remain unchanged; exact new candidate targeted verification pending.


### JCR-08 pre-lock legacy service release fence — 2026-09-26

Isolated startup candidate `ec3072819723276d5807d88acbff0053a78dc21b` passed [targeted CI 36215498933](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36215498933): Ubuntu foundation and hosted Mac, 107 packaged/journal tests on each; 25 deterministic, 135 independent browser/review and 7 privacy cases. No unchanged-head rerun or full certification dispatch.

The next app transaction batch closes one bounded old-worker compatibility gap: acquiring the current worker lock alone cannot prove a pre-lock daemon has stopped. A bounded regular no-follow service.json read validates an integer PID and probes existence using signal 0 after acquiring the lock. A live or uninspectable PID blocks both install and rollback before candidate startup, state snapshot or app movement. Malformed, oversized, nonregular and aliased registry data refuses as unavailable. A dead process record stays intact; the updater never kills a service, deletes a registry or rewrites task authority to pass this guard. Synthetic tests exercise the production guard and both consumer operations, including unchanged committed-WAL task/event authority and retained app copies.

New candidate targeted cloud evidence remains pending. This is partial old-worker engineering, not a claim that an arbitrary historical service cooperates with the new lock: concurrent legacy startup after the probe, missing/recycled PID identity, legacy state transfer, self-contained base-Python/stdlib/native packaging and actual hosted app interaction remain open engineering exits. DFG-002 durable server readback and DFG-008 unsupported form capability remain open. No private owner state, live account, actual job action, new permission or signing resource was accessed; final submit remains user-only.


### JCR-08 legacy journal continuity fence — 2026-09-26

Pre-existing legacy-service guard candidate `367336d7768c35bdf4539fff07de1e83fd9b0719` passed [targeted CI 36216663941](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36216663941): Ubuntu/hosted Mac 124 packaged/journal cases each, deterministic 25, browser/review 135 and privacy 7. No repeated unchanged-head or full dispatch.

Source review found a distinct release transaction bug: the new app's shared default runtime can be empty even though the candidate checkout or exact historical launcher's repository still owns a populated repo-local journal. Candidate health/compatibility against only the new path would silently strand existing tasks and saved answers. Before staging or service startup, this batch inspects only those finite legacy paths plus the historical packaged release runtime. Any state outside the selected guarded authority (including WAL, keys, service records or unclassified private artifacts) requires transactional migration; it is never read into diagnostics, copied opportunistically or deleted. Aliased/non-directory legacy paths refuse as unavailable. Same-authority explicit selection and empty regular lock-only locations retain their existing behavior.

Regressions use committed SQLite WAL task/event authority, private answer-key canaries and exact historical launchers in distinct repositories. They assert no candidate startup, unchanged original authority/app/key, no new staging/rollback and safe output. New cloud verification pending. This closes silent activation into a different empty journal only; it does not certify or substitute for legacy transfer. Transactional copy/compatibility/activation/old-writer retirement, independent base-Python/stdlib/native packaging, actual app interaction and DFG-002/008 durable server outcomes remain engineering debt. No owner environment, real private state, external application action, new permission or signing resource was accessed. Final-submit remains user-only.

Legacy continuity bounded diagnosis: cf1b23058700fdb38945f965d6a68357c19844a4 / CI 36217727614 hosted Mac completed 131 PASS / 1 FAIL. The existing symlinked-release contract expects untrusted_app_path; the new state detector ran first and returned legacy_state_unavailable. The app integrity check now runs before any historical state-path inspection or staging. The original gate/assertion is retained. The new in-bundle state fixture also checks the true immutable-source trust reason and direct state detection, retaining all WAL/private canaries and zero-start/no-app-change assertions. This is a product validation-order repair, not timeout/runner/harness suppression or a lowered integrity gate. Exact new candidate pending.

### JCR-08 encrypted answer compatibility candidate

Prior exact head `674e5612010b613b34074361f5e8fcae1f443229` / [targeted CI 36218005649](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36218005649) PASS: Ubuntu and hosted Mac packaged/journal132 each, deterministic25, independent review135 and privacy7. Current independent batch makes the install/rollback journal gate also prove that saved task-only encrypted answers remain readable with exactly the same owned private key and latest typed values. Equal encrypted database rows are necessary but do not prove decoder/key compatibility. Only a bounded regular owned no-follow mode0600 key and committed-WAL SQLite backup enter the disposable compatibility directory. Candidate queue/TaskAnswerStore initialization and read-only answer loads run there; no applicant worker, browser, credential/token copy or action runs. Ephemeral keyed expected digests avoid raw private values in args/logs/receipts; the live key and encrypted history remain untouched. Tests cover missing/malformed/public/symlink/FIFO key, unreadable ciphertext, decoder omission/guesses/type coercion, key rotation, distinct tasks and answer versions plus real packaged app activation refusal after empty health succeeds. New cloud evidence pending; not legacy-state transfer, independent base runtime, DFG-002 closure or JCR-08 certification. Final submit remains user-only.
