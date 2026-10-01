# Exact opaque human-request transport prototype

Status: **unregistered and not live-enabled**. No generic runner, model, Resume,
OTP, v1 or UI HTTP route imports or invokes this component. It is a narrow
transport/state-machine prototype, not the missing final-action authority.

`OpaqueHumanRequest` retains one concrete paused form-urlencoded POST Request.
It never reads/parses its body, serializes the Request, or projects protected
field values. Only the public content type is selected from local provisional
headers. Local Playwright transport necessarily materializes transient bytes;
this is not a promise that protected bytes never leave the renderer.

The owner schedules a native constant-text confirm dialog only after the exact
request is paused. The request nonce identifies that retained object, not a
payload hash. Opening and Closed events must pair on the owned frame, original
URL, unique constant message/nonce, confirm type, native browser-handler support
and a short deadline. There is no product call to `Dialog.accept()`. The private
owner must wire authenticated same-document CDP events, preserve a non-accepting
listener, and keep its event loop responsive to cancellation. Synthetic event
injection or test `accept()` would not establish physical-human provenance.

After the matched human response, an internal durable CAS must consume the
existing preparation's sole final-request slot before one local API client
`fetch(exact_Request, max_redirects=0, max_retries=0)`. Every reentrant guard,
consume, fulfill, dispose and receipt callback is fenced. Cancellation or drift
must not be overwritten by a later return. Unknown network/record outcomes never
permit automatic retry. The native browser's deny-only proxy stays closed.

The three browser fixtures verify opaque exact bytes at an independent loopback
server, original server-set HttpOnly cookie continuity, an application token,
redirect refusal and response-loss non-replay. Their native confirmation is an
explicit synthetic facade. Their SQLite fixture illustrates consume-before-send;
it is not wired production authority. They do not certify Qiyunfang's actual
CAPTCHA/session semantics or browser-native physical user controls.

## Required integration gates

- Durable one-final-slot CAS bound to original task/revision, preparation attempt,
  current UI/service identity and retained process/document/root/change epoch
- A pinned browser-side classifier producing only command/form/role metadata,
  with edit/setter/root replacement invalidation and no protected-value projection
- Real paired native-event observer and cancellation/expiry lifecycle ownership
- Dedicated child environment without protocol/API debug output, tracing, HAR,
  video or diagnostic capture; secret-canary stdout/stderr/artifact tests
- Native process-loss recovery and exact-head transport evidence
- Separate explicit file-bound resume upload/chunk workflow, with actual binary
  byte equality proof; opaque text Request forwarding must not imply file support

Resume upload occurs before final Submit on the observed site. Its uploadify
configuration uses resumable/chunk options. CDP may omit file bytes from request
postData entries, so the final text-forwarder is deliberately not an upload gate.
CAPTCHA retrieval and user handling likewise need their own narrowly bounded
stage. No endpoint is automatically admitted from this document or site scripts.
