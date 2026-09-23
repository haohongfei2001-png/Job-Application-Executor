from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlsplit

from ..target_resolver import (
    OPPO_CAMPUS_ROOT, SCHNEIDER_ALIASES, _is_oppo_company,
    resolve_oppo, resolve_schneider,
)
from .core import (
    DiscoveryCandidate, DiscoveryRequest, DiscoveryResult,
    DiscoverySnapshot, evaluate_snapshot,
)


def _contract(request: DiscoveryRequest):
    name = request.company.strip().casefold()
    if _is_oppo_company(request.company):
        return "oppo", "OPPO", "oppo-campus", "careers.oppo.com", OPPO_CAMPUS_ROOT
    if name in SCHNEIDER_ALIASES:
        return "schneider", "Schneider Electric", "schneider", "careers.se.com", "https://careers.se.com/jobs"
    return None


def discover(request: DiscoveryRequest, *, selected_candidate_id: str = "") -> DiscoveryResult:
    contract = _contract(request)
    if contract is None:
        return DiscoveryResult("UNSUPPORTED", (), None, "", "no_official_site_contract")
    platform, employer, tenant, host, root = contract
    if request.source_url:
        parsed = urlsplit(request.source_url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() != host:
            return DiscoveryResult("UNSUPPORTED", (), None, "", "source_outside_official_contract")
        if platform == "oppo" and not re.fullmatch(
            r"/university/oppo/campus(?:/post(?:/\d+)?)?/?", parsed.path):
            return DiscoveryResult("UNSUPPORTED", (), None, "", "source_outside_official_contract")
        if platform == "schneider" and not re.fullmatch(r"/jobs(?:/\d+)?/?", parsed.path):
            return DiscoveryResult("UNSUPPORTED", (), None, "", "source_outside_official_contract")
    try:
        if platform == "oppo":
            items, complete = resolve_oppo(request.role, request.location, return_coverage=True)
        else:
            items, complete = resolve_schneider(request.role, request.location,
                                                return_coverage=True)
    except Exception:
        return DiscoveryResult("INCOMPLETE", (), None, root, "public_observation_unavailable")
    candidates = []
    for item in items:
        url = urlsplit(item.job_url)
        if url.scheme != "https" or (url.hostname or "").casefold() != host:
            continue
        campaign = ""
        employment_type = ""
        if platform == "oppo":
            recruit_type = parse_qs(url.query).get("recruitType", [""])[0]
            campaign = getattr(item, "campaign", "") or recruit_type
            employment_type = getattr(item, "employment_type", "") or ("campus" if recruit_type.casefold() == "campus" else "")
        candidates.append(DiscoveryCandidate(
            employer=employer, tenant=tenant, job_id=item.job_id,
            title=item.title, location=item.location, campaign=campaign,
            employment_type=employment_type, detail_url=item.job_url,
            platform=platform,
        ))
    snapshot = DiscoverySnapshot(
        candidates=tuple(candidates), complete=complete,
        source_chain=(root,), allowed_hosts=(host,),
        observed_at=datetime.now(timezone.utc).isoformat(),
        coverage=f"{platform}:public_listing:{'complete' if complete else 'unproven'}",
        source_verified=True,
    )
    canonical_request = DiscoveryRequest(
        company=employer, role=request.role, location=request.location,
        campaign=request.campaign, employment_type=request.employment_type,
        source_url=request.source_url,
    )
    return evaluate_snapshot(canonical_request, snapshot,
                             selected_candidate_id=selected_candidate_id)
