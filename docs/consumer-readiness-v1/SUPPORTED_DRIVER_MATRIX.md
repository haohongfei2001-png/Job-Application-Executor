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
dynamic-redraw cases cover this boundary. Their current candidate result is
NOT_RUN until exact-head cloud CI. This remains a generic capability refusal and
DOM readback boundary, not a new certified external driver, server draft receipt,
page-advance capability, or completed DFG-008.
