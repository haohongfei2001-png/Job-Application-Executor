# D747 scoped pass and incomplete owning Mac lane

Head: `d747c19035aa205801e44b38d27f2de40595f22b`.
Run: https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36853291875

- Linux early preparation oracle:405 passed,29 explicitly skipped Mac-only
  cases,2 raw-CDP timing cases XPASS. Neither XPASS certifies that rejected backend
- Mac early preparation oracle:434 passed,1 XFAIL,1 XPASS in371.82s; corrected
  WebTransport controls, driver-death checks and all three opaque text-forwarding
  fixtures passed, including cookie continuity/byte equality and no redirects/retry
- Seven selected owning jobs passed
- Mac native owning job110339734805 was cancelled at its25-minute ceiling after
  the new six-minute oracle plus part of the original unchanged suite. This head
  is **not all-green**, and the old suite is not claimed complete

The new preparation oracle is moved into a separate12-minute two-platform
allocation. Every original owning command, fixture selector, assertion and time
cap is preserved. A temporary research branch runs only read-only public preflight
and focused preparation checks; coherent changes return to draftPR20 for the
owning suites. No secrets, releases, merge/deploy or applicant data are involved.

## Read-only real-site result

Artifact11159175853 (2705-byte ZIP) was downloaded and inspected. It reports
PUBLIC_PREFLIGHT_INCOMPLETE: the main document and29 public assets loaded200,
with the previously audited site.js hash matching, but the popup was not yet
rendered. The strict gate refused the public POST lookup
`siteForm_h.jsp?cmd=getWafNotCk_getFormPopupId`. It also denied analytics, login,
cookie-changing and unrelated module requests. No applicant field was entered.

Pinned partitionSite.js independently shows that form6 lookup emits literal
`&formId=6`, then a separate getPopupZoneModule read. The next candidate admits
only that single reconstructed pre-data lookup and records public popup ID/hash.
Unknown module/bootstrap bodies remain denied until actual finite selectors are
observed. A DOM observation confirms public col124, manageMode=false,
_vueStyleGrayTest=false, major color#2b2b2b and the empty module1567 form inside
module1566. Runtime_extId/actual popup response were not inferred from those facts.

## Follow-up research run 36859394707 (72bc9827)

The first split research run failed before public site access. Ubuntu24's bundled
headless-shell rejected sandbox startup: 37 failed, 430 passed, 29 skipped,
2 unsafe-comparator XPASS. Mac's sandboxed launch succeeded, but native command
line inspection required explicit `--enable-automation`, absent from pinned
Playwright1.63 defaults: 59 failed, 437 passed, 1 comparator XFAIL, 1 XPASS.
Neither preflight reached the site. These failures are retained, not retried as
if transient. The next recipe explicitly discloses automation and retains all
safe-flag checks.

The next Linux experiment is the **distinct already-installed official Chrome**
channel with sandboxing enabled and its existing Ubuntu AppArmor profile. It is
not evidence for bundled Linux Chromium. No AppArmor/sysctl/setuid changes,
executable copying, no-sandbox fallback or certificate bypass are permitted.
Sources: [Playwright branded channels](https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge),
[Chromium Ubuntu sandbox policy](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md),
[GitHub Ubuntu24 image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md).

## Sandboxed research run36860470239 (46575b3)

- Mac:505 passed,29 platform-only skips,1 unsafe-CDP XFAIL,1 timing XPASS in425.92s
- Linux installed Chrome:473 passed,58 platform skips,2 comparator XPASS;3 opaque
  request fixtures still used bundled headless-shell and failed sandbox startup.
  Those fixtures now explicitly select installed Chrome and verify native flags
- Both public probes launched safely and observed form6→popup1566 with matching
  response hash. The next blocked read had `_extId=undefined`, popup1566,
  col124, false management/fresh/gray flags and color#2b2b2b
- Both main HTML responses explicitly linked the public CSS variant with
  `clientSupportWebp=false`; all19 fetched script hashes matched across platforms

The next research iteration exercises the exact popup read and affected opaque
request fixtures only. The full native channel/lifecycle oracle stays in the
main owning workflow; this scope reduction avoids repeatedly spending seven
Mac minutes on unchanged transport code. Live admission remains disabled.
