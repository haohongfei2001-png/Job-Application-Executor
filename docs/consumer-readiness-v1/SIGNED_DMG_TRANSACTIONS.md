# Signed DMG transaction engineering boundary

This increment connects the current signed-payload format to the existing
single-file installer, byte-preserving copy, update and retained-version rollback.
It does not configure a real publisher or establish Apple distribution approval.
`BUILT_IN_PUBLISHER_POLICY` remains `None`; normal builds cannot admit signed
consumer payloads without a separately reviewed, authenticated verifier build.
There is no policy file, environment switch, candidate-derived publisher,
credential enrollment, new trust service or automatic signed/unsigned migration.

## Accepted representation

The supported signed representation is exactly the current script-main app with
the four `_CodeSignature` files, signing-input bridge and sealed current-payload
manifest. The app must authenticate against an independent publisher requirement
and its current source/runtime/native bytes. Every file and directory must be
owned, ordinary, alias-free and within the declared inventory. Every extended
attribute, including an empty attribute or quarantine metadata, causes refusal;
the installer never strips one or attempts a new metadata transport. Additional
signature layouts are unsupported until separately reviewed.

Original unsigned manifests and wheel RECORDs remain provenance. The current
manifest supplies the actual signed runtime hashes. A signature directory with
no current manifest cannot fall through to unsigned reconstruction. The signed
copy binds its source, empty target inode, all copied bytes/modes and final target
inode across validation. Installation preserves the launcher, Info.plist, native
host, nested signing bytes and outer envelope together. Transaction identity
rechecks the narrow inventory, modes and absence of xattrs after waits. Uncertain
signed staging and failed/retained versions remain available for diagnosis.

Unsigned-to-unsigned behavior retains its existing path. Fresh signed install,
signed-to-signed update and explicit signed rollback use the existing task/app
locks and activation/recovery transaction. Signed/unsigned transitions refuse
before the downloaded installer retires the current service. There is no mixed
migration, old-data import, forced window closure or task replay.

## Build interface

`scripts/build_macos_installer.py` accepts exactly one of `--distribution` and
`--signed-app`. The unsigned invocation is unchanged. A signed invocation also
requires explicit `--team-id` and `--bundle-id` from the trusted build operator;
these arguments do not change the shipped runtime policy. No signed tar format
or second archive parser is introduced.

The builder stages the signed app with the same preserving copy, creates the
DMG, mounts the completed image read-only and authenticates its actual delivered
bytes before writing a receipt. A signed receipt records
`signing: static_publisher_verified`, `consumer_admission: NOT_ADMITTED`,
`notarization: not_performed` and `certification: NOT_CERTIFIED`. The receipt is
not a trust root. Normal release jobs continue to produce unsigned artifacts.

## Finite first-use preparation

The first-use child still performs every original full identity, app/worker/
migration lock, private prepared-state and fence check. After each successful
named boundary it writes one compact, ordered preparation frame to its private
reply pipe. Preparation frames do not admit a service, open UI, or acknowledge
success. Unknown, repeated, skipped, malformed or extra stages refuse.

Each complete valid preparation frame renews a 30-second no-progress deadline;
partial bytes never do. The ten named boundaries plus final acknowledgement are
bounded by 165 seconds and 4096 aggregate bytes. The original final success ACK
is still sent only after prepared-state/fence commitment and service registry
publication, followed by EOF and the unchanged exact-child/service/health checks.
This addresses the measured signed-child path in run 37296852919: ten successful
full identity checks took 8.764–13.081 seconds each, and the child reached ACK at
102.085 seconds, after its original reader had closed. The protocol run
37300392241 subsequently completed all ten checks and ACK at 101.018 seconds;
its slowest check took 14.841 seconds. The 30-second no-progress bound gives
approximately twice that measured stage duration, while the absolute bound stays
165 seconds and cannot be renewed. The initial private startup-record transport
remains bounded by 15 seconds. A slower/stalled stage or absolute-limit expiry
still fails; the measurements are not a future timing or acceptance guarantee.

On refusal the parent closes only its reply reader and polls only its own Popen
child. A still-preparing child observes cancellation at its next verified frame,
not in the middle of private-state writes. Existing prepared objects and a
completed fence survive lost ACKs. An already committed service is not killed,
restarted, or adopted on ambiguous acknowledgement; no registry PID is signaled.
The synthetic complete-transaction observer allows 900 seconds, including entry
verification/installation and actual native startup. The existing native observer
retains its 600-second limit; the outer 900-second transaction and the oracle's
remaining 3900 seconds also bound that wait. These are shared test observation
limits, not additional production startup/health/UI authority.

## Evidence and limits

Local tests exercise inert signed fixtures at explicitly identified publisher
and execution seams. They cover complete copying, policy refusal, current data,
metadata drift, changing source/target paths, retained evidence, cross-kind
refusal, same-kind installation/update/rollback and image-tool wiring. Those
tests do not prove Apple signing, native execution or an actual mounted DMG.

The separate `signed_dmg_acceptance` hosted-Mac owner has a 75-minute job budget
and one 65-minute acceptance subprocess. It must perform the full signed-runtime,
read-only DMG, Cocoa cancellation, explicit synthetic first install, actual native
reopen/service health, same-kind update, explicit rollback and failed-final-path
recovery journey. The test-only fixed publisher adapter is placed in an isolated
source copy before manifests/build/signing; every bundled process uses that same
copy. It keeps real strict/all-architecture code-sign verification while omitting
certificate identity only. Unmodified production must independently refuse the
same ad-hoc bytes. No environment switch or post-sign source patch is permitted.
The fixture has no authority to upload signed packages or confer owner intent.

The first hosted attempt exhausted the original 900-second engineering budget:
three complete signed builds and verified DMGs consumed 817.15 seconds before
the production/damage refusals, and the installation journey never started.
That failed run remains negative evidence, not a performance or acceptance pass.
The next 1800-second bound estimated those measured 817 seconds, a 348-second
journey and 635 seconds of additional signed checks. Actual signed work exceeded
that estimate. Run 37300392241 passed real first install/native/health and signed
update/private-state preservation, but only reached rollback refusal work at
1756.42 seconds, then failed the 1800-second limit. Rollback, the third image and
failed-update restoration were not completed; no full receipt exists.

The 3900-second bound was initially calculated from that measured 1756.42-second
prefix and two remaining transactions at their then-current 600-second limits,
estimated 400 seconds for version-three build/sign/image work and 300 seconds
for refusal/identity/reopen/state checks. That 3656.42-second model leaves 243.58
seconds of contingency. The 400/300 allocations are empirical estimates, not
mathematical sums of all individual timeouts; a complete real run is required.
The four transaction limits are now 900 seconds, ordinary commands remain 180
seconds, and failure-only observation remains inside the total. Independent jobs would
need to repeat real setup rather than transfer private state or trust receipts;
this owner retains the same actual apps, service history and private-state
identity through the complete journey without uploading them.

Run 37304892470 completed the entire real synthetic journey in 3085.71 seconds,
including all 25 checks, three versions/images, rollback and actual failed-update
restoration. The subsequent same-production run 37312104450 still failed: update
entry verification/installation returned normally only at 592.213 seconds, and
the old shared 600-second transaction allowance cut off its newly started native
child after less than eight seconds. It was not a completed update or acceptance
pass. The revised 900-second observer is supported by the measured slow entry
(592.213 seconds) plus the longest completed fresh-native observation (216.234
seconds): 808.447 seconds plus 91.553 seconds of margin. Those observations came
from different runs, so this composition is an estimate, not a future bound.
It does not create independently enforced entry/native phase allocations. The
native limit stays 600 seconds and the total remains 3900 seconds; individual
allowances are not guaranteed to fit if every stage reaches its cap.
All original production limits remain unchanged.

This exposes a product performance gap. A completed synthetic fresh installation
command took 495.987 seconds (about eight minutes), and a completed update took
587.67 seconds (almost ten minutes); another update entry alone took 592.213
seconds. Repeated full signature/payload verification is costly. These results
prove controlled software integrity/connectivity only, not acceptable consumer
installation speed, consumer delivery or Apple distribution certification.

Every old CI owner, owning test and timeout remains in force. The required
aggregate also requires the new Mac owner to succeed; skip/cancel/failure is not
acceptance. A completed exact-head hosted run is required separately from this
description. No completed hosted acceptance or consumer certification is claimed
by adding these checks. Actual Developer ID, notarization, stable download
delivery, physical-device acceptance and positive old-data migration remain open.
