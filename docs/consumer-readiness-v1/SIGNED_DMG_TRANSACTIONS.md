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

## Evidence and limits

Local tests exercise inert signed fixtures at explicitly identified publisher
and execution seams. They cover complete copying, policy refusal, current data,
metadata drift, changing source/target paths, retained evidence, cross-kind
refusal, same-kind installation/update/rollback and image-tool wiring. Those
tests do not prove Apple signing, native execution or an actual mounted DMG.

The separate `signed_dmg_acceptance` hosted-Mac owner has a 40-minute job budget
and one 30-minute acceptance subprocess. It must perform the full signed-runtime,
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
The revised 1800-second bound allows those measured 817 seconds, an estimated
348-second complete journey and 635 seconds for additional signed checks and
variation. This is a scheduling estimate; a complete real run is still required.

Every old CI owner, owning test and timeout remains in force. The required
aggregate also requires the new Mac owner to succeed; skip/cancel/failure is not
acceptance. A completed exact-head hosted run is required separately from this
description. No completed hosted acceptance or consumer certification is claimed
by adding these checks. Actual Developer ID, notarization, stable download
delivery, physical-device acceptance and positive old-data migration remain open.
