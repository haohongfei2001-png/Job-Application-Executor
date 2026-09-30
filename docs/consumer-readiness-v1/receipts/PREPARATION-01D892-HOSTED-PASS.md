# Local preparation and duplicate-binding hosted verification

- Candidate: `01d892ed16c45295603e7c89f8d6054fbb7d6e34`
- Draft PR: https://github.com/haohongfei2001-png/Job-Application-Executor/pull/20
- Hosted run: https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36780909670
- Outcome: all eight selected jobs completed successfully; Draft/full-suite and
  unsigned-artifact guards were unchanged

The foundation job passed 25 contract/privacy checks, two state sequences,
358 independent browser checks, 502 operations/backend/manager/discovery checks,
and 77 dashboard cases (two pre-existing deselections). All 22 newly added
preparation/duplicate-binding browser cases passed. The hosted Mac native shard
passed 537 selected checks and seven updater checks; this is not device-specific
acceptance or a certification of an external ATS driver.

The synthetic preparation-dialog screenshot was downloaded and visually
inspected. It showed readable Chinese local-only/unverified status, the cached
public-source/manual-action boundary and no applicant values, ID or private
paths. Artifact `jae-local-preparation-ui-36780909670-1` (ID `11127728371`)
has SHA-256 `9c0226e8bde822d09d082c737b5c557fe0e01cb146ffd5769c5430a4eaf7bcb6`
and the configured three-day retention. No private fixture or real applicant
screenshot was published.

This supersedes the preceding run's single synthetic popup charset failure.
The corrected fixture declares UTF-8; production assertions were not removed.
Local cloud Chromium execution remained blocked, so browser and packaged
runtime results above are hosted evidence, not locally executed browser tests.
The non-Draft aggregate and current-source unsigned distribution remained
unrun/skipped. No merge, release, deployment or real application was performed.
