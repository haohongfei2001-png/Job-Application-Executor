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

Actual Developer ID signing and notarization still require a separately approved
Apple identity/account workflow. A real consumer release additionally needs
verified download/distribution, exact signed DMG/app checks, and fresh quarantined
first launch/device evidence. This experiment cannot replace those gates.

Apple references: [signature storage and hashes](https://developer.apple.com/documentation/technotes/tn3126-inside-code-signing-hashes),
[nonstandard code structures](https://developer.apple.com/documentation/xcode/embedding-nonstandard-code-structures-in-a-bundle),
[distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac),
[independent requirements](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements).
