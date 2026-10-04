# Explicit replacement of a missing managed resume

This bounded JCR-08 / A-07 increment addresses a reproduced consumer block: an intact app-managed profile could not open in the editor after its managed resume file became missing, even though the existing explicit replacement API could already create a new immutable version.

Only the authenticated editor and explicit reconcile surface may request the bounded repair projection. The default reader used by manager task admission and publication readback stays strict. A missing managed leaf is recognized under the existing private directory descriptor, after verifying the selected managed profile and exact resume metadata. Missing ancestors, aliases, hardlinks, public/foreign entries, corrupt bytes, malformed metadata and external paths do not enter this repair route.

The Chinese repair view prioritizes a new resume selection and places the retained basic facts behind a read-only disclosure. Nothing is saved until the user chooses a new file and explicitly saves. The `replace_missing` action requires the prior exact profile/settings versions, bounded new opaque bytes and no fact edits. It repeats descriptor-anchored absence checks before and around immutable publication and settings selection. An arriving entry is never reused, overwritten or deleted. A post-publication failure retains honest uncertainty/reconciliation behavior; it does not claim rollback or repeat the save automatically.

New resume/profile versions use the existing publisher and previous-slot history. Original profile bytes, facts, provenance and existing task profile references remain unchanged. Existing tasks continue to use their old version, so replacing a missing resume for future tasks does not silently repair or rebind those existing tasks. No legacy migration, document parsing, credential setup, external upload or real application is added.

## Verification

The initial GUI-state regression failed on adopted main7770efb3 with `profile_editor_conflict`. Local validation currently passes31 new boundary cases and588 owning backend/workflow cases with132 subtests. Coverage includes strict task admission before replacement, explicit reconciliation, immutable fact/history retention, stale versions, concurrent saves, pre/post-switch write faults, missing-leaf arrival, alias/hardlink/private-mode refusal and authority replacement. The strict failure readback may conservatively report uncertainty even when injected failure leaves old settings unchanged, because the old selection still has a missing resume.

Twelve new browser cases cover explicit intent, read-only facts, cancellation, session expiry, reconciliation, malformed projections and no replay after uncertain saves. The real installed-DMG journey also simulates loss of a known synthetic resume, replaces it through the installed UI, verifies paused task bindings and facts, then reopens and updates the installed app. A synthetic hosted screenshot is retained for pixel review. Browser/Mac cases have not been claimed as locally run: the established local browser/runtime limitations remain.

Independent source/pixel review and the existing draft/full/exact-main gates remain required before adoption. The monotonic bundle sequence advances to4. This remains unsigned / NOT_CERTIFIED engineering work, not completion of all A-07 cases or public consumer delivery.
