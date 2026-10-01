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
