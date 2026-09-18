from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path.home() / "Job-Application-Executor"
PROTECTED_TARGETS = ROOT / "config" / "protected-targets.json"


def load_protected_targets(path: str | Path = PROTECTED_TARGETS) -> list[dict]:
    path = Path(path).expanduser()
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("targets") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def protected_target(target_url: str, items: list[dict] | None = None) -> dict | None:
    parsed = urlparse(target_url)
    query = parse_qs(parsed.query)
    for item in items if items is not None else load_protected_targets():
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


def assert_target_not_protected(target_url: str) -> None:
    hit = protected_target(target_url)
    if hit:
        label = hit.get("label") or "protected submitted application"
        status = hit.get("status") or "protected"
        raise RuntimeError(f"target is protected from mutation: {label} ({status})")
