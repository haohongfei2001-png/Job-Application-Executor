# Exact-head engineering closure

This is an explicit opt-in engineering gate, not a merge/release/deployment or
consumer certification. The pull request remains Draft. It adds no credential,
paid service, signing, live-site action or repository security-setting change.

## Trigger contract

After the final candidate's normal targeted checks and review, a maintainer may
add exactly `eng-<full 40-character lowercase head SHA>` to the same-repository
PR. The label must match the PR's current head. Applying unrelated, malformed,
old-head or fork labels cannot run candidate tests/builds. Existing ordinary
foundation/packaged/Mac matrix jobs are skipped for label events, including on
non-Draft PRs, rather than rerunning already green shards.

GitHub expression string comparison is case-insensitive. A case-variant label
can allocate checkout plus the small admission guard, but the strict Python
check refuses it before dependency installation, repository tests or building.
Every admitted label path checks out the exact head, runs isolated stdlib Python
(`-I`, avoiding checkout import shadowing), validates the label/repository/ref,
and compares both HEAD and the live remote branch with the requested SHA.
A branch that advances while queued refuses before repository execution.
The Mac job repeats this admission after the full Linux job and consumes that
job's exact-head output; a stale checkout/output cannot build an artifact.

The supported operator policy is **one closure per final candidate**, with only
failure-driven bounded retries. Do not remove/re-add a successful label merely
to obtain another green run. Stop publishing source while a closure is active;
if the candidate changes, its old label and evidence do not certify the new head.

## Preserved full gate and artifact

- Existing complete `python -m pytest -v` Linux job, existing 70-minute limit
- Only after that job succeeds: one 20-minute `macos-latest` unsigned build using
  the existing digest-pinned standalone runtime and distribution builder
- Workflow permissions remain `contents: read`; no write-token or secret access
  is added, and non-label push/non-Draft behavior stays unchanged
- New artifact name includes the complete tested SHA and run/attempt:
  `jae-engineering-NOT_CERTIFIED-<SHA>-<run>-<attempt>`
- Exactly `AIApplicationManager-unsigned.tar.gz` and
  `distribution-manifest.json`, retained seven days
- The existing builder binds archive/source/runtime/dependency digests, labels
  the result unsigned/NOT_CERTIFIED and excludes task state/build-host metadata

Historical sizing evidence: [run 36553017150](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36553017150)
on `7f1d8fa16e6bd7e2eed4d6735a300d04a724f3ca` passed 1778 tests with 16 skips in
1526.41 seconds (25m26s); the Linux job took about 26m16s including setup. The
current suite is larger and runtime is not assumed linear, but this evidence
does not justify blindly increasing the original 70-minute cap. These are runtime
budgets and historical measurements, not quoted prices or a promise of completion.

## Offline guard verification

`tests/test_engineering_closure_workflow_v1.py` is stdlib-only and runs from the
ordinary foundation job before dependencies. It evaluates the actual workflow
conditions and executes the actual isolated inline guards against local synthetic
Git remotes. Cases include valid/old/malformed/unrelated/fork labels, non-Draft
label events, preserved push behavior, moved or inaccessible refs, wrong checkout
and output SHA, case variants, shell metacharacters and local import shadowing.
It does not dispatch hosted work or access applicant data.

A successful full run plus artifact still does not establish signing/notarization,
real-device/physical-picker acceptance, a 24-hour soak, five real usage days, an
external ATS driver, account verification or server-persisted application draft.
Final submission remains human-only. See the [candidate guide](ENGINEERING_CANDIDATE_GUIDE.zh-CN.md)
and the explicitly [deferred anonymous-preparation design](ANONYMOUS_PREPARATION_DESIGN.md).
