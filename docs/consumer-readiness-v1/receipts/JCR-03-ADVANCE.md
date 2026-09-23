# JCR-03 advance receipt — bounded discovery support

Canonical package: `JAE-CONSUMER-READINESS-v1`. Proposed status: **ADVANCE_ALLOWED_WITH_DEFERRED**, not COMPLETE and not consumer certified. JCR-04 durable facts may proceed independently; JCR-03's unsupported platform paths remain disabled and DFG-003 remains engineering debt.

## Authority and integration

- Base remote main: `959cd9d2faf22b2620f5055362e391b7f3b899ad`, after JCR-02 merge and successful exact-main CI. Sole JCR-03 writer: draft PR [#13](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/13), `feat/jcr03-discovery-identity`.
- Final PR head, merge SHA and exact-main readback belong in a later main-integration receipt, not in this pre-merge receipt.
- The versioned contract is JCR-03 in `05_ROUNDS.md`, target-identity ADR in `02_ARCHITECTURE.md`, and B-01–B-13 in `04_ACCEPTANCE_MATRIX.md`.

## Automatic and public evidence

- Company plus exact role works without a pasted URL for supported official OPPO campus discovery. Public page classification distinguishes its landing, listing and detail routes. Listing pagination validates total/pages/current/count and stable job IDs; exact candidates are checked against public detail ID/title/location/campaign/status. A positive public read-only OPPO target and a synthetic zero-result query were observed using a temporary isolated headless browser, with no applicant data or application action.
- An incomplete page, changed count, duplicate ID, mismatched detail, cross-origin redirect or failed observation cannot yield a verified target. Requested title, location, campaign, employment type and exact detail URL identity must all match. Multiple exact candidates remain unqueued until an explicit selection; selection performs fresh discovery.
- A real isolated consumer browser journey verified two candidates, no task before selection and one disabled-for-live-write task after selection. Local company/role chat phrases bypass the model and use the same discovery result. The server performs public discovery outside its mutation lock, then enqueues under its lock.
- Shared Greenhouse/Lever ATS route tests require an employer-official observed link plus the same platform, tenant and job ID. Tenant/job/campaign scopes queue identity and protected submitted targets. Legacy protected URLs, OPPO route aliases, Greenhouse board aliases and same ID across different tenants have targeted regressions. Safe SPA hash job routes survive; token-like fragments are rejected.
- The source and capability boundary for four platform mechanisms is frozen in `JCR-03-PUBLIC-RECON.md`. Schneider's public listing contract could not prove coverage and therefore remains INCOMPLETE under DFG-003.
- Local full isolated/headless suite after target-source, alias and migration repairs: **320 passed in 49.96 seconds**. Exact-head CI for the final receipt/status commit must pass before merge; this receipt does not claim it early.

## Unverified and rollback

- **No B-01–B-13 final-live case is marked PASS.** The tests above are scoped AUTO/PUBLIC evidence. Actual willing-to-apply target choice, real account/auth redirects, a real draft, and any final application side effect remain NOT_RUN. B-09 return-target after login is dependent on later auth/recovery work; unknown identity keeps the task blocked. Unsupported employers and ATS source chains remain read-only/unsupported.
- DFG-003 blocks Schneider unique-target verification. DFG-001 fact persistence and DFG-002 unknown browser outcome remain open, with their exact dependent actions blocked. These do not prevent JCR-04 facts or later independent work.
- New target metadata is additive in task JSON. A synthetic old-spec row retained the same task ID and parsed with safe defaults after restart; a new verified identity and source chain survived another restart. A pre-JCR-03 binary that rejects new fields or ignores tenant/protection evidence is **not** a safe live rollback target. Rollback must preserve tasks and use a compatible reader or remain read-only until compatible migration is proven; never silently reinterpret a verified task as another posting.
- No real account, private profile, OTP, upload, paid service, new permission or final submit was used.
