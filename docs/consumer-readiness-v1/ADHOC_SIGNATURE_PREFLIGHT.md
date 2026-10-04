# Bounded ad-hoc signature preflight

This is engineering evidence preparation for JCR-08 consumer installation. It
does not complete A-01, publisher authentication, notarization or consumer release.
No production installer, integrity inventory, manifest or signing admission is
changed. The canonical status remains NOT_CERTIFIED.

The one branch-scoped hosted-Mac job builds this checkout's declared app in a
fresh synthetic HOME, then signs disposable copies with `codesign --sign -`.
This creates no certificate, private key, account, persistent grant or paid
commitment. No candidate code runs after signing or tampering. The original
unsigned builder still performs its usual real build/health verification.

The report distinguishes:

- exact path/type/mode/hash/xattr inventories before and after outer script-main
  signing, including the actual sidecar files emitted by the hosted toolchain;
- unchanged production inventory/archive/copy/intake/image/install refusals,
  and separate bytes-only tar/copy and read-only DMG transport controls;
- source, version, launcher, native binary, extra-resource and each emitted
  signature-sidecar tampering, with pinned-inventory rejection reported separately
  from Apple's own integrity result;
- explicitly catalogued Mach-O files signed inside-out, original wheel RECORD,
  runtime/native manifest effects, and a late inner-component re-sign;
- ad-hoc integrity only, publisher trust ABSENT and notarization NOT_PERFORMED.

Only report.json is retained as an artifact. No experimental bundle/archive/DMG,
private synthetic HOME, service state or credentials are uploaded. The raw
transport controls are explicitly not deliveries. The installer is called only
after the unchanged static intake has refused the control package; it must return
failure without creating an application or state authority.

The stopping condition is one measured report or an honest incomplete/refused
observation. A signing failure does not authorize changing layout, entitlements,
RECORD, manifests or production acceptance to force success. A later production
design would need independently trusted publisher policy and a reviewed versioned
contract for original input provenance versus final signed bytes. Without that
identity/contract this work stops at evidence; it does not grow a signing framework.

The first hosted attempt, run 37177620344 at source 23681e95, stopped in probe
contract tests before signing: CPython's `os.*xattr` functions are Linux-only.
The corrected harness uses bounded Darwin descriptor APIs for reading xattrs and
the system metadata-preserving copy tool for tamper controls. This correction
does not turn the failed attempt into signing evidence or modify product code.

The second attempt, run 37178920410 at 53322a86, passed 22 contracts and failed
the strict metadata control: the hosted system copier retained a nonempty xattr
but dropped a zero-length xattr. This is a real observed transport failure.
The next bounded observation retains it as FAIL_INVENTORY_LOSS in report.json
and continues independent probes. Tamper cases still require an exact, valid
baseline; any loss makes that case inconclusive before mutation. No copy repair,
attribute exclusion or transport PASS is substituted.

The third attempt, run 37179458231 at 57ca4576, passed 23 contracts and failed
one report test before signing. That test mocked every platform command,
including `ditto`, then attempted to inventory its never-created destination.
No product signing or tamper observation was produced by any of these three
attempts. The ordinary application CI result is not signature evidence.

The resumed correction separates that pure report test from all real platform
effects. Contracts now run before standalone-runtime preparation. A small real
Apple-tool control then signs a freshly generated script-main app, verifies its
baseline, observes metadata/bytes/tar transport, and modifies the exact verified
original script to measure rejection. It never executes that script. Any failure
to establish a valid signature or reject the modified script stops before runtime
download and product build. Its separate report is explicitly synthetic platform
evidence, not evidence for the product's bundle or dependencies. Both sanitized
reports are retained; empty-attribute loss remains a failed transport observation.

Only after those inexpensive checks pass does the existing bounded product
experiment run once on this candidate. Stop at that observation's supported
decision, including an incomplete or refused result, and reassess the next step
from actual evidence rather than repeatedly rerunning the same chain.

Actual Developer ID signing and notarization still require a separately approved
Apple identity/account workflow. A real consumer release additionally needs
verified download/distribution, exact signed DMG/app checks, and fresh quarantined
first launch/device evidence. This experiment cannot replace those gates.

Apple references: [signature storage and hashes](https://developer.apple.com/documentation/technotes/tn3126-inside-code-signing-hashes),
[nonstandard code structures](https://developer.apple.com/documentation/xcode/embedding-nonstandard-code-structures-in-a-bundle),
[distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac),
[independent requirements](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements).
