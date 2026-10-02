# 9fc2202 main engineering delivery receipt

Evidence checked: 2026-10-02. This is a fixed-version hosted engineering receipt,
not consumer certification, a local installation result or real-site acceptance.

## Exact source and completed run

- Repository: `haohongfei2001-png/Job-Application-Executor`
- Tested main commit: `9fc2202e3bde08ce0bfbc8a31d42464f9eb76d5a`
- Source tree: `1bd4b9949ca18788d624d34b311b5fa1856875cb`
- [Application Executor CI 37045631893](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37045631893),
  ordinary `push`, attempt `1`: `completed / success`, final update
  `2026-10-02T18:56:31Z`
- All eight selected jobs succeeded. `packaged_candidate`, the branch-specific
  early `qiyunfang_human_channel_smoke`, and label-only
  `engineering_closure_macos` were skipped by their existing conditions.
  The full Linux `test` and all four hosted Mac owning shards did execute.

Decoded hosted logs preserve these actual results; overlapping jobs are not
summed into a unique-test total:

- [Full Linux suite, job 110966238632](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37045631893/job/110966238632):
  **3350 passed, 93 skipped, 2 xpassed, 132 subtests passed** in 2728.70 seconds.
- [Hosted Mac native owning suite, job 110966238925](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37045631893/job/110966238925):
  **678 passed** in 1200.63 seconds; the separate retired-updater selector passed
  **7**, with its existing **87 deselected**. Build and artifact upload succeeded.
- [Linux preparation, job 110966239022](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37045631893/job/110966239022):
  **1082 passed, 77 skipped, 2 xpassed** in 371.27 seconds.
- [Hosted Mac preparation, job 110966239058](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37045631893/job/110966239058):
  **1130 passed, 29 skipped, 1 xfailed, 1 xpassed** in 1014.96 seconds.

The rejected raw-CDP comparison backend retains timing-dependent XFAIL/XPASS
observations. Neither outcome certifies that backend or the guarded native
backend. Skipped, deselected, live and physical-device cases are not PASS.

## Existing unsigned artifact

- [Artifact 11245607039](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/37045631893/artifacts/11245607039)
- Name: `jae-unsigned-macos-37045631893-1`
- Outer ZIP size: `138322904` bytes
- Uploaded: `2026-10-02T18:32:58Z`
- Recorded expiry: `2026-10-09T18:32:52Z`; not expired when checked
- GitHub outer ZIP SHA-256:
  `8a73176a342587221b21e2f106cd60c2dd9545d2632dc57aac18a2fee6a1f417`

The successful native build emitted the following distribution manifest at
`2026-10-02T18:32:51Z`, and the subsequent upload listed both
`AIApplicationManager-unsigned.tar.gz` and `distribution-manifest.json`:

- Format: `jae-macos-distribution-v1`
- App: `AI 投递经理.app`; presentation: `native`
- Inner archive SHA-256:
  `3110593afd8c296754fa739a34c44e8c0be99fd1b65f79cff64264b02d919bc7`
- Source payload SHA-256:
  `1388b6ae9d1e73f686499b3fd76f6d904c170ced511dddfc0dc41e83afc41844`
- Runtime SHA-256:
  `fb6ad115e087ad2830c77d39fa3db621738dff3d9b45277c1bbff544c0553878`
- Requirements SHA-256:
  `2fcd7e695c748d6c061798278e4c8a2564342da0d2eb82503b6b69430f53c4dc`
- Signing: `unsigned`; certification: `NOT_CERTIFIED`
- Final click actor: `user`; task state and build-host metadata: `excluded`

These digests are observed GitHub artifact metadata and hosted build/upload log
evidence. This documentation check did not download and independently rehash the
artifact or install it on the user's Mac. Recipients must still verify the
downloaded inner archive against its manifest before installation. The outer ZIP,
inner archive, source payload and Git commit are distinct identities.

## Artifact routing and limits

The existing workflow has two delivery routes:

1. Ordinary main push or non-Draft PR: the native owning Mac shard publishes
   `jae-unsigned-macos-<run>-<attempt>`. Wait for the corresponding complete CI
   result, since the artifact can appear before the full Linux job finishes.
2. Exact-head engineering closure label: after admission of
   `eng-<full-head-SHA>` and full Linux success, `engineering_closure_macos`
   publishes `jae-engineering-NOT_CERTIFIED-<full-head-SHA>-<run>-<attempt>`.
   This label-only artifact is not expected in the ordinary push recorded here.

Both routes retain the same two distribution files for seven days. This receipt
records the existing successful run without requesting a rerun, label or rebuild.
See the [candidate guide](../ENGINEERING_CANDIDATE_GUIDE.zh-CN.md) and
[closure contract](../ENGINEERING_CLOSURE.md) for use and admission details.

JCR-08 remains `IN_PROGRESS`; JCR-09 formal acceptance remains `NOT_STARTED`;
consumer certification remains `NOT_CERTIFIED`. Full frozen acceptance and
platform/fault coverage, real-user Mac first installation/physical operations,
real-site/account/server-result acceptance, signing/notarization, a 24-hour soak
and five real usage days are not established by this run. Hosted synthetic human
confirmation fixtures do not prove the user has submitted any real application.
Final submission remains human-only. No live submission, upload, credential,
spending or permission action was performed for this receipt.

Later documentation or source commits do not inherit this run as exact-head
evidence. The September 29 PR #20 notes retained in `STATUS.json` are historical;
the current delivery reference is the immutable main commit and run above.
