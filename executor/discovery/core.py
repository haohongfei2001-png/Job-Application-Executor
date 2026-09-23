from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlsplit


def normalize_component(value: str) -> str:
    return re.sub(r"[\s·・_/（）()【】\[\]—–-]+", "", unicodedata.normalize("NFKC", value).casefold())


_norm = normalize_component

def _location_match(wanted: str, observed: str) -> bool:
    if not wanted:
        return True
    wanted = _norm(wanted).removesuffix("市")
    return any(_norm(part).removesuffix("市") == wanted for part in re.split(r"[/|,，、]", observed))


@dataclass(frozen=True)
class DiscoveryRequest:
    company: str
    role: str
    location: str = ""
    campaign: str = ""
    employment_type: str = ""
    source_url: str = ""

    def __post_init__(self):
        if not self.company.strip() or not self.role.strip():
            raise ValueError("company and role are required")
        if any(len(value) > limit for value, limit in (
            (self.company, 150), (self.role, 200), (self.location, 150),
            (self.campaign, 150), (self.employment_type, 100), (self.source_url, 2000),
        )):
            raise ValueError("discovery request too long")
        if self.source_url:
            parsed = urlsplit(self.source_url)
            if parsed.scheme not in {"https", "http", "file"} or parsed.username or parsed.password:
                raise ValueError("invalid discovery source URL")
            if parsed.scheme != "file" and not parsed.hostname:
                raise ValueError("discovery source host required")
            if parsed.fragment and not re.fullmatch(
                r"/(?:job|jobs|position|positions)/[A-Za-z0-9_-]{2,150}", parsed.fragment
            ):
                raise ValueError("unsupported or secret-bearing discovery fragment")
            if any(re.search(r"(?:token|secret|session|auth|password|otp|email|cookie|code)", key, re.I)
                   for key, _ in parse_qsl(parsed.query)):
                raise ValueError("secret-bearing discovery URL")


@dataclass(frozen=True)
class DiscoveryCandidate:
    employer: str
    tenant: str
    job_id: str
    title: str
    location: str
    campaign: str
    employment_type: str
    detail_url: str
    platform: str

    @property
    def candidate_id(self) -> str:
        payload = [self.tenant, self.job_id, self.campaign, self.location, self.employment_type]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:24]

    @property
    def target_key(self) -> str:
        return f"{self.tenant.casefold()}:{self.job_id}:{_norm(self.campaign)}"


@dataclass(frozen=True)
class DiscoverySnapshot:
    candidates: tuple[DiscoveryCandidate, ...]
    complete: bool
    source_chain: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    observed_at: str
    coverage: str
    source_verified: bool


@dataclass(frozen=True)
class VerifiedJobTarget:
    employer: str
    tenant: str
    job_id: str
    campaign: str
    title: str
    location: str
    employment_type: str
    detail_url: str
    platform: str
    source_chain: tuple[str, ...]
    allowed_navigation_hosts: tuple[str, ...]
    evidence_digest: str

    @property
    def target_key(self) -> str:
        return f"{self.tenant.casefold()}:{self.job_id}:{_norm(self.campaign)}"


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    candidates: tuple[DiscoveryCandidate, ...]
    target: VerifiedJobTarget | None
    coverage: str
    reason: str = ""

    def public_dict(self) -> dict:
        return {
            "status": self.status,
            "candidates": [
                {**asdict(candidate), "candidate_id": candidate.candidate_id}
                for candidate in self.candidates
            ],
            "target": asdict(self.target) if self.target else None,
            "coverage": self.coverage,
            "reason": self.reason,
        }


def evaluate_snapshot(request: DiscoveryRequest, snapshot: DiscoverySnapshot,
                      *, selected_candidate_id: str = "") -> DiscoveryResult:
    """A single observed match is never proof of a unique target."""
    if not snapshot.source_verified or not snapshot.source_chain:
        return DiscoveryResult("UNSUPPORTED", (), None, snapshot.coverage, "official_source_unverified")
    matches = []
    source_path = urlsplit(request.source_url).path if request.source_url else ""
    source_job = re.search(r"/(?:post|jobs)/(\d+)(?:/|$)", source_path)
    for candidate in snapshot.candidates:
        parsed = urlsplit(candidate.detail_url)
        if (
            not candidate.employer or not candidate.tenant or not candidate.job_id
            or parsed.scheme != "https" or (parsed.hostname or "").casefold() not in snapshot.allowed_hosts
            or parsed.username or parsed.password
        ):
            continue
        if _norm(candidate.employer) != _norm(request.company):
            continue
        if _norm(candidate.title) != _norm(request.role):
            continue
        if source_job and candidate.job_id != source_job.group(1):
            continue
        if not _location_match(request.location, candidate.location):
            continue
        if request.campaign and _norm(request.campaign) != _norm(candidate.campaign):
            continue
        if request.employment_type and _norm(request.employment_type) != _norm(candidate.employment_type):
            continue
        matches.append(candidate)
    matches.sort(key=lambda item: (item.title.casefold(), item.location.casefold(), item.campaign.casefold(), item.job_id))
    if not snapshot.complete:
        return DiscoveryResult("INCOMPLETE", tuple(matches), None, snapshot.coverage, "candidate_coverage_unproven")
    if not matches:
        return DiscoveryResult("UNAVAILABLE", (), None, snapshot.coverage, "no_exact_available_target")
    if selected_candidate_id:
        selected = [item for item in matches if item.candidate_id == selected_candidate_id]
        if len(selected) != 1:
            return DiscoveryResult("UNAVAILABLE", tuple(matches), None, snapshot.coverage, "selected_candidate_changed")
        choice = selected[0]
    elif len(matches) == 1:
        choice = matches[0]
    else:
        return DiscoveryResult("AMBIGUOUS", tuple(matches), None, snapshot.coverage, "explicit_candidate_selection_required")
    evidence = {
        "request": asdict(request), "candidate": asdict(choice),
        "source_chain": snapshot.source_chain, "coverage": snapshot.coverage,
        "observed_at": snapshot.observed_at,
    }
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    target = VerifiedJobTarget(
        employer=choice.employer, tenant=choice.tenant, job_id=choice.job_id,
        campaign=choice.campaign, title=choice.title, location=choice.location,
        employment_type=choice.employment_type, detail_url=choice.detail_url,
        platform=choice.platform, source_chain=snapshot.source_chain,
        allowed_navigation_hosts=snapshot.allowed_hosts, evidence_digest=digest,
    )
    return DiscoveryResult("VERIFIED", tuple(matches), target, snapshot.coverage)
