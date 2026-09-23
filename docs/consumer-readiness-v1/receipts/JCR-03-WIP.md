# JCR-03 implementation checkpoint

Canonical package: `JAE-CONSUMER-READINESS-v1`. Status: **IMPLEMENTING**, not complete or consumer certified.

- Base: remote main `959cd9d2faf22b2620f5055362e391b7f3b899ad`, JCR-02 exact-main CI run `35908127690` successful. Sole writer: `feat/jcr03-discovery-identity`.
- `DiscoveryRequest → DiscoverySnapshot → DiscoveryResult/VerifiedJobTarget` now requires a complete candidate set, official source, exact title and all requested constraints. Multiple exact matches need an explicit candidate choice and fresh discovery before a task is created.
- The consumer local form accepts company plus role without URL, optional location/campaign/employment type, and presents candidates. A real isolated headless UI test proves two matches create zero tasks until a choice is made. A narrow explicit company/role chat phrase is handled locally without sending it to DeepSeek.
- OPPO public discovery uses the observed official listing query and verifies every page count/ID and exact-match detail ID/title/location/campaign/status. It runs in a temporary headless browser, never the applicant profile. Read-only synthetic zero-result and public positive list/detail smokes completed; no application was made. A changed or incomplete listing fails closed.
- Shared ATS route proof recognizes Greenhouse and Lever tenant/job route shapes only when a caller has observed the link from the expected official employer page. Queue identities include tenant/job/campaign; protected submitted identities cover URL aliases. Plain job hash routes are preserved while secret-bearing fragments are rejected.
- Targeted historical safety/queue and manager regressions passed. Full isolated suite at this checkpoint: **317 passed in 49.82 seconds** before the last source-path tightening; rerun the exact final head before merge.
- Schneider public listing count/page contract remains unproven and emits INCOMPLETE, not a false zero-match. DFG-003 records the blocked Schneider verification path. DFG-001 and DFG-002 remain open as documented.

Still required in this round: an exact final-head full suite and CI; explicit URL/source-chain and stale candidate fault review; final B-class synthetic/public evidence and per-case status; review feedback closure; merge and exact-main CI/readback. B-01–B-13 final-live evidence remains NOT_RUN.
