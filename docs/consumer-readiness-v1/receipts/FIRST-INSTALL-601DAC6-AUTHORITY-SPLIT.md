# Delivered first-install authority split: preserved failure and bounded repair

## Actual hosted reproduction

PR #32 head `601dac6c9ca119ed04992ddaf382054586b9cb4b` adds one hard
regression without changing production code or existing test assertions.
[Run 37109015305, Mac runtime job 111163126766](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37109015305/job/111163126766)
finished its runtime/distribution selection at 2026-10-03 08:19:30 UTC:
**149 passed, 1 failed** in 291.20 seconds.

The fresh-home test used the real compiled native app, complete archive/receipt
intake and documented `install-distribution` CLI from an ordinary old checkout.
No runtime/destination override, existing app or pre-existing packaged task root
was present. The synthetic checkout retained a committed WAL task, five encrypted
answer events, the original answer key and a populated synthetic profile.

The failing assertion recorded only these value-free observations:

- `install_ok=true`, `app_activated=true`, `native_opened=true`.
- Old checkout task count: **1**; newly selected default journal task count: **0**.
- Complete old private file bytes, modes and inodes, task/answer rows and key
  remained unchanged. Archive and receipt identity also remained unchanged.
- Only the test-owned service was gracefully stopped; no website/application
  submission occurred. Raw private files, runtime keys and tokens are not receipts.

This establishes a split-authority first install, despite successful native
startup. It is not an inferred failure from an archive path or a mocked UI.

## Narrow correction

Delivered bundle intake replaces the installer source path with the admitted
candidate release. With no old installed launcher, the original invoking checkout
was consequently absent from the existing finite legacy-authority checks.

The delivered-bundle entry now captures its own invoking module source root and
passes that internal origin alongside the candidate release. Both the early
pre-staging check and the late pre-activation check apply the unchanged legacy
state detector to both sources. Aliases remain refused before path resolution.
This origin is not a CLI/archive field and cannot replace the selected task root.
Pure distribution builds do not supply it and continue to exclude private build
state. Existing same-authority and ordinary empty-lock semantics are preserved.

The exact hosted regression remains a hard assertion requiring
`legacy_state_migration_required`, no activated app, and no second journal,
credential or service. Additive deterministic cases cover early private state,
origin/ancestor/runtime aliases, late arrival, explicit same authority, empty
locks, pure-build exclusion and origin propagation.

This repair is safe refusal pending migration, not completed migration UX,
first-install consumer certification, owner-Mac acceptance or a real application.
Corrected-head hosted results remain pending; the original failed run is retained.
