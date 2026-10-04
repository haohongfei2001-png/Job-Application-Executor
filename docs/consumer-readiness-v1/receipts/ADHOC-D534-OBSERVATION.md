# Measured ad-hoc signature boundary at d534fc66

This is bounded engineering evidence, not publisher authentication, notarization,
signed intake approval, an application release or consumer certification.

## Exact observation and retained bytes

- Source head: `d534fc668facad3a0be108c6bab9f83d12715528`
- [Hosted observation 37188428890](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37188428890):
  SUCCESS, completed 2026-10-04 08:20:22 UTC; 30 probe contracts passed before
  the small real platform control, runtime preparation and product observation.
- Host: macOS 26.6.2 arm64, build 25G83; Python 3.12.10.
- Artifact `11298112902`, `jae-adhoc-NOT-TRUSTED-37188428890-1`, 676,137 bytes.
  Downloaded ZIP SHA-256, independently rehashed:
  `d596cb34b7abae587fcd3ee25120b56c609ba48be33b221816edb4f546017534`.
- ZIP contains exactly two separate reports; neither overwrote the other:
  `jae-platform-control/report.json` (6,797 bytes), SHA-256
  `acb047c837bd89bfc27e4fe8f5865549deba606c60dda7cb42d4954cb3658dec`;
  `jae-adhoc-preflight/report.json` (4,113,548 bytes), SHA-256
  `21ba2bd33b888fc8b9cc5fc8bbd6aaa71c484af479f823108357e87efb372a54`.
- The scopes are respectively `SYNTHETIC_PLATFORM_CONTROL` and
  `PRODUCT_EXPERIMENT`. Both say `OBSERVATION_COMPLETE`,
  `ABSENT_ADHOC_ONLY`, `NOT_PERFORMED` notarization and `NOT_CERTIFIED`.
- Product source manifest digest:
  `9b359d4edfdd1545f522c203495cfc95dcd0f8ce5fe3605cfb473be9aad62cb2`.
  Experimental unsigned runtime digest:
  `4112e0b0f05871a72a29a93986395924a233c0a2469615db87190ad22517a6c8`.
  These identify this observation, not a downloadable signed release.

## What actually worked

The small generated script-main app and the actual product app both accepted an
outer ad-hoc seal and passed Apple's strict on-disk signature verification.
On this host the outer script-main signature added `_CodeSignature` containing
`CodeDirectory`, `CodeRequirements`, `CodeResources` and `CodeSignature`.
No pre-existing product payload byte or mode changed, and no signature xattrs
were present. The controlled byte copy, tar round trip and read-only DMG retained
the complete measured inventory and verified successfully. This is evidence for
these exact bytes and toolchain, not a general xattr-preservation guarantee.

All ten product tamper cases first proved an exact copied inventory and a valid
OS signature. Modifying source, version, launcher, native image, resources or
signature entries then changed the pinned inventory and was rejected by the OS.
Re-signing modified source ad-hoc produced another OS-valid bundle, but it still
failed the original inventory identity. Therefore signature integrity alone
cannot establish the expected release or publisher.

All 32 individually catalogued nested Mach-O files accepted ad-hoc signatures
and individually verified. The subsequent outer seal also verified. Re-signing
one inner component after that seal invalidated the outer signature and changed
the pinned inventory, confirming that signing order is material.

## Failures and remaining production boundary

The real empty-xattr transport control still records `FAIL_INVENTORY_LOSS`:
the system copier retained a nonempty attribute but dropped its zero-length
companion. No attribute was repaired, omitted or relabeled as preserved.

Nested signing changed all 32 Mach-O hashes and invalidated the existing runtime
manifest. Wheel RECORD verification changed from valid to invalid for cffi,
cryptography, greenlet, lxml, playwright, pydantic_core and pymupdf. The pip RECORD
check was already false in the unsigned baseline and is not a signing regression.
Source-manifest verification remained true. The signing pipeline needs a reviewed
model binding original input provenance to final signed bytes; silently rewriting
RECORD or ignoring changed hashes would erase the proof instead of fixing it.

The unchanged production inventory, archive, copy, image and intake routes refused
the signed envelope. The controlled installer returned failure and created no
application/state authority. The report's `trusted_unsigned_bundle` field is the
existing local manifest predicate, not an Apple publisher-trust decision.

The optional `codesign --version` metadata query returned unsupported-option rc2;
no codesign version was obtained. Actual signing and verification commands have
their own successful or failed results. This diagnostic does not erase them.

No signed candidate ran. No signed bundle/archive/DMG, runtime credentials, keys,
private log, database or applicant data was uploaded. Real Developer ID policy,
notarization, signed production admission and fresh quarantined owner-device
acceptance remain unverified. This experiment is complete; another identical run
is unnecessary. The three earlier failed attempts remain recorded in
[ADHOC_SIGNATURE_PREFLIGHT.md](../ADHOC_SIGNATURE_PREFLIGHT.md).

## Adopted consumer baseline and next work

The fixed verified consumer baseline for this receipt is
`543c423e269078fd1acfd240dd3b6335767b3f0c`.
[Exact-main application CI 37184314697](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37184314697)
passed all eight required jobs at 07:34:53 UTC: Linux 3,661 passed, 114 skipped,
2 comparator XPASS and 132 subtests; hosted Mac native 906 plus 7 retired-updater
cases, runtime/distribution 156 plus 19 runner cases. The actual installed DMG
profile/resume repair, reopen and increasing-version update journey passed.
Comparator XPASS does not certify a guarded browser backend.

The unchanged observation head subsequently passed all eight required candidate
jobs in [run 37189672321](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37189672321)
at 09:29:17 UTC (Linux 3,691 passed, 114 skipped, 2 comparator XPASS and 132
subtests). PR38 merged as `0d0e0a4ee54f47403ec2a5138cc21e2bab51b646`.
The merge adds only the four reviewed engineering files; it does not change
production source or signed admission. This fixed observation receipt does not
claim results for later source, merge-head checks or the separate completion
durability candidate. Those exact checks remain independently required.

PR36 added normal basic-profile/resume onboarding without requiring JSON; PR37
added explicit replacement for a missing managed resume while preserving facts
and existing task bindings. PR39 corrected premature machine file selection in
browser tests; it did not change production UI. These are adopted engineering
improvements, not whole A-01/A-07 acceptance or a signed consumer release.

The next bounded product gap is interruption after first-use private state has
been initialized but before its completion fence is committed. Existing tests
prove that such state is preserved but cannot yet be resumed. Any repair must
prove the same installation transaction and state authority, refuse unknown
nonempty roots, and never automatically run application tasks. Positive legacy
transfer remains a separate unfinished requirement. JCR-08 stays `IN_PROGRESS`,
JCR-09 formal acceptance `NOT_STARTED`, and consumer status `NOT_CERTIFIED`.
