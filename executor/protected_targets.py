from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .discovery.core import normalize_component
from .discovery.ats_routes import parse_ats_route


ROOT = Path.home() / "Job-Application-Executor"
PROTECTED_TARGETS = ROOT / "config" / "protected-targets.json"


def _url_job_key(url: str) -> tuple[str, str, str] | None:
    route = parse_ats_route(url)
    if route and route.job_id:
        return route.platform, route.tenant, route.job_id
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host == "careers.oppo.com":
        match = re.search(r"/university/oppo/campus/post/(\d+)(?:/|$)", parsed.path)
        if match:
            return "oppo", "oppo-campus", match.group(1)
    query = parse_qs(parsed.query)
    ids = {value for key, values in query.items()
           if key.casefold() in {"postid", "jobid", "positionid", "encryptjobid"}
           for value in values}
    if len(ids) == 1 and host:
        return "host", host, next(iter(ids))
    return None


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
    target_key = _url_job_key(target_url)
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
        exact_urls = {str(x) for x in item.get("exact_urls", [])}
        if target_key and any(_url_job_key(protected_url) == target_key for protected_url in exact_urls):
            return item
        hosts = {str(x).casefold() for x in item.get("hosts", [])}
        if hosts and (parsed.hostname or "").casefold() not in hosts:
            continue
        if exact_urls and target_url in exact_urls:
            return item
        query_match = item.get("query_any") or {}
        for key, allowed in query_match.items():
            current = set(query.get(str(key), []))
            expected = {str(x) for x in allowed}
            if current & expected:
                return item
            if (target_key and str(key).casefold() in {"postid", "jobid", "positionid", "encryptjobid"}
                    and target_key[2] in expected):
                return item
    return None


def assert_target_not_protected(target_url: str, *, tenant: str = "", job_id: str = "",
                                campaign: str = "") -> None:
    hit = protected_target(target_url, tenant=tenant, job_id=job_id, campaign=campaign)
    if hit:
        label = hit.get("label") or "protected submitted application"
        status = hit.get("status") or "protected"
        raise RuntimeError(f"target is protected from mutation: {label} ({status})")


def register_user_confirmed_target(
    target_url: str, *, tenant: str, job_id: str, campaign: str = "",
    verified_identity: bool, user_confirmed: bool,
    path: str | Path = PROTECTED_TARGETS,
) -> bool:
    """Protect a verified target only after explicit human submission confirmation.

    The file stores a stable job identity, never the application URL or its
    query. Existing protection is idempotent. A page signal alone cannot call
    this with user_confirmed=True.
    """
    if verified_identity is not True or user_confirmed is not True:
        raise ValueError("verified target and explicit human confirmation required")
    if (not isinstance(tenant, str) or not tenant.strip()
            or not isinstance(job_id, str) or not job_id.strip()
            or not isinstance(campaign, str)):
        raise ValueError("verified job identity required")
    parsed = urlparse(target_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("verified HTTPS target required")
    route = parse_ats_route(target_url)
    if route and (route.tenant.casefold() != tenant.casefold() or route.job_id != job_id):
        raise ValueError("verified target differs from job route")

    path = Path(path).expanduser()
    parent = path.parent
    if parent.is_symlink():
        raise ValueError("protected target directory cannot be a symlink")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent.chmod(0o700)
    if path.is_symlink():
        raise ValueError("protected target file cannot be a symlink")
    lock_path = parent / (path.name + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink():
            raise ValueError("protected target file cannot be a symlink")
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
                raise ValueError("protected target registry is malformed")
            if path.stat().st_mode & 0o077:
                raise ValueError("protected target registry must be private")
        else:
            data = {"targets": []}
        items = data["targets"]
        if protected_target(target_url, items, tenant=tenant, job_id=job_id,
                            campaign=campaign):
            return False
        items.append({
            "label": "user-confirmed submitted application",
            "status": "USER_CONFIRMED",
            "target_identity": {
                "tenant": tenant,
                "job_id": job_id,
                "campaign": campaign,
            },
        })
        temporary = parent / (path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return True
