"""Pure, read-only proof of official employer links into shared ATS tenants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ATSRoute:
    platform: str
    tenant: str
    job_id: str
    canonical_url: str


def parse_ats_route(url: str) -> ATSRoute | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        return None
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        if not parts or not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", parts[0]):
            return None
        if len(parts) == 1:
            job_id = ""
        elif len(parts) == 3 and parts[1] == "jobs" and re.fullmatch(r"\d{3,20}", parts[2]):
            job_id = parts[2]
        else:
            return None
        canonical = f"https://{host}/{parts[0]}" + (f"/jobs/{job_id}" if job_id else "")
        return ATSRoute("greenhouse", parts[0].casefold(), job_id, canonical)
    if host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        if not parts or not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", parts[0]):
            return None
        if len(parts) == 1:
            job_id = ""
        elif len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9-]{8,100}", parts[1]):
            job_id = parts[1]
        else:
            return None
        canonical = f"https://{host}/{parts[0]}" + (f"/{job_id}" if job_id else "")
        return ATSRoute("lever", parts[0].casefold(), job_id, canonical)
    return None


def verified_official_ats_target(*, official_source_url: str,
                                 official_host: str, observed_link: str,
                                 candidate_url: str) -> ATSRoute | None:
    """Caller must have observed the link on the named official employer page."""
    try:
        source = urlsplit(official_source_url)
    except ValueError:
        return None
    if (source.scheme != "https" or (source.hostname or "").casefold() != official_host.casefold()
            or source.username or source.password):
        return None
    linked = parse_ats_route(observed_link)
    candidate = parse_ats_route(candidate_url)
    if linked is None or candidate is None or not candidate.job_id:
        return None
    if linked.platform != candidate.platform or linked.tenant != candidate.tenant:
        return None
    if linked.job_id and linked.job_id != candidate.job_id:
        return None
    return candidate
