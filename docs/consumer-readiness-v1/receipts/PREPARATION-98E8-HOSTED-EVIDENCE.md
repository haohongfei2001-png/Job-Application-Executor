# Local preparation hosted evidence and fixture correction

- Exact candidate: `98e81975ce2702398ab871e3e1d89b5261f03c44`
- [Application Executor CI 36777928800](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36777928800), attempt 1: completed **FAILURE** on 2026-09-30
- Seven selected jobs passed; foundation failed in one new browser fixture. This head is not a CI pass

## Passing evidence

- Foundation's complete browser/review group: 358 passed
- Foundation's operations/window/reliability/preparation group: 401 passed, including all 144 new preparation backend cases
- Dashboard group: 73 passed, 1 failed, 2 existing deselections. Eighteen of the nineteen new preparation browser cases passed
- Hosted Mac native/backend group: 436 passed in 1162.69 seconds, plus 7 retired-updater cases passed with 87 existing deselections
- Complete consumer/compatibility suites: 623 passed on Linux and 623 on hosted Mac
- Recovery: 40 passed per platform; Linux release/distribution: 247 passed and 16 declared platform skips; hosted Mac runtime/distribution: 149 passed

## Failure and bounded correction

The positive preparation dialog test passed its rendering, field-status, privacy-canary, narrow-layout, fixed-link and screenshot checks. Its synthetic external page then rendered the UTF-8 text `合成官方页面` as mojibake because the fixture omitted an encoding declaration. The failure occurred at the expected-page-text assertion; later opener/referrer/focus assertions in that case were not reached.

The correction declares UTF-8 in the synthetic response Content-Type and HTML meta tag. No product behavior, test assertion or fixture content is removed. The safe screenshot was produced before the failure, but the prior success-only artifact condition skipped its upload. Future runs retain that fixed synthetic PNG even if a later assertion fails; the screenshot hook still follows visible-private-canary checks and never captures traces, HTML, cookies or private files.

Exact new-head browser verification remains required. The complete non-Draft test and unsigned Mac artifact lanes were skipped by the existing Draft policy. Local preparation remains read-only and value-free; none of this evidence certifies an external ATS, saved server draft, account identity, live/private operation or final submission. Final application submission stays user-only.
