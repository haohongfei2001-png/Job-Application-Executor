# Qiyunfang preparation kernel checkpoint

Status: implementation checkpoint, **not live-enabled / not certified**.

## What can be used now

The existing local preparation dialog can explicitly reveal the original task's
routine proposed values. This is a private, no-store UI read. It does not open,
fill, upload to, or submit on a website. Closing, hiding, refresh, task/version
change, session expiry and page exit clear the values. No handwritten JSON is
needed: future-task profile editing remains available through the existing UI.

The preparation kernel is separate from `GenericWebAdapter` and
`ApplicationExecutor`. It is intentionally not in the production registry and
has no HTTP action endpoint. Its result is `PREPARED_UNVERIFIED`, never account,
server-draft, READY, upload, or submission evidence. The current app route returns
`AWAITING_LIVE_PREFLIGHT`; it does not mint a live approval.

## Independent public observation

On 2026-10-01, the official recruitment page
<https://www.qiyunfang.com/h-col-124.html> listed 应用实施工程师, 武汉, 校招, 应届生.
Opening the empty application popup exposed 19 ordered rows and 90 input controls.
The value-free capture is
`tests/fixtures/qiyunfang/public_form_observation_20261001.json`. It was captured
independently of the implementation constants. No applicant value was entered.
FileList occupancy could not be observed by that inspector; the capture preserves
that limitation rather than claiming an empty FileList was verified.

The popup uses `data-formid` rows under module1567, rather than associated labels
for its text inputs. Role1 and role2 repeat 31 options under different named
radio groups. Visual stars and native required attributes differ. The kernel
requires the complete observed structure, options, control states, no native form
owner, retained root and document before every primitive and in final readback.
It never writes ID, role2, file upload, CAPTCHA, privacy agreement, or Submit.
Exact option strings only are proposed; ambiguous degree/date/certificate/city
facts remain manual. Numeric CET-6 scores require an exact CET-6 source record.

## Submit path and limits

A read-only fetch of the publicly referenced `site.min.js?v=202506121459` had
SHA-256 `d9e0a901236460425c9cef8a16bc06614b9336347f0384c420d840162b630da3`.
The inspected form implementation binds the submit div's click handler to
`SiteFormModule.submit`, which posts `cmd=addWafCk_addSubmit` to
`/ajax/siteForm_h.jsp`. Editing uses `/api/guest/form/memberModifySubmit`.
Inspection found no submit/autosave-on-input path in the routine form components.
This is a bounded static source observation, not proof of every executed script
or server behavior. Leaving ID/CAPTCHA blank is not a submit barrier.

The new transport component accepts only a finite public URL manifest, refuses
redirects, unexpected navigation and all form endpoints, and seals all HTTP and
WebSocket traffic before primitives. It has no reopen/final-submit permit. The
manifest is deliberately incomplete; unknown assets fail closed. This component
is not an OS-level network sandbox and must not be advertised as a proof of zero
transmission over every browser channel.

## Required next gates

- Hosted Linux and Mac browser oracle results for this exact candidate, including
  independent loopback server counters after controller SIGKILL with pending and
  delayed requests, and fresh-CDP-context disposal on detach
- A durable, one-shot approval bound to the exact task/revision, UI session,
  original profile and resume bytes, full selected plan, owned browser/context,
  document and form contract; a shared atomic worker/update admission fence
- A proven human-only transition that never silently unroutes or reconnects a
  filled document, permits no input-triggered application creation and never
  borrows a stale click after failed validation; no automated final click
- Script/resource identity admission, all relevant network channels, process
  death/disconnect, real installed Chrome/version and native user-device coverage

Runtime consent must explicitly explain that the employer may receive reviewed
routine fields as they are typed. No user applicant data may be used merely to
obtain engineering evidence. Resume transmission needs its own approval; ID,
CAPTCHA, terms and final submission retain their existing boundaries.

Local validation includes pure mapping, contract, private-route, privacy and
workflow-preservation tests. Browser lifecycle tests cannot run in the current
cloud shell because Chromium's process-singleton socket is denied, including the
supported escalated invocation. Do not count those tests as passed until the
corresponding hosted CI evidence exists. All prior account/draft/recovery safety
assertions remain selected and unchanged.
