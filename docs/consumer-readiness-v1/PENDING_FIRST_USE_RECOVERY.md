# Explicit recovery of interrupted first use — engineering candidate

This bounded A-01 increment lets an already activated, unchanged managed app
recheck a valid pending first-use fence through a static native prompt. It does
not migrate old tasks, repair arbitrary private state, reinstall or delete files.
It applies only to delivered builds containing this implementation. Older
bundles do not acquire recovery behavior merely from this document.

## Consumer path

Open the managed application normally. If its exact private pending fence and
bundle match and the default root is lock-only or has the same installation's
verified initial prepared checkpoint, a native prompt
allows “Recheck and complete first use” or “Cancel”. Cancellation does not start
a health probe or service. A malformed/mismatched initial continuity pipe cannot
fall back to this prompt as fresh authority. Existing complete installations keep
their ordinary reopening behavior.

After approval, the current verified application is health-probed with isolated
disposable state. The app and default-state guards are held during revalidation;
source, fence, default-root and lock-inode identities remain bound through the
handoff. The real strict installer creates the private root and all three lock
inodes before writing its pending fence. Recovery requires those existing
entries; a missing root or lock refuses, without recreating an empty authority.
The pre-prompt identities are retained. A first-use-only guard opens that directory
and its three ordinary lock inodes by held descriptor, without mkdir, creation or
chmod of any entry. The same held
identities are rechecked after health. The later strict native lease likewise
opens only the record-bound existing inode, without mkdir or chmod. Known legacy locations are finite read-only observations. These advisory
locks do not exclude arbitrary noncooperating legacy filesystem writers.

## Actual initialization boundary

The fence remains pending after the parent releases its installation guards.
Before focus IPC, Chrome, ordinary lifecycle health, auth-token or service-log
creation, a dedicated app-owned child consumes a finite inherited-pipe record.
The record contains only digest identities; paths and executables are derived
from the child's managed source. There is no browser/model/remote approval input.

The child takes nonblocking application and actual worker/migration ownership.
Before the first SQLite open it rechecks the exact bundle, pending fence, default
root and lock inodes, and requires lock-only default state or the exact prepared
checkpoint described below, plus absent known legacy
authority. Replacing even an empty root or identical fence does not count as the
same admission. Busy ownership refuses promptly. Ordinary queue construction
keeps its existing behavior; its strict callback is a private static object.

Build sequence 6 runs the existing queue, worker, answer and supervisor schema
initializers against one private in-memory SQLite connection. Ordinary startup
still uses its existing file-backed transactions. The bundled SQLite must support
serialization; otherwise strict first use refuses before creating payload files.
No separate schema implementation or SQLite pathname is used during preparation.
The initializer creates an empty queue and generates the initial answer key/local
token in memory. While migration ownership remains held, it writes the three
canonical payload objects exactly once through exclusive, no-follow creation
descriptors. Each is flushed and read back against the actual creation descriptor.
A bounded private `.first-use-prepared.json` receipt is written last. It binds the
exact bundle, original pending fence, root/lock identities, each payload's actual
inode/mode/owner/size/hash and its own inode. There is no rename, promotion, deletion
or replacement of payloads.

After a complete prepared checkpoint, an explicit Resume reuses those same three
objects. It revalidates the receipt and exact namespace before the prompt, after
health and in the actual child under worker/migration ownership. The unchanged
initializer must independently reproduce the recorded initial database bytes;
even an edited receipt cannot authorize adopting a journal with existing tasks or
other private records. The retained key/token must match exactly and are never
regenerated. A bound server is then constructed. No worker or HTTP
thread is exposed until the same pending fence has been completed and read back.
The child acknowledges its exact process/service instance and source binding;
the parent revalidates that instance before native presentation. Recovery UI
ticket issuance also carries the expected service identity into the server's
existing ticket lock; a replacement instance cannot mint a ticket for that
handoff. Binding is rechecked after minting. Ordinary empty-body ticket clients
retain their existing one-time-ticket behavior. Read-only token
access never creates a missing token or substitutes an ambient token. Missing or
wrong acknowledgements cannot trigger ordinary start/restart, existing-service
adoption, focus-only success or bootstrap fallback.

Completion means initialization admission committed. It does not itself prove
that the native window became ready, permissions were granted, business
dependencies work, or an application was completed.

## Failure and interruption

Before admitted initialization, refusal preserves existing private data. After
that point, a crash before the complete prepared receipt can leave partial private
objects. They are retained and recovery refuses. A completed, unchanged prepared
checkpoint can survive a later server-bind failure or interrupted completion;
the next explicit Resume can finish the same first-use transaction. No unknown
nonempty root is accepted, and the app does not claim nothing changed after a
partial write. Lost acknowledgement likewise remains an unconfirmed
startup, never an automatic replay. No registry PID is signaled by recovery.

Build sequence 5 preserves the original pending JSON and inode during completion.
It appends one bounded completion record, separated by ASCII RS and bound to the
original pending bytes' SHA-256 and bundle tag. Only the full canonical record,
including its final newline, projects as complete. An exact truncated prefix
remains pending; unrelated trailing data, duplicates and changed bindings refuse.
An explicitly re-admitted completion operation may append only the missing
suffix. It never truncates, overwrites or replaces the pending evidence. Existing
v1 complete records remain readable. Earlier binaries need not understand the
new completed framing and are not credited with this behavior.

Sequence 5's completion durability alone did not provide positive nonempty
recovery. Sequence 6 adds only the fully prepared initial checkpoint described
above. Neither can manufacture missing transaction provenance for older
installations, recover arbitrary historical databases or transfer old tasks,
answers, profiles or attachments. Earlier interrupted sequence-5 state without a
prepared receipt remains unsupported.

Selected legacy folders, unreceipted or changed nonempty pending state, changed bundles, missing managed
activation, automatic cleanup and positive migration remain unsupported. The
source and tests do not establish owner-device, physical-button/picker, signing,
real-site, 24-hour soak, five-day use, or whole A-01 acceptance.

## Verification required

Synthetic checks cover finite/cross-mode/partial/oversized replies; cancellation;
late default/legacy authority; root/lock/fence replacement before the prompt,
during health and at native handoff; nonblocking busy
ownership; actual migration-held admission before SQLite; queue crashes, short
fence writes, lost acknowledgement, wrong service instance and missing-token
preservation. Existing service-retirement and native owning tests remain intact.
Prepared-state tests additionally cover zero SQLite pathname opens during
initialization, agreement with the ordinary initializer, unavailable serialization,
short payload/receipt writes, failed flushes, unknown entries, exact inode and
content changes, aliases, permission changes, prompt/health/child arrival races,
and a retagged noninitial journal. Incomplete objects are retained, not repaired.

Hosted tests use complete actual native bundles with their own interpreter and
no build checkout/compiler. The real static Cocoa prompt is observed through its
cancellation oracle; a clearly synthetic intent then drives real recovery/native
opening or injects an authority after parent admission and proves the actual
child refuses before private initialization. This does not certify a physical
owner click. A separate installed test requires the bundled interpreter to create
an actual prepared checkpoint, survive pre-completion and short-completion faults,
preserve cancellation, Resume through the real child/service acknowledgement,
open the native page and reopen the same service. It checks retained private
object identities and zero task/answer activity. These hosted assertions are
requirements, not a claim that an unrun candidate passed. Exact-head full CI and
post-merge verification are still required.
