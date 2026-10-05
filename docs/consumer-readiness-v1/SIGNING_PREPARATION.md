# Build-only signing preparation and finalization

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
signature-success result during preparation. Missing/malformed policy refuses before building.
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

## Connected static finalization

The build report now includes `signing_preparation.finalization_inputs`: the
original receipt digest, the prepared identity digest, and the canonical explicit
publisher-policy digest. Preserve these values in the trusted invoking build
context. After a separately authorized signer has worked on that same workspace,
the next entry is:

```sh
python scripts/finalize_macos_signing.py \
  --distribution "$UNSIGNED_OUTPUT" \
  --workspace "$FRESH_PRIVATE_WORKSPACE" \
  --expected-receipt-sha256 "$BUILD_RECEIPT_SHA256" \
  --expected-prepared-identity-sha256 "$BUILD_PREPARED_IDENTITY_SHA256" \
  --expected-policy-sha256 "$BUILD_POLICY_SHA256" \
  --publisher-team-id "$EXPECTED_APPLE_TEAM_ID" \
  --publisher-bundle-id com.local.job-application-executor.ai-application-manager
```

These are explicit build inputs, not values discovered from the candidate's
claimed signer. Rehashing arbitrary downloaded sidecars does not authenticate a
release. There is no signing command or credential lookup in this pipeline.
Calling finalization on the normal unsigned build must fail Apple's real static
verification and must not produce a final identity.

The finalizer reuses the original unsigned stager and deterministically rebuilds
the preparation statement. It checks every original payload byte against the
original pinned archive, except a strictly bounded Mach-O signing transition:

- `macos_signing_delta.py` supports thin little-endian arm64/x86_64 images and
  the observed big-endian FAT32 containers with exactly those two slices. CPU,
  subtype and architecture order are preserved; no architecture is removed.
- The transition follows the internal allocator in the fixed Apple Security
  source version linked below. Existing signature offsets remain fixed. A missing
  signature command may be appended only in verified original zero header padding,
  with exactly one 16-byte command and the required header count/length increments;
  the signature starts at the original slice end rounded up to 16 bytes.
- Signature allocations and the necessary `__LINKEDIT` file length may change.
  The new mapping length must equal that file length rounded up to 16KB. FAT slices
  must be repacked at exactly 16KB boundaries with alignment exponent 14. All
  intervening padding is checked; these are fixed formulas, not arbitrary masks.
- Everything else, including original section/command fields and all code/data,
  stays byte-exact. Thread-zerofill sections are correctly treated as memory-only
  while their original fields and existing file padding remain preserved.
- Other formats, commands, relocation or allocator behavior refuse. In particular,
  this is not a generic Mach-O rewriter or compatibility promise for older
  standalone cctools allocation rules.
- Bytes between the declared SuperBlob length and the already-bounded
  `LC_CODE_SIGNATURE.datasize` are opaque allocation slack. Apple's writer may
  leave old signature bytes there. They remain included in the complete signed
  SHA-256/inventory; no non-signature byte range is newly exempted. This slack is
  not authenticated as code by Apple's signature check. Internal blob gaps,
  indexes, overlaps, boundaries and original payload comparisons stay strict.
  Refusals include object/slice names and numeric framing locations, never bytes.
- All other original paths, including manifests, RECORD/METADATA/WHEEL and the
  bridge, remain byte-exact. No hashes or metadata are repaired. The only added
  paths are the current four-file script-main `_CodeSignature` envelope.

The only external process is `/usr/bin/codesign` on macOS, invoked with fixed
`--verify --strict --all-architectures --test-requirement` arguments. Each planned
native object and the outer app must satisfy a requirement reconstructed from the
independently supplied policy: Apple anchor, Developer ID intermediate and leaf
certificate OIDs, team OU and the predetermined per-object identifier. Displayed
TeamIdentifier/Authority strings and the candidate's own requirement are unused.

The outer signature must authenticate an unchanged v2 CodeResources envelope
whose exact ordinary `files2` entry for the bridge contains its SHA-256. Optional,
symlink, nested-code, missing, duplicate or unknown bridge-entry forms refuse.
A codesign return code of zero without this linkage or original-payload proof is
insufficient. The finalizer rechecks the complete inventory and source evidence
before and after committing its external result.

Use a trusted, quiescent local build workspace. Apple static validation and
before/after snapshots do not provide isolation from a hostile concurrent writer.
Aliases, hardlinks, unexpected xattrs (including empty attributes), unsupported
file types and drift refuse. This remains a no-xattr build subset, not a solution
to the measured signed-metadata transport gap.

Only after all checks does `signed-build-identity-v2.json`, outside the app,
record the complete final bytes/types/modes inventory and the original-input
linkage. It excludes itself to avoid a hash cycle. Its phase is
`STATIC_SIGNED_BUILD_IDENTITY`; consumer admission is always `NOT_ADMITTED` and
certification `NOT_CERTIFIED`. It has no independently authenticated release
binding. Hardened-runtime policy, entitlements, notarization, Gatekeeper and fresh
online revocation checks are explicitly `NOT_PERFORMED`. It is a point-in-time
build identity that must be rehashed, not a consumer authorization. An error can
leave partial evidence, including a receipt invalidated by a final recheck; it
never counts as completion and is not automatically overwritten.

Actual Developer ID positive-path verification is still unverified. The owner’s
Apple team/identity/notary availability is unknown. A later authorized flow must
supply legitimate identity access and establish real signed compatibility,
notarization/stapling and quarantined fresh-install evidence. No ad-hoc or mocked
success substitutes for those gates. Stapling or any later mutation invalidates
this point-in-time identity and requires a separately reviewed final closure.

Signed archive/DMG transport, independently authenticated release binding,
consumer admission and runtime/update/rollback integration remain developer work.
The original cold installer continues to refuse these workspaces. Optional legacy
migration remains separate from ordinary fresh installation.

## Verification

`tests/test_macos_signing_preparation.py` uses explicitly synthetic static app
bytes and the real unsigned stager/copy/preparation. It tests policy/input binding,
metadata preservation, unexpected modifications, aliases, hardlinks, xattrs,
workspace replacement and real command-entry wiring. It never mocks codesign to
claim Apple acceptance. The hosted-Mac test in `test_app_distribution_v1.py` uses
the actual unsigned builder/runtime/native host, feeds its emitted pins into the
actual finalizer CLI, and requires rejection by Apple's static verifier with no
output identity. A further bounded 90-second section reuses that workspace for a
real ad-hoc-only signing-delta oracle: each nested object is signed explicitly
with the `-` identity, checked with all-architecture static verification, and then
sealed by the outer app. The new delta validator and bridge-coverage check consume
those actual before/after bytes. The full finalizer must still reject the ad-hoc
app against the independent Developer ID requirement and create no final identity.
The oracle never executes signed code or uploads the signed bundle. It reports
partial counts/elapsed time on failure, keeps the runtime owner/job budget,
and does not replace any original test. Only an actual hosted run can establish
this oracle's result; local synthetic tests or historical length records cannot.
`test_macos_signing_finalization.py` and `test_macos_signing_delta.py` cover pinned
input/phase rejection, structural byte preservation, explicit bridge coverage and
static-verifier refusal boundaries using clearly synthetic bytes. Their explicitly labeled post-verifier synthetic seam exercises receipt writing
and mutation failures; it never establishes a successful Apple-verified artifact. Local Linux results do not
establish the hosted negative test or any signed-release gate.

Relevant prior evidence: [D534 measured boundary](receipts/ADHOC-D534-OBSERVATION.md).
Apple references: [independent requirements](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)
and [distribution signing order](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac).

Static-finalization references: [code signing hashes and resource envelopes](https://developer.apple.com/documentation/technotes/tn3126-inside-code-signing-hashes),
[Apple static verifier implementation](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_codesigning/lib/StaticCode.cpp),
and [static verification concurrency limitation](https://github.com/apple-oss-distributions/Security/blob/main/OSX/libsecurity_codesigning/lib/SecStaticCode.h).

### Existing real input coverage and remaining signed-byte evidence

Read-only inspection of PR43 exact-main run `37232251737`, artifact `11315559898`,
SHA `681e32bac364cab798bd4483aa6df051f1baf9ab`, identifies 32 Mach-O objects and 43
architecture slices: 21 thin objects and 11 two-slice FAT containers. Of those
slices, 32 already contain an embedded signature; 11 x86_64 slices require a new
signature command. The bounded original-layout inspector covers all 32 objects and 43 slices. This
is original-layout evidence, not signature validation.
The archived artifact ZIP SHA-256 is
`b56952a81a444254a2c74265cec1ed7316ff73b4d2535e3395134ce1abb0654a`.

All 32 original binary hashes also match D534's retained unsigned inventory.
D534 records actual ad-hoc signed hashes, sizes and static-check results, but did
not retain signed payload bytes. Therefore that correlation alone cannot verify
the new byte-delta implementation or establish real Developer ID compatibility.

The modeled allocation formulas are pinned to Apple Security commit
`db15acbe6a7f257a859ad9a3bb86097bfe0679d9` (Security-61901.0.87.0.1):
[internal allocator invocation](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_codesigning/lib/signerutils.cpp#L175-L188),
[signature and LINKEDIT layout](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_codesigning/lib/codesign_alloc.cpp#L225-L308),
and [FAT layout](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_codesigning/lib/codesign_alloc.cpp#L391-L430).
Source inspection does not prove which allocator produced a historical artifact;
real signed-byte compatibility and publisher acceptance remain separate evidence.

The allocation-slack distinction is supported by the same fixed Apple source:
[writer only writes the declared blob](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_codesigning/lib/signerutils.cpp#L219-L229),
[reader uses the allocation as a maximum](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_codesigning/lib/machorep.cpp#L304-L323),
and [strict internal blob framing](https://github.com/apple-oss-distributions/Security/blob/db15acbe6a7f257a859ad9a3bb86097bfe0679d9/OSX/libsecurity_utilities/lib/superblob.h#L93-L110).
PR45's first hosted oracle (`37240392091`, job `111547774601`, head `c967826`)
retained a real negative result: 159 tests passed, this oracle failed after all
32 objects and the outer ad-hoc seal had verified, at a zero-unframed-byte check.
The 12.09-second child segment (12.63 seconds in the parent) did not time out.
Its original diagnostic did not identify whether bytes were inside the declared
blob or only allocation slack. The source-backed format correction must not be
presented as proof of that historical failure's exact location; a later actual
oracle result is separately required.

The separately reviewed CI headroom adjustment gives only `native_integration`
30 minutes for its existing tests plus unsigned app/DMG build/upload. All other
Mac matrix owners retain 25 minutes. The signing-delta oracle remains on
`runtime_distribution` with its unchanged 90-second child limit.
