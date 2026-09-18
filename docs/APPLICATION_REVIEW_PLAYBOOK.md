# Application Review Playbook

This playbook defines mandatory behavior for all job-application executions.

## 1. Final-action boundary

The executor may perform every reversible/preparatory step it can safely complete: navigation, field filling, dropdown selection, material upload, privacy/data-processing assent under standing policy, factual validation, draft saving, and pre-submit review.

The executor must stop at the final irreversible application action. The user personally clicks controls such as `Submit`, `Submit application`, `确认提交`, `确认投递`, `正式投递`, or the equivalent final update action for an already-submitted application.

`submit_authorized` never bypasses this boundary. It is only an exact-target authorization marker.

After the user performs the final click, the executor may resume verification work: read success pages, inspect server-backed application history/API records, capture application IDs/timestamps/status, and mark the execution `VERIFIED`.

## 2. Mandatory pre-submit review

Before asking the user to perform the final click, produce a concise review of:
- exact company / campaign / role / position identifier;
- work location / business unit / interview city when applicable;
- required fields and any site-specific declarations;
- education, dates, ranking and employment/internship claims;
- uploaded attachments and their exact filenames;
- structured project/activity rows;
- any site-specific representation compromise;
- unresolved or explicitly excluded canonical facts/projects.

The review must be based on the current form/server draft, not assumptions from the source resume alone.

## 3. Resume-parser rule

Resume parsing is an untrusted draft, never an authoritative import.

After parsing, audit every section. Typical failure modes include:
- product/project text misclassified as work or internship experience;
- education-program text misclassified as employment;
- language/certificate text misclassified as project experience;
- fields shifted between repeated rows;
- dates or ranking values silently dropped;
- correct attachment upload creating an incorrect structured resume.

Incorrect parsed rows must be deleted or repaired before proceeding.

## 4. Structured fields versus attachment

A resume attachment does not substitute for structured fields when the portal exposes them.

If the portal has a structured project/activity section, compare it against the canonical project inventory. Every canonical project must be:
1. included in the structured form;
2. explicitly excluded because of a user instruction, relevance decision, field limit, or missing factual data; or
3. surfaced as an uncovered project in the final review.

Silent omission is forbidden.

Research projects are projects. Do not drop research merely because the target role is product, strategy, AI, or management-oriented. Selection must be deliberate and visible in the review.

If the user explicitly excludes a project for a specific application, record that exclusion for the execution rather than deleting the project from the canonical profile.

## 5. Dates and site quirks

Never invent a missing month/date to satisfy a portal.

When a portal has a UI bug or restrictive representation:
- preserve the underlying truth;
- choose a site-compatible representation only if it does not create a false factual claim;
- disclose the representation in the final review;
- keep the authoritative attachment/canonical record unchanged.

If no truthful representation exists, stop and surface the missing fact.

## 6. Update-after-submission flow

Editing an already-submitted application follows the same boundary as a new application:
- the executor may prepare and validate the updated draft;
- the user performs the final update/confirm click;
- the executor then verifies that the original application record was updated rather than duplicated.

Verification should prefer server-backed application IDs, timestamps, status, or application history over page-only success signals.

## 7. Operational principle

The system should minimize questions by reusing canonical, user-confirmed facts and standing decision policies. The one deliberate human checkpoint that always remains is the final irreversible submit/update click.
