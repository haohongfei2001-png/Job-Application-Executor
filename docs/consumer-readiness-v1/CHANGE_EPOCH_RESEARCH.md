# Value-free change-epoch research

Status: **unregistered, not a submission classifier, and not live-enabled**.
The production preparation session, controller, UI, transport and runner do not
import this component. It cannot authorize a private upload, send a final
request, accept a native dialog or establish physical-human provenance.

`PreDocumentChangeEpoch` is installed before any page in a fresh, exclusively
owned context. The caller must ensure there are no earlier untrusted init
scripts; Playwright does not guarantee ordering among multiple init scripts.
No retrofit onto an existing page is accepted. Native ownership and the deny-only
network boundary remain separate mandatory prerequisites.

The renderer exports only a fixed format, monotonic integer and two booleans.
It does not invoke protected field getters, inspect setter arguments, read a
request body or return field values. Sealing chooses the unique current form
root and native controls. Thereafter any document mutation, native control write
or input/change/reset event revokes equality, including same-name radio peers
outside the root. Numeric/date native setters and native reset/range/step methods
are covered. The broad invalidation deliberately favors refusal over usability.

Instrumentation descriptors and reflection entrypoints are pinned before page
code. Descriptor/prototype mutation attempts after sealing permanently invalidate,
including legacy helpers and changes that are subsequently restored. Captured
setters still point to the wrappers. Extra frames/pages, object/embed elements,
shadow creation, document.open, service-worker/context lifecycle changes and native
navigation loss are unsupported. Python read, validation and lifecycle failures
are terminal and emit only `preparation_change_epoch_conflict`; later repair,
retry or resealing cannot restore trust.

## Evidence and limits

Synthetic tests cover ordinary change-and-return, typed setters, native keyboard
input, outside radio peers, captured wrapped setters, transient descriptors and
prototype replacement, hidden/new realms, protected getter canaries, and
permanent native-side revocation. Hosted Linux and Mac preparation lanes select
both new complete owning test files; no existing gate or fixture is removed.

An epoch is a conservative observation, not proof that arbitrary hostile
JavaScript is sandboxed. Browser extensions, prior privileged init scripts,
protocol-injected isolated worlds and arbitrary request-generation logic are
outside this component's proof. Pinned prototypes can be incompatible with page
frameworks; such pages must remain unsupported rather than relaxing the guard.
An explicit public-empty-form diagnostic has now observed the pinned Qiyunfang scripts with this observer. This is not production or private-form admission.
A separate body-free request classifier, retained request/document/process
binding, durable final-slot ownership, native human-event provenance and all
private/live acceptance gates are still required. No production integration or
release certification follows from passing these synthetic tests.

## Public empty-form compatibility correction

The original public probe reached the matched empty form but refused epoch sealing on both Linux and Mac. Finite diagnostics identified a pre-seal intrinsic definition. The observer now permits only single-key definitions of unrelated Object/Reflect properties before sealing. Its own pinned keys, prototype changes, bulk intrinsic definitions and every post-seal reflection write still invalidate trust. Invalidation does not prevent the wrapper from invoking the underlying native method; this component does not block all page mutations or become a JavaScript sandbox.

The bulk-definition path refuses trust without an audit enumeration, so a Proxy cannot swap the audited and applied property names. The alternating-ownKeys regression is retained. Diagnostics expose only a fixed reason enum and cannot clear an existing refusal.

Exact code head `219578b8bc8ca01436c4494d6e95d28ac578e910` passed [public compatibility run 36956253583](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36956253583): 96 synthetic/contract cases on each platform, followed by the actual empty official form, sealed deny transport, two seconds of unchanged epoch and proven owned-session closure. This establishes only a short, no-applicant, read-only compatibility observation. No private fields were filled, no resume was uploaded, and no final request or CAPTCHA was sent. The diagnostic is branch-scoped and does not register the observer in a production entrypoint.
