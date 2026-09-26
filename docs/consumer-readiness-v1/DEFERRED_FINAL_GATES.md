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


### JCR-08 standalone interpreter/stdlib/native staging — 2026-09-26

Exact `dad360070312fbe930441b13a167c08cc21e5f01` passed [CI36219990660](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36219990660): Ubuntu foundation and hosted Mac packaged/journal145 each; the retained native SELECT RAF reversion and new deterministic delayed selection/option-value rejection cases passed. The encrypted latest-answer/key/type migration regressions also passed both platforms. No unchanged-head rerun or full closure dispatch.

The next coherent batch provides an actual installer/CLI path for a complete standalone runtime, prepared once per affected cloud job from fixed upstream asset names and SHA-256 digests (astral-sh/python-build-standalone release20260924 / CPython3.12.14). Complete interpreter, standard library and pinned dependencies are staged and manifested. Only internal file aliases are materialized; host virtualenv configuration, host executable without owned base/stdlib, escaping/directory/loop aliases and nonregular payload refuse. A relocated `-I -B` subprocess requires app-owned sys.prefix/sys.base_prefix/stdlib/search/import origins, closed wheel metadata/RECORD pins and owned loaded native libraries (operating-system libraries are the explicit exception). A standalone marker is covered by the full runtime manifest and revalidated during staging and final-path health; neither a marker nor archive digest is claimed as signing.

Required Linux/hosted Mac positive fixtures exercise the actual installer with pre-existing task authority, remove checkout/prepared build runtime, poison startup variables, verify relocated ownership, packaged Playwright driver startup and authenticated service startup, exercise actual second activation and rollback preserving known-good/failed versions, then reject marker drift with unchanged task authority. Missing cloud runtime preparation fails this gate; no fixture substitution or hosted Mac skip. Existing legacy venv tests remain intact and do not gain standalone certification. New candidate evidence pending. The consumer distribution/default build choice, signed artifact channel, full round closure, transactional legacy transfer/retirement, hosted interaction and DFG-002/008 durable server recovery still require their own canonical exits; private owner/live evidence remains deferred.


### JCR-08 unsigned standalone app distribution batch — 2026-09-26

Exact `bdd6083088d767d5cfd5a7950341d736b4636bd9` passed [CI36221095500](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36221095500): foundation/hosted Mac success, Mac153 cases in137.38s. Independent runtime and actual two-version activation/rollback have affected engineering evidence, not full consumer certification.

The next coherent production path builds a standalone unsigned Mac app archive through the real staged installer, using only declared code and a separate empty build journal. A populated repo-local journal, key, profile or credential is neither migration input nor distributed content; existing source authority remains untouched. Archive inventory refuses known task/private-state paths, undeclared outer members and aliases/nonregular files. Streaming normalizes archive headers (no build-host user/path/timestamp); source/runtime/requirements/archive digests bind the manifest. Exclusive output creation and links cannot overwrite an existing artifact; the completion receipt is written last only after source/runtime rechecks. A failed candidate leaves no ready artifact and never erases unidentified concurrent output.

Required Linux/hosted-Mac fixtures actually build/unpack, delete the build checkout/runtime, poison Python configuration and verify relocated independent runtime plus authenticated loopback service health. Existing153 packaged/journal tests and all privacy/no-submit assertions remain; missing standalone preparation fails. Draft pushes run affected checks; cloud unsigned artifact build/upload runs only at the stable round candidate/main boundary with7-day retention. No signing, public release/deployment, owner device install, new permissions or paid resources. The receipt explicitly states unsigned / NOT_CERTIFIED / final-click actor user.

New candidate evidence pending. This provides an automatable distribution build path, not certification of the channel or user device. Signing/private live evidence remains EXTERNAL/FINAL_LIVE; transactional legacy state transfer/old-writer retirement, hosted app interaction and DFG-002/008 engineering remain open. JCR-08 stays IN_PROGRESS, final submit user-only.


## JCR-08 transaction writer-fence batch — engineering pending

Prior exact `c2deb358ffee0f9623c4e17c6c6e2290bf8d90e2` / [CI36222577088](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36222577088) PASS: foundation25 deterministic,137 browser/review,7 privacy,161 packaged/journal; hosted Mac161. Actual unsigned app build/unpack/relocated startup succeeded with no source private-state inclusion. Full round and signed/live-owner evidence are not implied.

Bounded updater audit found a separate writer: TaskQueue initializes schema/derived state under migration.lock without acquiring worker.lock. The app's guarded compatibility/activation transaction previously held only worker.lock. It now holds both nonblocking fences until activation/recovery finishes, refusing an active initializer promptly. Queue constructors now retain the migration fence through backup and the complete schema transaction (previously it ended after backup), preserving their existing blocking protocol and waiting until release; neither updater nor fixture starts applicant workflows or changes submission authority.

The app and state fences now inspect lock descriptors as owned ordinary single-link files before chmod/flock use; symlink/FIFO/directory/hardlink ambiguity refuses without reading payload, altering external permissions, staging or activation. Thirteen new regressions retain real WAL authority/schema, prove cross-process real queue construction waits through the guarded transaction, ensure second-lock refusal releases the worker fence, and exercise production install/rollback refusals and lock cleanup. Existing tests/budgets/timeouts remain unchanged. New coherent cloud candidate pending.

This closes an independent transaction concurrency defect only after cloud evidence. It does not prove old pre-lock writer retirement, transfer legacy private state, close DFG-002/008, certify hosted app UI interaction, or authorize real owner state migration/final submit. Those canonical engineering exits remain open.


## JCR-08 consumer admission retirement batch

Exact `da3e5b1c3130bf1986cc0b1794972bf4cb7b35e2` / [CI36224112822](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36224112822) PASS: Ubuntu25/137/7/174, hosted Mac174. The whole queue initialization/migration fence and13 added lock/concurrency regressions preserve the previous161 cases.

The consumer source-checkout UI still admitted the historical in-place Git writer. It now retains existing busy/task/OTP/mutation-drain fences and returns safe `legacy_update_retired` before any spawn. Packaged verified/unverified refusal and all auth/no-submit/privacy gates remain. Dashboard explains the unavailable update path without claiming release health. Five task-state cases and an actual authenticated loopback UI request repeated twice assert no writer, task/event mutation or updater artifacts. The historical drain-success assertion is explicitly superseded by the canonical retirement requirement: real mutation drains, zero Git writers admitted. Its concurrency setup/assertions and all historical legacy-engine tests stay.

This closes only consumer admission when its affected cloud gates pass. Direct legacy module/CLI invocation, transactional old-state transfer and process retirement remain ENGINEERING_DEBT; the complete old updater is not claimed retired. Existing missing/ambiguous/WAL legacy authority still refuses app activation. No live migration, new permission/channel/payment, unchanged-head rerun, full certification or final submission performed.


### Consumer retirement follow-through — second concurrency oracle

Exact `146433cbc221fc5be9defbe2e4917ac1fc11642a` / [CI36225082930](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36225082930) hosted Mac174 PASS. Foundation passed24 cases and failed the historical safety-check/RESUME race because it required a successful Git updater spawn, now explicitly retired from consumer admission; subsequent foundation groups did not run. No runtime failure or timeout workaround is inferred.

This existing test remains an actual concurrent production-Supervisor test. It now asserts a competing local RESUME stays blocked with unchanged paused task and no receipt while the safety decision owns the command lock; after safe refusal it requires zero spawned writers, exactly one accepted revision, durable matching receipt and idempotent replay. A refused retired update must not freeze normal controls. This explicitly supersedes only the old successful-spawn/FENCED outcome, keeping and strengthening serialization/no-second-writer/revision requirements. Product runtime and all other cases unchanged; new affected cloud evidence pending.


## JCR-08 public/module legacy updater retirement — cloud candidate pending

Exact `91b629d6dbf76e0e62cc389ff1ffd780541dd9d2` / [CI36225534283](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36225534283) PASS: foundation25 deterministic,137 browser/review,14 update/privacy/admission and174 packaged/journal; hosted Mac174. The actual concurrent RESUME safety oracle is proved without admitting the retired Git writer. Full round certification remains unrun.

The historical public `spawn_update` and `perform_update` paths now refuse before runtime/lock/Git/service/applicant state access. The module CLI parses its historical flags but exits before claiming an inherited descriptor, creating runtime state or stopping/restarting a service; `--restart-only` cannot bypass retirement. Actual child-process regressions preserve code, caller descriptor identity/mode/payload and missing-state absence. Parameterized public-entrypoint regressions preserve populated task/WAL/key/lock canaries and refuse any route to the historical engine. Hosted Mac additionally runs these entrypoint checks.

All eight existing engine safety, provenance, timeout, Git-mutation-failure and concurrency cases remain through explicitly private retained engine functions. This is a documented replacement of the public mutation contract, not deletion of historical assertions; complete local operations coverage is now part of the affected Ubuntu gate. Production consumer/public/module paths cannot dispatch the private legacy engine. Private compatibility code physically remains, so the old implementation is not claimed removed or a signing-certified updater.

This does not establish retirement of a process launched by an older installed version before this change, perform real private-state migration, prove full app interaction, close DFG-002/008 or certify a channel/device. Existing transfer/authority ambiguity remains fail-closed and ENGINEERING_DEBT; signing/private/live evidence remains deferred owner work. New candidate evidence pending. No live applicant action, permission/payment, unchanged-head rerun or final submit.


## JCR-08 relocated production app entry and headless consumer interaction

Exact `6b1cd95205ce18177de3fcfe7617d5e31ed507a1` / [CI36226364678](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36226364678) PASS: foundation25/137/43 complete local operations/1 diagnostics/174 packaged-journal; hosted Mac174 plus7 retired entrypoint cases. Public/module legacy Git admission is now proved retired while private historical safety engines remain retained and inaccessible through those routes. Full round certification remains unrun.

The existing full unsigned archive regression already proves relocation after removing checkout and prepared runtime, dependency/source provenance and absence of applicant state. This next coherent batch continues through the exact production app executable and owned isolated interpreter into the actual supervisor, ticket/session authentication and full consumer HTML/JavaScript, with actual hosted headless Chromium. The oracle substitutes only the normal browser-opening transport using BROWSER; no fake candidate CLI, patched service, injected UI, unverified health fixture or direct product-method calls. Both Ubuntu and hosted Mac must run this complete path. Keyboard open/close diagnostics, read-only state/readiness, actual update-button refusal, missing-cookie and consumed-ticket negatives, zero external browser requests, two real app opens reusing the same writer PID, exact loaded digest and safe checkpoint stop are required. Evidence output stores only booleans. All existing checks and fixture content stay; the archive is built once per existing test rather than repeating packaging.

This closes a missing production-entry interaction proof only if its new cloud gates pass. It does not claim a native single-window Mac shell, tasks/security/final-review/scale/expired-session interaction completeness, real old-state transfer or pre-lock process retirement, DFG002/008 closure, signed build or owner-device acceptance. Those independent engineering exits remain actionable; private/live/signing evidence remains deferred. No live application, new payment/permission, certification rerun or final submit.

## JCR-08 durable task-journal backup prerequisite — coherent candidate

Fresh remote main753688f0a5cb8b69463ff747b47e32c86335c969, sole writerPR20/head70dc3093d6fd2fbb86a56799bcb3903e2bda1d25. CI36228856436 foundation108367999406 and hostedMac108367999293 PASS, including production relocated app/owned runtime/real service/headless UI entry path;174 packaged/journal plus7 retired updater entry cases on Mac. The draft full-round test job was intentionally skipped, so this is affected engineering evidence, not JCR08 certification.

The next coherent candidate adds an internal durable journal/key backup primitive. SQLite online backup captures committed WAL without migrating the original; destination uses a single standalone DELETE-journal database. Owned ordinary single-link source DB/key, private exclusive output, current worker and schema fences, live registry refusal, complete SQLite integrity/schema/table snapshot, real typed answer decryption against the exact copied key and source inode/key readback are required. Payload is flushed before exclusive manifest-last publication. Failure cleanup removes only known same-inode artifacts and preserves unknown concurrent payload or replacements.

Thirteen added synthetic cases preserve every original task/event, every encrypted history row and schema; prove current real candidate queue/answer decoder only on a disposable copy; require private permissions, exact payload hashes, no answer/profile/token plaintext, immutable durable payload on compatibility/repeat, live writer/key/path refusal, publication failure with unknown artifact retained and rotation refusal with the actual rotated source key left untouched. Existing fixtures/assertions/deadlines remain. New-head Ubuntu and hostedMac targeted evidence is pending.

This is a scoped point-in-time backup prerequisite, not complete transactional state transfer: activation remains NOT_AUTHORIZED and pre-lock legacy-writer retirement remains NOT_CERTIFIED in its receipt. It copies only the task journal and encrypted answers key; applicant profiles, authentication tokens, logs and unrelated private files are excluded. No service signal, private owner state access, restore, live migration or final submit occurs. Full legacy transfer/retirement, native single-window shell and DFG002/008 remain actionable independent engineering; signing/real device/private/live evidence stays deferred. No full certification or unchanged-head CI retry.

## JCR-08 bound durable-capsule candidate preflight

Exact df57e01fdbc85fb2aaa1b5bec29e0930d2e33ba7 / CI36258709659 PASS: Ubuntu foundation108450288700 and hostedMac108450288860;187 packaged/journal cases (including13 durable backup faults) plus7 public/module updater retirement cases on Mac. Draft full-round suite intentionally unrun; this is affected engineering evidence.

The next coherent batch closes a transfer/rollback prerequisite: a saved backup manifest cannot validate itself after corruption or replacement. verify_task_state_backup requires the original trusted staging receipt supplied independently by its caller; exact format/scope/NOT_AUTHORIZED activation/NOT_CERTIFIED retirement, typed counts and expected DB/key digests must match. It refuses extra/missing inventory, aliases/hardlinks/FIFOs/nonregular/public/unowned payloads, malformed/oversized receipts and non-standalone WAL headers before running candidate code. All reads are bounded for metadata/key, no-follow, and database snapshot is immutable/read-only without auxiliary files. It checks actual integrity/all authority tables/latest typed answers and rechecks root/file identity, bytes and inventory after the read.

task_state_backup_candidate_compatible proves the actual candidate queue migration and answer decoder only against the existing disposable private SQLite/key copy, then revalidates the unchanged durable capsule against the same frozen external receipt. No service, worker, browser, credential copy, restore or original migration runs. Candidate loss/type coercion is refused; a mid-probe payload change remains visible and is never repaired or deleted.

Twenty-eight added synthetic cases cover actual current candidate/encrypted-history and no-answer/no-key positives;15 drift/alias/hardlink/FIFO/public/manifest/key/database negatives that forbid candidate execution;7 receipt authority/type/count negatives;2 real candidate decoder loss/type-coercion negatives;post-probe key drift;and a matching-receipt WAL-header negative. Original task/event/key authority and durable payload immutability are required; previous tests/fixtures/deadlines stay. New stable-head cloud evidence pending. This is verified handoff preflight, not actual legacy transfer, restore permission, pre-lock writer retirement, native single-window acceptance, DFG002/008 resolution or JCR08 certification. Those remaining engineering exits stay actionable; owner/device/signing/private/live evidence remains deferred on its own dependent paths. Final submit remains user-only.


## JCR-08 durable capsule in real install/rollback transaction — candidate pending

Exact13c0400031279ad0500347b91b591fba3be2f0eb [CI36260197290](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36260197290) PASS: foundation108454426965 and hostedMac108454427133,215 packaged/journal cases each including all28 externally bound backup/decoder handoff cases; Mac also7 retired updater cases. No draft full-round certification.

The next coherent batch connects that verified capsule to actual install and rollback entrypoints. Existing app-directory/worker/schema locks stay held from admission through final-path health/recovery; an internal locked backup primitive avoids reacquiring or temporarily releasing either task fence. Public stage_task_state_backup retains path validation and exclusive-lock behavior. Empty/new journals keep the existing compatibility path. A populated selected journal is backed up privately as standalone DELETE SQLite plus exact answer key, published receipt-last, then checked with the original returned receipt and candidate's real schema/typed decoder on a disposable copy before any bundle rename. Complete backup evidence is returned on success and later refusal/recovery, and completed capsules are retained rather than erased to hide a failed update. No profile/token/log copy and no backup restore.

Eight added actual entrypoint/fault cases plus strengthened four retained real update cases cover original WAL/tasks/events/complete encrypted history/key, candidate destructive row/schema/decoder refusal, receipt publication with unknown artifact preserved, capsule key drift before candidate execution, rejected candidate, successful real packaged rollback, and final-path health failure restoring the original app while retaining the failed candidate and verified capsule. Existing live-worker/schema/legacy-service/continuity/alias/concurrency and all capsule negatives remain. New exact-head Ubuntu/hostedMac targeted evidence pending; no unchanged-head/full rerun.

This closes an integration prerequisite only after cloud proof. It does not transfer to a new state root, retire a historical pre-lock process, restore an older journal over newer confirmed task values, implement the native single-window shell, close DFG002/008 or certify JCR08/device/channel. Backup activation remains NOT_AUTHORIZED and legacy writer retirement NOT_CERTIFIED; source state remains unchanged authority. Signing/private/live evidence remains deferred only on its dependent paths; final submit user-only.


## JCR-08 truthful expired-session consumer interaction — coherent candidate

Exact dffcf19b35ac1eadd4730cfd22cbe8d1413f2bd1 / CI36261320896 PASS: Ubuntu foundation108457568287 and hostedMac108457568497,223 packaged/journal cases each including the8 actual capsule/install/rollback failures and successful rollback; Mac also7 retired updater entrypoints. Full-round suite skipped while draft; JCR08 remains IN_PROGRESS and consumer NOT_CERTIFIED.

Independent consumer engineering now handles the supervisor's existing authenticated UI API401 boundary explicitly. The first401 latches a permanent notice on the old page, marks connection/session truthfully, retains unsent non-OTP field/form/message values locally as read-only selectable text, disables old actions, clears cached private review and diagnostics, and refuses further reads/mutations from polling or old action callbacks. A late formerly authorized response cannot repaint private values or reopen diagnostics. There is no automatic authentication refresh, ticket reuse, action replay or task mutation. Message input is cleared only after confirmed successful chat response; refused/unconfirmed messages remain local, with truthful status. Existing OTP nonretention and user-only final submit remain.

Six additional targeted cloud browser cases cover401 from state/readiness/diagnostics/task command/chat and an outstanding diagnostics200 arriving after another request reports expiry. They require durable notice/focus, exact unsent values, cleared private review, all action controls disabled, zero subsequent request even through existing callback entrypoints, no clipboard or external request, and no POST beyond the refused original command/chat. The existing actual relocated unsigned app/owned runtime/service/browser oracle is strengthened on both existing app opens: delete only its synthetic HttpOnly cookie, invoke the actual diagnostics button, require real supervisor401, retained form/message and disabled actions, expired HTML admission and consumed ticket refusal. No extra app build or removed fixture/test.

The existing foundation diagnostics browser step expands its selection only by expired_session; all prior checks and both hosted platforms' real packaged-entry oracle remain. Server expiration policy/one-use ticket/HttpOnly/SameSite/Host/Origin/token gates are unchanged. New stable head affected proof pending; no unchanged-head rerun/full certification. This closes the local consumer session-loss interaction only after cloud proof; native single-window shell, full task/onboarding/final-review/scale, new-root legacy state transfer/pre-lock process retirement and DFG002/008 remain engineering exits. Signing/owner-device/private/live gates remain deferred only on their dependent paths.
