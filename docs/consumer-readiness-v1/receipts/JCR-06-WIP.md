# JCR-06 engineering checkpoint — IN_PROGRESS

Canonical package: `JAE-CONSUMER-READINESS-v1`. Sole writer: draft PR #16, `feat/jcr06-structured-forms`. This is a work receipt, not JCR-06 closure or an acceptance PASS.

## Verified synthetic subevidence

- `FormObservation` and `FillPlan` are separate. The copy-safe structural summary omits field values and labels. Hidden required controls, duplicate selectors, unsupported components and site validation indicators are observed. A parent write triggers bounded fresh observation; new required fields are either resolved from the canonical profile or block.
- Duplicate generic selectors do not write the first repeated row. Read-only observation counts repeated rows with hashed site markers and flags reused/ambiguous row identities without copying raw tokens into structural receipts. Same-title canonical project records require distinct `record_id` bindings. An observed but unparsed resume research/project section blocks project coverage. English research/project headings are classified.
- A supported ARIA combobox needs one controlled listbox, exact visible choice and post-selection readback. Missing virtualized choices remain unsupported. Date controls reject a month-only fact. A scoped salary quote requires a user-confirmed exact target, currency, annual/monthly period, tax basis, explicit unit and site step/bounds.
- Resume and photo slots are distinct. A canonical file hash, declared MIME/extension and site size limit are checked before a site upload driver may act. Generic file inputs remain blocked because browser selection does not prove upload completion or retained draft state.
- A generic DOM value or clicked save button does not certify persistence. The loopback SyntheticATS test compares a server draft and revision against a golden draft, and a wrong-value canary fails. A missing proof creates `draft_persistence_unverified`, which cannot be resumed blindly after restart.
- Before a supported driver clicks a multi-page Next control, the executor now requires an independent draft readback and records only its evidence level and revision. The two-page loopback ATS checks the first page's server draft before navigation and the combined draft at final review. Fresh isolated browser contexts re-enter both pages and observe values restored from the server draft. A server that discards the first write blocks before the second page is opened; the synthetic submit counter stays zero. Generic sites with no independent readback stay blocked at that navigation.
- General standing consent flags no longer authorize unseen statement text or another target. A default checked declaration without exact user-confirmed text and target digests blocks.

Local isolated/headless full suite after the multi-page draft preflight: **406 passed in 81.81s**. The focused structured forms and SyntheticATS regression passed **19 tests**; an additional isolated generic Next negative case passed separately. The attachment read-error classification passed its focused regression. GitHub exact-head CI and review status must be read from PR #16 for the current head; earlier heads passed both CI jobs.

## Still open

D-01–D-22 are not certified as a set. Production repeated-row add/delete/reorder recovery, controlled React/Vue and virtualized list fixtures, dependency-sensitive location/major drivers, supported site upload completion receipts, broader multi-page recovery and per-action fault injections, and real platform evidence remain. The generic unsupported paths are blocked. DFG-002 browser write reconciliation is engineering debt; it is not a final-live owner item. No owner action or automated final submit is authorized by this checkpoint.

No migration is introduced by this checkpoint. `FieldResolution.record_id` is optional for old plans; an older writer that ignores the new proof gates must not be used to resume a task blocked for uncertain draft or browser outcome.
