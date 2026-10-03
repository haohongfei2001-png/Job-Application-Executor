# Basic-profile first onboarding

This bounded JCR-08 / A-01 increment leads a new consumer into the existing local basic-profile and resume editor. The dashboard offers **开始填写** only when the current readiness response explicitly reports no configured, existing or loadable profile. Opening the editor remains an explicit user action; the status check does not request private field values.

The ordinary profile-settings path asks the user to fill basic fields and choose a resume. Importing an existing JSON profile remains an optional disclosure. Closing a dialog clears its selections; delayed settings replies cannot open the editor after Close, Escape or session expiry. A superseded readiness response cannot replace a newer setup observation.

The existing profile authority, schema, save/reconciliation endpoints, privacy controls and future-task binding are unchanged. Saving local data is not a completeness certificate, model connectivity check, permission grant or authorization to transmit data to an application site. Existing tasks retain their original profiles. Legacy transfer is not added.

## Verification boundary

The installed-DMG journey now exercises the actual installed service and dashboard with a fresh synthetic HOME: explicit basic-name and opaque-resume save, readback, close/reopen, and preservation across the already-tested increasing-version update. The GUI interaction uses an isolated browser; native entry and Cocoa installation remain covered by the same journey. No owner data, live application or physical owner-device evidence is used.

Local backend validation passed 218 profile-editor/import tests and 20 workflow contract tests with 132 subtests. Browser assertions are pending hosted execution: the pinned local browser was absent, its official download returned invalid ZIP bytes, and the installed system Chromium could not create its singleton socket. The cloud browser separately blocked the loopback preview URL. The unchanged base also fails the local legacy candidate-start fixture because its copied Python resolves its standard library to missing /install/lib/python3.12 and cannot import encodings. These limitations are not test passes and do not justify production workarounds.

This release advances the monotonic bundle sequence from 2 to 3. Draft and full hosted owning gates, independent source review and exact-main verification remain required. The installer remains unsigned / NOT_CERTIFIED; public signed delivery, old-data migration and final personal acceptance are still open.
