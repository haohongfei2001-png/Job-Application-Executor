# Local Diagnostics + One-Click Update v1

This package reduces dependence on direct desktop-control access. Runtime execution
stays local, while code remains GitHub-backed.

## Copy-safe diagnostics

The consumer dashboard exposes **复制诊断**. It returns only operational metadata:

- local Git version and branch;
- whether the tracked worktree is clean;
- supervisor / worker / Chrome CDP / DeepSeek availability;
- OTP state as counts only;
- task company, role, target hostname, stage, blocker and attempt count;
- a bounded list of recent transition events;
- the permanent manual-final-click safety flags.

The diagnostic report never includes applicant profile values, profile paths,
phone numbers, OTP values, cookies, API keys, browser credentials, full target
URLs or query parameters.

The report is designed to be pasted into ChatGPT for debugging when direct local
computer access is unavailable.

## One-click update

The dashboard exposes **检查并更新**. Update is intentionally fail-closed.

Before any Git mutation, the local updater requires:

- current branch is exactly `main`;
- no tracked local code changes;
- `origin` is the expected Job-Application-Executor GitHub repository;
- no active worker;
- no immediately runnable task;
- no OTP wait/ambiguity in progress.

The updater then executes an HTTP/1.1 fetch of `origin/main`. This intentionally
avoids the HTTP/2 framing failure observed on the local Mac. If the current HEAD
is already latest, no restart happens.

If an update exists, the updater requires the current HEAD to be an ancestor of
`origin/main`; only a fast-forward is permitted. It rechecks runtime safety
after fetch and before merge. Divergence, a dirty tracked worktree, an unexpected
remote or any runtime race aborts the update.

After a successful fast-forward, the updater safely stops the localhost
supervisor, starts the updated version, and opens a fresh authenticated UI.

## Runtime/update separation

The updater runs in a detached helper process. This avoids modifying Python source
files inside the same process that is serving the current UI.

Update state is stored under the private gitignored runtime directory. Logs go to
private runtime diagnostics and are not committed.

## Safety invariants

Updating does not:

- upload local runtime state to GitHub;
- copy applicant data, Chrome state or OTP values into Git;
- create, resume, cancel or submit an application;
- cross READY_TO_SUBMIT;
- add any final-submit endpoint.

Application execution remains local. GitHub remains the software source of truth,
not the personal runtime database.
