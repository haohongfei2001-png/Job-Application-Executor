# Application Executor migration audit — 2026-09-18

## Scope

This audit and migration started from the real local implementation, not from a greenfield design.

Pre-migration repository HEAD: `389e0696d73afc8fa8b460fcdefb08f797180f22`.

Safety checkpoint created before refactoring:

- Git branch: `checkpoint/pre-generic-migration-20260918`
- External bundle/config backup: `~/Job-Application-Executor-backups/20260918-0933-pre-generic/`
- Existing Schneider application state was already `SUBMITTED`; it was not used as a live mutation target during migration.

## Audit of the original implementation

Reusable pieces already present:

- dedicated Chrome profile and CDP connection in `executor/browser.py`;
- DOM field discovery/filling in `executor/engine.py`;
- deterministic field classification and final-submit detection;
- explicit submit confirmation barrier;
- runtime checkpoint model and screenshots;
- local profile loading with credential-key rejection;
- OTP helper with fail-closed ambiguity handling.

The original repository therefore already had useful execution primitives; replacing it wholesale would have discarded proven behavior.

Schneider-specific coupling found in the original code:

- `executor/target_resolver.py` only resolved Schneider careers listings;
- `executor/cli.py` contained Schneider command routing and Schneider-specific local config paths;
- `executor/adapters/schneider_boss.py` contains BOSS/Schneider API paths, field IDs, dictionaries, upload serialization and project-specific decisions;
- `config/schneider-profile.json` and `config/schneider-decisions.json` duplicated facts outside the generic candidate profile.

The adapter-specific API and field-code logic is appropriate inside the Schneider adapter. The duplication of applicant facts and company routing in the old compatibility CLI is not the target architecture and is retained only for regression/reference.

## Applicant evidence audit

The old generic profile was a small hand-maintained JSON and did not read the MAX document or resume.

The migrated evidence layer now reads:

- the MAX DOCX as the primary factual source;
- the legacy candidate profile as lower-confidence compatibility evidence;
- the final resume PDF as an evidence document and structured project source;
- local resume/photo assets by path and SHA-256;
- scoped historical application JSON without automatically promoting raw site answers.

At the time of migration the generated local canonical profile contained 56 canonical fields, 5 structured projects, 2 managed assets and no source conflicts. Fields explicitly marked pending in MAX, such as student origin, remain absent rather than inferred.

## Migrated architecture

Primary path:

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

New reusable modules:

- `executor/models.py`: canonical profile, field-resolution, plan, state and verification models;
- `executor/evidence.py`: provenance-aware profile construction and historical-source import;
- `executor/resolver.py`: deterministic mapping, ambiguity rules and DeepSeek semantic mapping;
- `executor/application.py`: site-independent execution/state machine;
- `executor/audit.py`: per-execution records with redaction;
- `executor/recovery.py`: recovery of non-terminal executions;
- `executor/adapters/base.py`: adapter contract;
- `executor/adapters/generic_web.py`: DOM/accessibility-first default adapter;
- `executor/adapters/registry.py`: platform routing without equating a shared platform with one company;
- `executor/protected_targets.py`: generic immutable-target safety barrier.

The new primary command surface is `./scripts/application.sh`. The old `executor.cli` path remains for compatibility with the already-verified Schneider implementation.

## Resolution and authorization semantics

Fact precedence is enforced independently from semantic interpretation.

- Explicit/user-confirmed facts outrank document evidence.
- MAX/resume evidence outranks explicitly canonicalized historical application facts.
- Site-existing values are preserved only when stronger canonical evidence is unavailable.
- DeepSeek may identify field meaning but cannot supply a personal value.
- Known but missing canonical fields are reported as missing, not guessed.
- Generic education labels that do not identify degree level remain unresolved.
- Legal/compliance/signature/work-authorization/salary questions require user confirmation even when optional.

Filling and submission are separate. A target can reach `READY_TO_SUBMIT` without submission authorization. Recovery never inherits submission authorization.

## Schneider safety boundary

The BOSS campus domain is treated as a shared platform (`boss_campus`), not as Schneider.

The exact already-submitted Schneider targets are stored only in local, gitignored `config/protected-targets.json`. The generic registry refuses those exact identifiers before browser mutation, while another employer on the same BOSS platform remains eligible for normal adapter handling.

No live Schneider save/upload/submit action was executed during this migration.

## Validation and verification

The generic adapter now:

- overwrites stale site values when a stronger canonical fact exists;
- validates required controls after filling;
- checks deterministic page values against the canonical plan where control semantics permit;
- detects authentication/CAPTCHA/MFA-style blockers conservatively;
- saves a draft when a unique safe draft control exists before stopping for user input;
- never promotes a success-page string beyond verification level `page_signal`.

Only an adapter that independently reads an application API/history/server record can promote a submitted application to `VERIFIED`.

## Audit and privacy

Each new execution is isolated under `applications/<execution-id>/`.

Text audit output redacts credential-like data and masks sensitive applicant values. The canonical profile, protected-target registry, settings, runtime state, browser profile and execution records are gitignored. Local canonical/audit files are written with restrictive permissions.

API keys remain in macOS Keychain. The migrated DeepSeek mapper reuses the existing `AI-Supervisor-DeepSeek` keychain service and the existing non-secret model/proxy configuration.

## Verification performed during migration

- Original baseline: 7 tests passed; 1 browser regression failed because Playwright defaults were incompatible with the existing CDP Chrome session.
- CDP connection path was corrected without replacing the dedicated browser profile.
- Final automated regression suite: 26 tests passed.
- A real local `file://` smoke form was run through the actual dedicated Chrome/CDP path with the generated canonical profile.
- The smoke form deliberately contained a stale wrong value and a missing personal fact.
- Known values were filled/overwritten, the resume was attached, the draft was saved, the missing fact stayed blank, and no submit action occurred.
- Text audit inspection found no raw sensitive canonical value leakage.

## Known remaining adapter work

The generic adapter is deliberately conservative. Complex SPA components such as custom cascaders, proprietary date pickers, virtualized selectors, custom upload protocols and platform-specific server verification may require a thin site adapter.

No new external company/school application was submitted as part of this migration. The next real target should be used as the first adapter-onboarding case, with execution stopped at unresolved user decisions or `READY_TO_SUBMIT` unless that exact target has explicit submission authorization.

## User-answer continuation layer

Unresolved fields now have a first-class continuation path rather than requiring a new site-specific script.

- `answer` stores an explicit value in the same execution's private `user-answers.json`.
- By default the answer is application-scoped and does not mutate the reusable applicant profile.
- `--promote-profile` is available only for facts the user wants to make canonical across future applications.
- Profile rebuild preserves explicit canonical user overrides.
- Recovery reuses the same execution ID and user-answer file, while submission authorization is deliberately not inherited.

A local smoke execution was continued this way from `BLOCKED` to `READY_TO_SUBMIT`. The synthetic answer remained execution-scoped, no submit action was recorded, and the canonical profile was unchanged.
