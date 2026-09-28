# JCR-08 production form driver capability matrix

The active registry selects `GenericWebAdapter` for every supported HTTP, HTTPS and file URL. The `boss_campus` route label identifies a target family for task scoping; it does **not** select or certify a BOSS campus form driver. `SchneiderBossAdapter` remains outside the production registry and its historical direct API writes do not count as JCR-08 support.

| Route label | Active production adapter | Read-only form observation | Certified independent draft readback | Certified page advance | Certified repeated rows | Certified attachment readback | Certified iframe, shadow or virtualized controls |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `generic_web` | `GenericWebAdapter` | Available, guarded by browser ownership | No | No | No | No | No |
| `boss_campus` | `GenericWebAdapter` | Available, guarded by browser ownership | No | No | No | No | No |

`declared_driver_capabilities(url)` exposes the same production boundary for diagnostics. The generic adapter's synthetic fixtures exercise native text/select, exact ARIA choices, dependency redraw, rejection of ambiguous rows and uploads, and local question context. Those tests prove safety behavior in the fixture; they do not certify an external ATS or a persisted draft. A unique DOM row token, visible input value, save click, or elapsed time does not become a server readback receipt. Final submit remains user-only.

To add a supported site path, register a dedicated production driver, prove exact target and applicant account, reconcile canonical row and attachment identities, supply independent server draft readback, and pass the affected D/G/H synthetic and fault cases on the exact PR head. Until then, unsupported controls and uncertain writes remain blocked for that task; unrelated engineering continues. Real applicant and site evidence remains DFG-007, separate from DFG-008 engineering.


## Bounded generic observation completeness

The generic metadata snapshot retains its existing performance bounds: at most350
matched controls (before visibility filtering, preserving ordinal locators) and200
options per visible enabled native select. These bounds are not evidence that a
larger form is complete. A partial collection is explicitly marked
`collection_complete:false`, bound into its observation digest, and rejected before
planning/application start or field writes. Bounds are rechecked before each field
action, after dependency redraw, and at post-fill validation; an oversized redraw
stops remaining actions. Hidden prefixes cannot turn an incomplete page into an
empty application-entry page. No fixture or limit is reduced to manufacture PASS.

Exact-limit native selection and synthetic351-control/201-option/hidden-prefix/
dynamic-redraw cases cover this boundary. Their bounded collection cases passed the affected exacte949107 review/browser
suite in CI36283653134; this does not certify a site driver. This remains a generic capability refusal and
DOM readback boundary, not a new certified external driver, server draft receipt,
page-advance capability, or completed DFG-008.

## Current disabled-choice admission boundary

Native options, disabled optgroups and inherited ARIA-disabled choices must be
currently admissible immediately before a generic selection primitive. An exact
label is insufficient; a disabled duplicate does not disambiguate another option.
Recheck after the ownership boundary and, for ARIA choices, after opening the
component. A journaled uncertain/refused action still retains UNKNOWN_OUTCOME and
blocks blind replay; an opened component is not presumed effect-free.

Ten actual isolated-browser cases and two private field-journal cases accompany
this batch. Their new exact-head cloud result is NOT_RUN. Existing enabled choice,
dependency redraw, DOM readback, independent server receipt and final-submit
user-only boundaries remain. This is DFG008 mitigation, not certification or closure.

## Reactive opaque-form redraw fence

After one generic field has a local DOM readback, a controlled redraw may add an
iframe, open shadow form or opaque custom element before the next field write.
The active adapter now reobserves that structure at this boundary. An unsupported
component or failed structural observation revokes the earlier journaled DOM
readback to UNKNOWN_OUTCOME and stops before the next control. Two isolated
browser/private-journal cases cover iframe and shadow insertion, untouched second
control, no submit and blocked replay. Exact new-head CI is PENDING. This is a
DFG-008 mitigation only; it does not certify an external ATS driver, server
persistence, DFG-002 continuation or final submission.
