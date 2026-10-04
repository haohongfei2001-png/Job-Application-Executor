# Build-only signing preparation

This developer component prepares the existing real unsigned distribution for a
future signing pipeline. It never signs, notarizes, executes the prepared app,
installs it, publishes a release, or admits a signed consumer package.

## Connected entry

The existing builder can now prepare a separate workspace after building its
normal native unsigned archive:

```sh
python scripts/build_macos_app.py \
  --standalone-runtime "$JAE_STANDALONE_RUNTIME" \
  --output "$UNSIGNED_OUTPUT" \
  --signing-workspace "$FRESH_PRIVATE_WORKSPACE" \
  --publisher-team-id "$EXPECTED_APPLE_TEAM_ID" \
  --publisher-bundle-id com.local.job-application-executor.ai-application-manager
```

The team is an explicitly configured *future required publisher*, not an observed
or authenticated publisher. There is no default team, incoming Authority/Team
claim, development/ad-hoc fallback, keychain access, identity selection or
signature-success result. Missing/malformed policy refuses before building.
The ordinary builder without these options produces the same unsigned artifact
contract as before. The producer lives in `scripts`. One shared copy-helper extension lets a caller
pin its already-created target inode before writing; existing callers keep their
old behavior. This delivered-source change advances the release sequence to 7.

For an already-produced artifact, `scripts/prepare_macos_signing.py` takes
`--distribution`, `--output`, `--expected-receipt-sha256`, `--publisher-team-id`
and `--publisher-bundle-id`. The expected receipt digest must come from the
invoking trusted build context. Hashing arbitrary downloaded bytes does not
authenticate their publisher. The build option above pins the actual receipt
from that invocation before calling the same preparation entry.

## Exact output and integrity boundary

The producer reuses `stage_macos_distribution` and `copy_bundle_payload` with
their full unsigned admission. Output must be a fresh private directory. Already-captured workspace/app identities fence later replacements. Partial
outputs are retained on failure and never overwritten or automatically retried.
A receipt is a point-in-time identity, not filesystem immutability: the future
signer must rehash it against the actual complete workspace before use. A failure
after receipt writing can leave mismatching evidence; it is not completion.

- The complete app is copied with unchanged original bytes and modes. Original
  source/runtime/native manifests and all wheel RECORD/METADATA/WHEEL files stay
  byte-for-byte intact. They remain evidence about the original unsigned input.
- `Contents/Resources/runtime/signing-input-bridge.json` binds that receipt, the entire
  original input inventory and explicit required publisher policy. It catalogs
  Mach-O-header candidates in inside-out order and binds stable per-path signing
  identifiers. Header classification is a plan, not native-code validation or
  permission to sign arbitrary replacements. A future signer must validate each
  actual code item and the full requested policy.
- `unsigned-distribution-manifest.json` outside the app preserves the original
  receipt bytes. The original archive remains at the caller's input location.
- `prepared-bundle-identity-v2.json` outside the app contains the complete prepared
  bytes/types/modes inventory, including the bridge. It does not include itself,
  avoiding a hash cycle. Its phase is `UNSIGNED_SIGNING_PREPARATION`, signing is
  `unsigned`, publisher validation and notarization are `NOT_PERFORMED`, signed
  output identity is null, and consumer admission is `DISALLOWED`.

This narrow preparation route requires no xattrs and refuses any it observes,
including empty attributes. It does not erase, repair or pretend to preserve
unsupported metadata. This is not a signed-metadata transport implementation or
a policy for downloaded quarantine annotations. D534's measured empty-attribute
transport loss remains a failure.

The bridge is deliberately an additional runtime file absent from the original
runtime manifest. Existing cold runtime validation therefore refuses this
workspace before direct installer/runtime execution; no consumer admission logic changed.
Repacking it with an updated archive hash still fails payload admission. The
local `_trusted_bundle` predicate remains an unsigned-integrity check, never a
publisher-trust decision. Do not distribute, open or install the prepared app
as a consumer artifact.

## Mandatory future stages

The future trusted signer must preserve the input bridge and wheel metadata,
validate the planned native inputs, sign them individually inside-out, and then
seal the outer app **including the bridge**. A valid outer publisher signature
must bind the original-input statement to final signed bytes. Never rewrite
RECORD hashes to disguise signing mutations or interpret original runtime/native
hashes as the signed outputs.

After legitimate signature/publisher verification and eventual notarization and
stapling, compute a **new** external all-bytes signed-output identity. The unsigned
preparation identity is stale as soon as any signing byte changes. The outer seal
cannot contain a manifest hashing that same final outer signature. The final
external identity also needs an independently authenticated release binding;
a self-declared JSON file next to the app is insufficient.

No signing finalizer, new release trust service, signed archive/DMG transport,
consumer admission, runtime startup exception or update/rollback policy is enabled
here. Those integrations and real-device acceptance remain developer work. Actual
Apple team/Developer ID/notarization availability is unknown and is not checked.
Any necessary owner account/credential step belongs to a later authorized flow.
Optional legacy-data migration is separate from ordinary fresh installation.

## Verification

`tests/test_macos_signing_preparation.py` uses explicitly synthetic static app
bytes and the real unsigned stager/copy/preparation. It tests policy/input binding,
metadata preservation, unexpected modifications, aliases, hardlinks, xattrs,
workspace replacement and real command-entry wiring. It never mocks codesign to
claim Apple acceptance. The hosted-Mac test in `test_app_distribution_v1.py` uses
the actual unsigned builder/runtime/native host and does not sign the result.
Local Linux results do not establish that hosted test or any signed-release gate.

Relevant prior evidence: [D534 measured boundary](receipts/ADHOC-D534-OBSERVATION.md).
Apple references: [independent requirements](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)
and [distribution signing order](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac).
