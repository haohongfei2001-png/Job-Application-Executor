# Application Executor

Local-first, evidence-backed application/form executor for job portals and other web application systems. The reusable core is intentionally separated from site-specific adapters.

## Architecture

```text
Applicant Profile / Evidence Layer
              ↓
        Field Resolver
              ↓
      Application Plan
              ↓
 Site Adapter / Browser Executor
              ↓
Validation / Submission / Audit
```

Core modules:

- `executor/evidence.py` — builds the canonical applicant profile from local evidence and preserves provenance/conflicts.
- `executor/profile.py` — reads canonical and legacy profiles without storing credentials.
- `executor/resolver.py` — deterministic aliases/rules first; DeepSeek may interpret field meaning but never invent applicant facts.
- `executor/application.py` — generic state machine and submit boundary.
- `executor/audit.py` — per-execution plans/actions/evidence/receipt with persisted values masked.
- `executor/recovery.py` — re-runs a non-terminal execution from current page/profile; terminal submissions are read-only.
- `executor/review.py` — builds the mandatory pre-submit review, including structured-project coverage against the canonical project inventory.
- `executor/adapters/generic_web.py` — DOM/accessibility-first generic web adapter.
- `executor/adapters/schneider_boss.py` — preserved BOSS/Schneider-specific reference implementation.

## State model

```text
DISCOVERED
→ PROFILE_RESOLVED
→ FORM_FILLED
→ VALIDATED
→ READY_TO_SUBMIT
→ SUBMITTED
→ VERIFIED
```

`BLOCKED` and `ERROR` are explicit non-terminal states. `READY_TO_SUBMIT` is a hard manual boundary: the executor must stop there and the user must personally click the final submit control.

## Local environment

The committed Python dependency manifest is `requirements.txt`. Real applications use the installed Google Chrome through the dedicated CDP profile `~/Job-Application-Executor/chrome-profile`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### Browser runtime

A real application execution keeps one Playwright/CDP attachment for the whole run instead of reconnecting for every page. Live connections only auto-clean automation-owned junk: stale pytest `file://` pages, redundant blank tabs, and duplicate copies of the exact same target URL. Unrelated real job/application tabs are never closed by that cleanup.

Tests are hard-isolated from the live profile. Pytest sets `APPLICATION_EXECUTOR_BROWSER_MODE=isolated`, which launches Playwright's bundled headless Chromium with extensions disabled; it must never attach to port 9333 or mutate the persistent application profile. Live CDP reuse is refused unless the listener belongs to the dedicated private application profile and Chrome process.

Generic form discovery and validation use one in-page DOM snapshot per pass rather than hundreds of per-field CDP calls. This materially reduces UI contention on large React/Beisen-style forms.

## Canonical applicant profile

The real local profile lives at `config/applicant-profile.json` and is gitignored. Each canonical field can carry:

- `value`
- `sources` / provenance
- `confidence`
- `last_verified`
- `aliases`
- `normalization`
- `user_confirmed`
- `sensitive`

Local assets such as resume and photo are references with hashes; credentials, cookies and API keys do not belong in the profile.

Build/rebuild from evidence:

```bash
./scripts/application.sh profile-build \
  --max-docx /path/to/MAX.docx \
  --legacy-profile /path/to/legacy-profile.json \
  --resume /path/to/resume.docx \
  --photo /path/to/photo.png \
  --history-json /path/to/site-history.json \
  --history-scope site-campaign \
  --reconfirm-key compliance
```

Pending or conflicting personal facts are kept unresolved rather than inferred.

## Execute a new application

Fill everything deterministically known and stop on unresolved required fields or the final submit boundary:

```bash
./scripts/application.sh execute --url 'https://example.com/application'
```

The legacy `--submit-authorized` flag may still be used to record exact-target authorization, but it never authorizes an automated final click. Even with that flag, execution stops at `READY_TO_SUBMIT` so the user can review and personally click the final submit control.

When an execution stops with `unresolved_fields`, answer the field once and recover the same execution:

```bash
./scripts/application.sh answer \
  --execution-id '<execution-id>' \
  --selector '<selector-from-unresolved-fields>' \
  --value-json '"answer"'

./scripts/application.sh recover --execution-id '<execution-id>'
```

Execution answers are scoped to that application by default. For a durable personal fact that should become canonical across future applications, add `--promote-profile`. Stable factual answers (for example household type, student origin, health-status label, personnel-file location or foreign-residency status) may be promoted when the user explicitly asks for future reuse.

A user may also set standing decision policies in the canonical profile. When `policy.auto_accept_privacy_terms` is user-confirmed, standard recruitment privacy/data-processing consents are accepted automatically for an already-authorized exact target. When `policy.auto_accept_truth_submission_declarations` is user-confirmed, standard truthfulness/submission declarations are accepted automatically after the form values have been resolved. When `policy.auto_decide_company_legal_compliance` is user-confirmed, company-specific legal/compliance questions are handled without re-asking merely because the field is a compliance field. Pure assent/commitment statements (for example agreeing to a company compliance policy or promising to follow a code of conduct) are accepted automatically. Factual compliance questions are resolved from confirmed canonical facts or evidence-backed semantic mappings. Unknown objective facts are not invented: they remain unresolved for lack of factual basis.

When `policy.final_submission_requires_user_click` is user-confirmed, the executor must complete every preparatory step it can, validate the application, and stop at the final submit control. Privacy/accuracy declarations may still be handled by the standing policies above, but the actual final application action is never clicked by automation. The user performs that final click personally after review.

### Mandatory pre-submit review

Reaching `READY_TO_SUBMIT` must create `metadata.final_review`. The review is not optional. It includes the exact final control, attachment basenames, unresolved-field count, and a structured-project coverage audit.

Resume parsing is treated as an untrusted draft. Before the user is asked to click the final button, the executor/operator must audit every parsed section and remove misclassified rows. A resume attachment is **not** a substitute for structured project fields when the portal exposes a project/activity section.

The project audit compares the canonical project inventory with project-name fields actually present in the structured form. Every canonical project must end in one of three states: included, explicitly excluded with a reason/user instruction, or surfaced as `uncovered_projects` in the final review. Silent omission is forbidden. Research projects are not dropped merely because the target role is product-oriented; relevance decisions must be explicit. Unknown dates or facts are never invented to satisfy a required field.

Recovery deliberately does not inherit submission authorization.

### SMS one-time-code authentication

When the local, gitignored `config/otp-bridge.json` enables the iPhone relay, the
formal executor can orchestrate a narrow SMS-login flow before waiting for the
code. It must prove one explicit authentication context containing exactly one
visible OTP field, one phone field and one initial send-code control. The phone
comes only from the local canonical profile and is never sent to DeepSeek or
written to audit. A QR/face alternative may coexist in the same dialog; it does
not force the run to the QR path when the SMS path is uniquely proven.

The executor may fill the local canonical phone and click the initial send-code
control once. It never automatically clicks resend/countdown controls. Standard
authentication/privacy terms are checked only when
`policy.auto_accept_privacy_terms` is user-confirmed, or when the user already
checked them on the page; unrelated marketing/newsletter consent is untouched.
A combined authentication control such as `注册/登录` is allowed only inside the
same proven auth context after those terms are authorized. Password, CAPTCHA,
slider/image challenges and ambiguous controls remain human-handled.

After the request is sent, the existing OTP path remains unchanged: the executor
passes only the current hostname to the local relay, consumes one 4–8 digit code,
fills the unique OTP field, and continues only after the challenge disappears or
the scoped authentication confirmation succeeds. OTP values are transient,
single-use and never written to plans, action logs, screenshot metadata,
exception messages, SQLite, or DeepSeek. This authentication convenience does
not alter the mandatory manual final application click at `READY_TO_SUBMIT`.

## Resolution and safety rules

Resolution is conservative:

1. explicit/user-confirmed canonical profile facts;
2. evidence-backed canonical facts built from MAX/resume and other approved sources;
3. verified historical canonical facts when a history record explicitly maps them;
4. a non-empty value already present on the current site when no stronger canonical fact exists;
5. otherwise the field becomes `unresolved_fields`.

DeepSeek may be called while interpreting an unfamiliar field label, before source precedence is applied, but it can only return one of the existing canonical keys. It never supplies the applicant value. Generic education labels such as `School`/`Major` are treated as ambiguous when the degree level is not explicit. Standing user policies may automate privacy, truthfulness and evidence-backed compliance decisions, but objective facts must never be invented. Electronic signatures and subjective choices without a standing policy remain explicit decision points.

The resolver can reuse the existing macOS Keychain service `AI-Supervisor-DeepSeek`; the key itself is not written to this repository.

## Verification semantics

The generic adapter can only claim `page_signal` when it sees a success page. That leaves the execution at `SUBMITTED`, not `VERIFIED`.

A site adapter may promote verification to `api`, `application_history`, or `server_record` after independently reading the site's application record/status/id/timestamp. Only those levels can produce `VERIFIED`.

## Audit storage

Each run uses `applications/<execution-id>/` for:

- `plan.json`
- `actions.jsonl`
- `evidence/*.png`
- `submit-receipt.json` (after a submit attempt)

Execution directories are local and gitignored. Audit serialization masks sensitive field values and stores attachment basenames rather than copying credentials or browser state.

## Schneider regression boundary

The existing Schneider/BOSS adapter and its unit tests are preserved. The BOSS campus host is identified as the platform `boss_campus`, not as Schneider: another employer using the same host is therefore not mistaken for Schneider.

Already-submitted targets are protected separately through local, gitignored `config/protected-targets.json`. The migrated local configuration contains the submitted Schneider target identifiers, so a fresh generic execution against those exact targets is refused before browser mutation. A sanitized example schema is tracked as `config/protected-targets.example.json`.

The old `python -m executor.cli schneider-*` compatibility commands remain available for regression/reference, including their existing submitted-state refusal. Do not use them to alter an already submitted application.

## Local autonomous supervision

The durable task queue, local daemon, OTP broker and authenticated localhost API
are available through `.venv/bin/python -m executor.autonomy.cli`.
See [Local Autonomy v1](docs/LOCAL_AUTONOMY.md) for lifecycle commands, API and
Codex/Work handoff, private storage, restart recovery and the synthetic E2E.
The daemon stops at `READY_TO_SUBMIT`; final submission remains user-only.

## DeepSeek application manager

Local Autonomy can be controlled through a policy-gated DeepSeek manager and a
localhost chat dashboard. The user-facing model is simple: one task panel plus
one conversation with the AI application manager. Queueing, checkpoints, OTP,
browser adapters and the manual final-submit boundary stay behind that surface.

### Consumer entry on macOS

For ordinary use, install the thin local launcher once:

    .venv/bin/python -m executor.autonomy.cli install-app

Then open `~/Applications/AI 投递经理.app` from Finder, Spotlight or the Dock.
The app automatically prepares the dedicated Chrome session, starts/reuses the
localhost supervisor, runs the fail-closed preflight and opens the authenticated
dashboard. Opening the app itself never creates a task or submits anything.

The equivalent developer command is:

    .venv/bin/python -m executor.autonomy.cli launch

See [Consumer Entry v1](docs/CONSUMER_ENTRY.md).

The dashboard also provides **复制诊断** and **检查并更新**. Diagnostics are
copy-safe operational metadata with applicant values, OTPs, credentials and
profile paths excluded. The updater only fast-forwards a clean local `main`
from the expected GitHub `origin/main`, uses HTTP/1.1 for fetch reliability,
refuses updates during active/runnable/OTP-sensitive work, then safely restarts
the local supervisor. See
[Local Diagnostics + One-Click Update v1](docs/LOCAL_DIAGNOSTICS_UPDATER.md).

For lower-level development/debugging, start the daemon and open the UI directly:

    .venv/bin/python -m executor.autonomy.cli start
    .venv/bin/python -m executor.autonomy.cli ui

Or send one local chat turn from stdin:

    printf '%s' '继续这个岗位' | .venv/bin/python -m executor.autonomy.cli chat

The manager can propose only typed high-level decisions and every mutation is
revalidated by deterministic Python policy. It has no arbitrary shell/JavaScript
or final-submit capability. Raw OTPs remain inside the memory-only OTP broker.
Live autonomous tasks may use the existing DeepSeek semantic field mapper, while
isolated tests always force external model calls off.

See [DeepSeek Application Agent v1](docs/DEEPSEEK_APPLICATION_AGENT.md).

Before the first real-machine acceptance run, use the fail-closed readiness gate:

    .venv/bin/python -m executor.autonomy.cli preflight --start

It verifies the live browser mode, dedicated Chrome/CDP session, canonical
profile availability, DeepSeek credential availability and localhost supervisor
without creating an application task. See
[Live E2E Acceptance v1](docs/LIVE_E2E_ACCEPTANCE.md).
