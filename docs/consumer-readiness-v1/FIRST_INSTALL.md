# Native first-install entry — engineering candidate

This increment targets the Terminal-free entry portion of A-01. It is not all
A-01 onboarding, migration, signing, owner-device acceptance or consumer
certification. It is an unadopted candidate until its own exact-head and
post-merge checks complete. Older delivered builds without `native-entry` do
not gain this behavior from documentation.

## Intended consumer route

Open the verified, extracted native app from its download location. The app's
owned isolated Python and source-bound Cocoa image present a local first-install
choice before starting a task service or issuing a dashboard ticket. No checkout,
virtual environment, compiler, Terminal command or download is required at this
step. macOS trust/security prompts remain for the owner to assess; no protection
is disabled.

The fixed destination is the current user's `~/Applications/AI 投递经理.app`.
Choose an old project folder for continuity checking, explicitly declare first
use without old tasks, or cancel. The chosen old folder never becomes an install
source, destination, task root or migration instruction. Selecting any old
checkout conservatively stops before activation, including an apparently empty
folder, until durable legacy continuity/migration is supported. Known conventional old
state is still checked when first use is selected. Cancellation performs no
installation and creates no task authority.

A populated, aliased, unreadable or ambiguous old authority stops installation.
So does existing default task state without an admitted target application.
There is no automatic private-data transfer, key rotation, profile import,
cleanup or choice of an empty alternate journal. Arbitrary undeclared old
checkouts are not discovered by filesystem search.

Existing target apps are not replaced by this entry. Open the existing app and
use its existing update path instead. Do not delete old state or choose an empty
folder to bypass continuity refusal.

## Transaction and evidence boundaries

- The running downloaded bundle is captured before the native choice; its full
  source/runtime/native/launcher identity is rebound through staging and before
  activation/reopen. A changed source cannot substitute a new approved candidate.
- Strict first-install repeats occupied-target and orphan-state admission under
  the existing app/native/worker/migration transaction fences. The update path
  retains its ordinary replacement behavior separately.
- Final first-install activation uses Darwin `renameatx_np` with `RENAME_EXCL`,
  anchored to an admitted directory descriptor. Unsupported filesystems fail
  closed, with no ordinary-rename fallback. [Apple's public header](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/stdio.h)
  defines the flag and API; [Apple's volume capability documentation](https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming)
  describes filesystem support.
- The chosen source is retained. After successful activation and release of all
  transaction/prompt resources, only the installed app's owned Python/source is
  asked to open. `reopen_requested` is not observed UI readiness. A later identity
  uncertainty is not reported as proof that nothing was installed.
- Strict first-install rechecks selected/conventional and default state after
  final-path health and before requesting reopen. An unconfirmed activated app
  remains in place, without replace-capable recovery moves. A bounded private
  inherited pipe binds the initial installed child to the exact activated
  bundle; that child rechecks empty default/conventional continuity under app
  and state locks before service startup. No selected path is passed.
- Before activation, a mode-0600 finite pending fence is fsynced in the application
  directory. It contains only a bundle tag and pending/complete state. Interrupted
  or refused admission therefore also blocks a later ordinary Finder open. The
  exact initial child, or an explicit pending-first-use recovery in builds that
  include it, can complete admission only at guarded owned service startup.
  See [pending recovery](PENDING_FIRST_USE_RECOVERY.md) for its bounded empty-state
  contract and partial-initialization failure boundary. Aliases,
  hardlinks, public modes, malformed records and uncertain writes fail closed.
  No fence is deleted, no private data is imported, and a completed fence does
  not independently authorize an application payload.
- Positive selected-legacy continuity remains unsupported; the route refuses
  before activation rather than relying on one-shot path checks.
- Historical delivered native launchers are preserved with their own source,
  rather than rewriting an old CLI to call an unsupported new entry command.
- Native replies use a finite schema, reject duplicate/extra fields and controls,
  and have bounded byte counts and a monotonic deadline, including partial lines.
  Local paths travel through the private pipe, not logs or model input.

The exact managed pathname can continue the normal installed-app path after
known continuity checks. That does not certify a manually copied installation,
or discover undeclared old checkouts. The supported first-install journey opens
the delivered app from its download location and uses the explicit native flow.
Manual-copy acceptance remains separate evidence, not an inferred PASS.

## Verification status before publication

- Focused routing/continuity/protocol cases: 75 passed, 7 declared Mac-only skips.
- Complete native-host owning file on Linux: 143 passed, 16 declared Mac-only skips. Syntax/whitespace checks pass.
- One pre-existing copied-runtime activation test fails identically on clean
  adopted b94af733 and this cloud candidate (`attempts == 1`, expected 2). This
  local result is retained; it is not a full owning-suite PASS or a reason to
  weaken the hosted assertion.
- Added hosted journeys use complete native bundles and actual standalone
  runtime: source mutation during probe/copy must refuse; a real static Cocoa
  prompt is observed, then one synthetic fixture intent drives actual first
  installation and native reopen; an actual legacy-command-contract bundle must
  remain launchable through the new installer. These have not run locally.
- The positive intent is explicitly test-supplied after a real cancellation
  oracle. It does not certify the physical button, picker or owner confirmation.
- Existing relocated-launch fixtures use their isolated HOME/Applications, while
  preserving all original payload, private-state, native UI and no-submit
  assertions. There is no first-install bypass via the smoke flag.

Corrected-head hosted full gates and exact-main verification remain required.
The previous adopted b94af733 safe-refusal repair has its own passing evidence;
it does not validate this larger native-entry candidate. Real migration,
credentials/permission onboarding, device/signing/site acceptance, 24h soak and
five normal-use days remain open as applicable.
