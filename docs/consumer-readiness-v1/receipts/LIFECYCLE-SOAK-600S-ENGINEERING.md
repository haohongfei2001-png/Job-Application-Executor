# 600-second paused-service lifecycle rehearsal

This is local Linux engineering evidence for a bounded partial Z-04 harness.
It is not 24-hour, browser/task, upgrade, native Mac or consumer certification.
No acceptance-matrix status is changed.

## Frozen identities and actual observation

- Runtime Git base: `1c3aa80541256a15865edc6db56ec0df23103807`
- Manifested runtime source payload:
  `4abde5d4f28fed7d67449259296dd06c05f53209fda0912ee13eea396d98baaf`
- Requirements digest:
  `2fcd7e695c748d6c061798278e4c8a2564342da0d2eb82503b6b69430f53c4dc`
- Rehearsed harness digest:
  `6db1bfea9db5e887a7c636313400354238e4d5a37d2d56e83f9e37f4fa18716f`
- The checkout was dirty; this is not exact committed-head harness evidence.
- Start: `2026-10-02T23:15:15.304213+00:00`
- Finish: `2026-10-02T23:25:15.880691+00:00`
- Requested window: 600 seconds; actual monotonic elapsed: **600.576 seconds**
- Actual service-running observations: **590.762 seconds**, excluding setup/stop
- **20** fixed paused synthetic tasks, **10** completed actual service cycles,
  **119** authenticated health observations and **121** retained checkpoints
- Terminal exit: **0 / COMPLETED_PARTIAL_WINDOW**
- Final actual child exit and worker-lock release confirmed; registry absent
- No journal/receipt/target/profile divergence or unexpected execution record
  detected; no browser, external application, user-data upload or submit occurred

Linux current-resource samples for the owned service ranged from **45,465,600**
to **56,524,800 bytes RSS**, with **5–6** open descriptors. These are observations,
not proof of no leak: startup warming and restarts affect resources, and runner
resource trends were not measured. Reported service-log bytes were zero.

The run executed a copied source manifest using the existing working Python,
with a fresh private HOME before application imports. It did not relocate Python,
read real profiles/settings, alter the user Mac or contact a model/provider.
Private runtime/token/key/log/database files remain local and are not included.
Only this value-free summary is retained here.

## Failure history and follow-up changes

During harness development, the first short probe refused an unmanifested checkout
health digest (`loaded_source_changed`) and gracefully retired its owned child.
The harness then copied/verified a manifested source snapshot. A strengthened
independent queue oracle initially assumed enqueue revision 0; local tests
correctly failed because the actual production event contract is enqueue 1,
paused 2. The oracle was corrected to that explicit contract without modifying
production queue code or loosening comparisons.

The 600-second run above used the earlier harness that encoded a checkpoint
before updating its cumulative-byte field. Later source corrects that accounting
and adds interpreter/platform identity; those changes do **not** inherit this
rehearsal as an exact-harness pass. Current black-box tests verify persisted byte
counts against actual files and exercise real repeated lifecycle, ambient-secret
isolation, aliases/existing roots, invalid bounds, SIGTERM, receipt/source
corruption, wrong registry ownership, synthetic resource-limit failures and a
window with no completed health cycle.

Current local validation after those changes: **18 runner tests + 17 existing
service-lifecycle tests passed**; existing workflow guards **18 + 2 passed**;
Python syntax and whitespace checks passed. CI selection adds two exact steps to
existing Linux foundation/hosted Mac runtime jobs. All original protected
commands, selectors, workflow permissions, job counts and budgets retain their
frozen hashes; no original gate or assertion was removed.

The new harness still requires its own complete hosted checks and later bounded
actual observation after adoption. Full Z-04 remains incomplete: no 24-hour
window, work-bearing reconnection, browser execution, update/rollback, crash
recovery, native Mac or leak-trend acceptance is claimed. Final submission remains
human-only and consumer certification remains `NOT_CERTIFIED`.
