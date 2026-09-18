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

`BLOCKED` and `ERROR` are explicit non-terminal states. `READY_TO_SUBMIT` never implies authorization to submit.

## Local environment

The committed Python dependency manifest is `requirements.txt`. The executor uses the installed Google Chrome through the dedicated CDP profile rather than a disposable Playwright browser profile.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

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

Only when the exact target has explicit submission authorization:

```bash
./scripts/application.sh execute --url 'https://example.com/application' --submit-authorized
```

When an execution stops with `unresolved_fields`, answer the field once and recover the same execution:

```bash
./scripts/application.sh answer \
  --execution-id '<execution-id>' \
  --selector '<selector-from-unresolved-fields>' \
  --value-json '"answer"'

./scripts/application.sh recover --execution-id '<execution-id>'
```

Execution answers are scoped to that application by default. For a durable personal fact that should become canonical across future applications, add `--promote-profile`. Stable factual answers (for example household type, student origin, health-status label, personnel-file location or foreign-residency status) may be promoted when the user explicitly asks for future reuse. One-time privacy consents, truth declarations, signatures, salary choices and other application-specific decisions remain execution-scoped. Company-specific compliance facts must use company-scoped canonical keys rather than being generalized to other employers.

Recovery deliberately does not inherit submission authorization.

## Resolution and safety rules

Resolution is conservative:

1. explicit/user-confirmed canonical profile facts;
2. evidence-backed canonical facts built from MAX/resume and other approved sources;
3. verified historical canonical facts when a history record explicitly maps them;
4. a non-empty value already present on the current site when no stronger canonical fact exists;
5. otherwise the field becomes `unresolved_fields`.

DeepSeek may be called while interpreting an unfamiliar field label, before source precedence is applied, but it can only return one of the existing canonical keys. It never supplies the applicant value. Generic education labels such as `School`/`Major` are treated as ambiguous when the degree level is not explicit. Salary, legal/compliance declarations, signatures, work authorization and similar decisions require user confirmation even when the site marks them optional.

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
