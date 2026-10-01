# Deferred anonymous preparation design

**Status: partially implemented; live execution remains disabled and uncertified.**

The separate deterministic mapper, private task-value review, one-pass kernel and
synthetic safety oracles are described in [the current implementation checkpoint](QIYUNFANG_PREPARATION_KERNEL.md). Durable live approval, lifecycle proof and safe
human transition remain gates; the generic account/draft executor is unchanged.

Assessment baseline: `fce8f0bf54778165f29e329e136130ae5ca4dec3`, 2026-09-30.
This does not authorize real applicant data transmission, page writes, uploads,
CAPTCHA handling, agreement acceptance or submission. The current release keeps
local profile editing and task preparation as its bounded usable capability.

## Why it cannot be a flag on the generic executor

The production registry selects `GenericWebAdapter`. Live
`ApplicationExecutor.run` checks account identity before form writes, and READY
requires independent server-draft/readback evidence and a current review
certificate. Worker authorization, owned browser fingerprints, document fences
and field journals do not establish that a field event has no external effect.
None of these existing boundaries should be weakened for an anonymous page.

A fresh read-only observation of the [official Qiyunfang page](https://www.qiyunfang.com/h-col-124.html)
opened its empty application popup for 应用实施工程师. No applicant value was
entered. The popup had no native form element; text inputs lacked stable IDs,
names and associated labels; native required attributes were false despite
visible asterisks. Role1/role2 repeated 31 option labels in separate groups
(observed names M1567R8 and M1567R13). These details are dated observations,
not a durable external-driver contract.

Input/change/blur/click/timer or framework handlers can transmit or submit via
fetch, XHR, navigation, beacon or other transports without a final button click.
A submit-event handler, blocking only POST, blank CAPTCHA/ID/privacy fields, or
temporarily taking the same document offline is insufficient proof. Reconnection
can release delayed effects. A network/side-effect boundary must be independently
proved before enabling real writes; absent that proof, the site stays unsupported.

## Separate future capability

1. A dedicated one-shot anonymous-preparation runner and preparation records
   associated with an existing task. Do not add an account-bypass option,
   manufacture an account match or register a falsely certified draft driver.
   `PREPARED_UNVERIFIED` is a separate preparation result, never READY or a
   server-persistence/application-completeness claim.
2. Explicit runtime user approval bound to task ID/revision, exact company,
   role and URL, original profile content/version, resume version, selected
   data categories and exact mapping plan. Bind driver/form version, complete
   control/options structure, owned browser process/tab/document, UI session
   and a short-lived nonce. Show actual values privately before approval.
3. Atomically consume approval before any possible effect. Model/chat choices,
   profile import, local checklist views and existing `live_authorized` must not
   create this approval. No blanket automatic consent or background retry.
4. Deterministic mapping only for explicitly approved routine fields. Role1
   may be included only as an additional explicit exact choice. Role2, ID,
   CAPTCHA, privacy/legal declarations, uploads and final submit have no write
   capability. Missing/conflicting facts, education representations, graduation
   options, language certificates or ambiguous city choices remain manual.
5. Reuse the exclusive browser-work fence; record value-free intent/outcomes
   before each primitive. Any target/task/profile/resume/document/control or
   option drift stops remaining writes. Death, timeout, disconnect or uncertain
   effects become UNKNOWN_OUTCOME with read-only reconciliation, never restart,
   Resume, OTP or manager-driven replay. Raw values remain local/private.
6. Explain that values were only observed in one document. A permanently inert
   local preview is possible, but does not count as filling the live website.

## Required acceptance evidence

- A realistic independent local-server replica of this popup, including
  unlabeled controls, duplicate role labels, visual required markers and all
  protected controls; correct approved mapping with protected controls untouched
- Wrong/stale/cross-task scope, target/group swaps, hidden/disabled/ambiguous
  controls, duplicate approval, concurrency and expired session produce zero
  new writes; exact nonce consumption survives process restart
- Crashes around intent, primitive, readback and result persistence never replay;
  navigation, popup replacement, reload and owned-process change stop execution
- Adversarial field/timer/framework handlers attempt submission through every
  supported transport; the independent server records zero application creation,
  including after any handoff or reconnection
- Retained/final-field redraw invalidates earlier evidence; diagnostics, journals,
  model context and browser storage contain no raw private values or resume bytes
- Existing account/server-draft/READY/no-submit/recovery suites stay intact;
  packaged/native dismissal, restart and update/rollback preserve the fence

Planning estimate, not a benchmark: 12–20 engineering hours for separate runner,
approval/state/mapping/lifecycle integration, plus 8–16 for adversarial and native
verification. Side-effect-boundary research is additional, plausibly 1–3 days,
and may still end with the site unsupported. No per-use model call is necessary;
development and bounded CI dominate. This is future work, not a release promise.

## 2026-10-01 implementation checkpoint

The public read-only bootstrap now independently matches the complete original
empty 90-control form and seals a source/activity receipt on Linux installed
Chrome and Mac bundled Chromium. Research run36865769165 records zero
unclassified requests while retaining all 54/55 denied background requests.
The dedicated owned-browser, one-shot approval, field journal, disposal and
opaque final-slot components have synthetic evidence; none invent an account,
server draft, READY state or verified application.

The next private UI surface selects routine fields and checks the current real
public form through a headless disposable browser. It has cookie-only exact-
Origin open/cancel/status routes, no field-approval endpoint, no returned
approval nonce, and hardcoded no-write/no-submit capabilities. Exact task,
profile/resume versions and selected values are rechecked; expiry, cancellation,
owner closure or retirement clears the private view. No hand-written JSON is
needed. This is still a read-only checkpoint: user-data filling requires native
runtime/device admission, and resume/CAPTCHA/legal/human-final stages remain
separate unresolved product gates.
