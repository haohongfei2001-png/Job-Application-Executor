from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .discovery.core import normalize_component


ROOT = Path.home() / "Job-Application-Executor"
PROTECTED_TARGETS = ROOT / "config" / "protected-targets.json"


def load_protected_targets(path: str | Path = PROTECTED_TARGETS) -> list[dict]:
    path = Path(path).expanduser()
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("targets") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def protected_target(target_url: str, items: list[dict] | None = None, *,
                     tenant: str = "", job_id: str = "", campaign: str = "") -> dict | None:
    parsed = urlparse(target_url)
    query = parse_qs(parsed.query)
    for item in items if items is not None else load_protected_targets():
        # A verified tenant/job identity protects URL aliases on shared ATS
        # hosts. Older host/query rules remain valid for legacy targets.
        target_identity = item.get("target_identity")
        if isinstance(target_identity, dict) and tenant and job_id:
            if (
                str(target_identity.get("tenant", "")).casefold() == tenant.casefold()
                and str(target_identity.get("job_id", "")) == job_id
                and normalize_component(str(target_identity.get("campaign", ""))) == normalize_component(campaign)
            ):
                return item
        hosts = {str(x).casefold() for x in item.get("hosts", [])}
        if hosts and (parsed.hostname or "").casefold() not in hosts:
            continue
        exact_urls = {str(x) for x in item.get("exact_urls", [])}
        if exact_urls and target_url in exact_urls:
            return item
        query_match = item.get("query_any") or {}
        for key, allowed in query_match.items():
            current = set(query.get(str(key), []))
            expected = {str(x) for x in allowed}
            if current & expected:
                return item
    return None


def assert_target_not_protected(target_url: str, *, tenant: str = "", job_id: str = "",
                                campaign: str = "") -> None:
    hit = protected_target(target_url, tenant=tenant, job_id=job_id, campaign=campaign)
    if hit:
        label = hit.get("label") or "protected submitted application"
        status = hit.get("status") or "protected"
        raise RuntimeError(f"target is protected from mutation: {label} ({status})")
