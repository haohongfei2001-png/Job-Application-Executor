# Profile chooser test readiness

The adopted main `63a7bf5be56a4a47e6c0db6219faa6c946a3ab2a` passed exact-main
CI `37174851862`. Later ordinary draft CI `37178920445`, on the unchanged
product source carried by research head `53322a86`, failed two existing browser
cases in `test_task_workspace_missing_resume_save_uncertainty_never_replays`.
Both timed out clicking Save because it was disabled. The failed run is retained;
this note does not relabel it or claim a product repair.

The helper waited for dialog visibility, which precedes the asynchronous profile
read. Playwright's `set_input_files` does not wait for enabled state, so it can
populate the disabled chooser before that read finishes. The normal render then
clears that premature selection. A person cannot select through that disabled
control. [Playwright's actionability table](https://playwright.dev/python/docs/actionability)
documents this difference from normal click/fill actions.

The bounded test repair waits for the enabled chooser before file selection in
the uncertainty and cancellation cases. Every original assertion, including
conflict handling, clearing, zero replay and private-value exclusion, is retained.
An added held-response browser case deterministically exercises the premature
machine selection and its clearing, then performs an admitted selection and
checks the real conflict/no-replay behavior.

Only tests and this evidence note change. Production UI, API, storage, authority,
timeouts, required CI gates and signing research remain unchanged. The hosted
held-response reproduction and exact candidate/main gates are pending until their
actual results are recorded. Local browser execution is unavailable in the current
cloud workspace; collection/static checks are not a browser PASS.
