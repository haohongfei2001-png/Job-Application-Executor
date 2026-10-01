# Preparation authority and recovery checkpoint

Status: implemented for isolated validation, **not registered for live execution**.
No model, generic queue Resume, OTP path, or public v1 endpoint can mint this
preparation approval. The existing private value-review UI is still read-only.

## Authority and one-shot execution

A short-lived offer binds the original task and revision, complete original
TaskSpec digest, exact profile bytes, designated resume version/status, selected
routine-value plan, UI session, service instance and browser process/context/
document/root/control/resource identities. The warning explicitly distinguishes
reviewed data transmission as typing occurs from final submission authority.

Approval consumption is a SQLite transaction that claims the existing task lease
and records a value-free one-shot nonce. Admission first takes the same pinned
`migration.lock` inode held by activation, before any SQLite transaction, through
marker fsync and commit. Activation-first and admission-first contention both
refuse without lock-order inversion. A separate durable context fence blocks
all workers and current updater/activation entrypoints even after pause, cancel,
lease expiry or service death. Every browser primitive rechecks the actual copied
plan digest, current task/session/material and retained document. Each approval
binds exactly one durable run attempt; returning cleanly cannot restart it.

The primitive journal contains only digests and outcomes. It enforces the full
ordered approved primitive sequence, separately journals checkbox primitives,
and downgrades all retained evidence on uncertainty. A prepared result requires
all expected durable identities and DOM readbacks. It remains
`PREPARED_UNVERIFIED`, with no account, server-draft, READY or final-submit claim.

## Marker and SQLite crash windows

Filesystem and SQLite operations are **not one atomic transaction**. A private,
no-follow, descriptor-pinned active marker is fsynced before admission commits.
It contains nonce/browser hashes only. Any uncertain publication leaves a refusal.

After independently proved context disposal, the marker is atomically renamed
under the pinned directory to a retained hash-only closed receipt. The moved
inode and bytes are verified. Replaceable paths are never unlinked. Every
post-rename uncertainty restores a no-overwrite blocking sentinel and preserves
the displaced bytes. SQLite rollback after marker retirement stays safe because
the browser was already proved disposed; the durable row still blocks workers
until reconciliation. A retired nonce receipt also prevents nonce reuse if a
subsequent SQLite tombstone commit was interrupted.

A restarted authority can perform read-only same-process CDP inventory. Absence
of the exact original context may close the record as UNKNOWN, never success,
never replay. Missing/unreachable/replaced processes are UNKNOWN. An orphan whose
admission never committed requires the same original browser identity proof and
gets a durable nonce tombstone. Wrong, replaced, malformed or aliased markers are
preserved and refused. Opaque highly sensitive payloads are not part of this layer.

The current activation code consults the active marker without opening a legacy
WAL database (a read-only SQLite connection can still change SHM reader marks).
This does not modify an already-installed historical binary or certify arbitrary
old-writer coexistence. The documented supported updater/rollback path remains
required; external legacy executables remain a separate compatibility limit.

## Evidence and remaining gates

Pure/API/SQLite tests cover wrong/stale scope, content/session drift, concurrent
worker/approval and competing journal admission, cancellation, per-primitive
revocation, marker/commit failure windows, cross-instance orphan replay refusal,
root replacement and preservation of outside canaries. Existing state-compatibility
and workflow byte-preservation assertions remain unchanged.

The synthetic browser integration selects an exact offer, consumes it, fills the
independently captured popup replica through the durable journal, proves protected
controls untouched, checks root replacement, closes a real fresh context, and
uses real CDP absence inventory for non-replaying recovery. Its exact hosted
results must be reported separately; local Chromium startup remains unavailable.

The owned live-process verifier, runtime UI approval/session bridge, complete
resource admission, additional transport/driver-death coverage and a human-only
final-request handoff remain required before enabling real applicant filling.
Final ID/CAPTCHA/request bodies must remain opaque/transient on the browser side;
app review/logs must not mirror those payloads. Real user-data tests and final
submission remain separate from this engineering work.
