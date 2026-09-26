from __future__ import annotations

import re
import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import browser
from .manager import safe_task_view
from .release import (is_packaged_source, read_release_identity,
                      verify_runtime_candidate, RUNTIME_MANIFEST_NAME)


def _git(repo: Path, *args: str, timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _current_packaged_source(repo_root: str | Path) -> dict:
    supplied = Path(repo_root).expanduser().absolute()
    try:
        # A damaged/aliased installed app is not permission to fall back to Git
        # or hash arbitrary host files through a replaced path component.
        if any(path.is_symlink() for path in (supplied, *supplied.parents)):
            return {"status": "unverified", "source_sha256": ""}
        identity = read_release_identity(supplied)
        digest = identity.get("source_sha256")
        if (identity.get("status") == "verified" and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest)):
            return {"status": "verified", "source_sha256": digest}
    except (OSError, ValueError, RuntimeError, TypeError):
        pass
    return {"status": "unverified", "source_sha256": ""}


def packaged_provenance(repo_root: str | Path) -> dict | None:
    """Current payload integrity, distinct from the process's startup identity.

    Hash only declared app source/runtime payloads. Never execute the candidate
    interpreter, consult Git, read applicant state, or call a remote service.
    The runtime manifest is an integrity receipt, not proof that a new process
    successfully loaded its dependency lock or that distribution is signed.
    """
    supplied = Path(repo_root).expanduser().absolute()
    if not is_packaged_source(supplied):
        return None
    source = _current_packaged_source(supplied)
    source_ok = source["status"] == "verified"
    report = {
        "source_verified_now": source_ok,
        "source_sha256": source["source_sha256"],
        "runtime_verified_now": False,
        "runtime_sha256": "",
        "requirements_sha256": "",
        "interpreter_owned": False,
        "verification_scope": "payload_integrity_only",
        "signed_distribution_certified": False,
    }
    if not source_ok:
        return report
    # Recognize the installed sibling layout. A source-only staging manifest
    # cannot prove interpreter ownership merely by naming another host folder.
    if not (supplied.name == "release" and supplied.parent.name == "Resources"
            and supplied.parent.parent.name == "Contents"
            and supplied.parent.parent.parent.name.endswith(".app")):
        return report
    runtime = supplied.parent / "runtime"
    try:
        manifest_file = runtime / RUNTIME_MANIFEST_NAME
        if runtime.is_symlink() or manifest_file.is_symlink():
            return report
        manifest_text = manifest_file.read_text(encoding="utf-8")
        if not verify_runtime_candidate(runtime, supplied):
            return report
        if manifest_file.read_text(encoding="utf-8") != manifest_text:
            return report
        manifest = json.loads(manifest_text)
        runtime_digest = manifest.get("runtime_sha256")
        requirements_digest = manifest.get("requirements_sha256")
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in (runtime_digest, requirements_digest)):
            return report
        # Re-read source after the runtime hash: a concurrent source edit may
        # invalidate its requirement binding while the report is being built.
        if _current_packaged_source(supplied) != source:
            report.update(source_verified_now=False, source_sha256="")
            return report
        root = runtime.resolve()
        report.update(
            runtime_verified_now=True, runtime_sha256=runtime_digest,
            requirements_sha256=requirements_digest,
            interpreter_owned=all(Path(path).resolve().is_relative_to(root)
                                  for path in (sys.executable, sys.prefix, sys.base_prefix)),
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, TypeError):
        pass
    return report


def repository_state(repo_root: str | Path) -> dict[str, Any]:
    supplied = Path(repo_root).expanduser().absolute()
    if is_packaged_source(supplied):
        identity = _current_packaged_source(supplied)
        digest = identity["source_sha256"]
        return {"version": digest[:12] if digest else "unknown",
                "branch": "packaged", "worktree_clean": None}
    repo = supplied.resolve()
    sha = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "branch", "--show-current")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {
        "version": sha[:12] if re.fullmatch(r"[0-9a-fA-F]{40}", sha or "") else "unknown",
        "branch": "main" if branch == "main" else "other" if branch else "unknown",
        "worktree_clean": None if dirty is None else dirty == "",
    }


def collect_diagnostics(supervisor, *, repo_root: str | Path) -> dict[str, Any]:
    """Return a copy-safe diagnostic report with no applicant values or secrets."""
    raw_tasks = supervisor.queue.tasks()
    tasks = []
    for task in raw_tasks:
        safe = safe_task_view(task)
        tasks.append(
            {
                "task": safe["task_id"][:8],
                # Company/role/host are user-supplied strings and can contain
                # arbitrary applicant facts. Diagnostics are meant to be copied.
                "stage": safe["stage"],
                "attempts": safe["attempts"],
            }
        )

    events = []
    for event in supervisor.queue.recent_events(12):
        task_id = str(event.get("task_id") or "")
        kind = str(event.get("kind") or "")
        stage = str(event.get("stage") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{0,80}", kind):
            kind = "unknown"
        if not re.fullmatch(r"[A-Z_]{0,80}", stage):
            stage = "unknown"
        events.append(
            {
                "task": task_id[:8] if re.fullmatch(r"[A-Za-z0-9]{8,120}", task_id) else "",
                "kind": kind,
                "stage": stage,
            }
        )

    waiting = [
        task
        for task in raw_tasks
        if task.get("stage") == "NEEDS_USER_ACTION"
        and task.get("blocker") in {"otp_waiting", "otp_ambiguous"}
    ]
    buffered = 0
    for task in waiting:
        try:
            buffered += int(bool(supervisor.worker.broker.pending(task["task_id"])))
        except Exception:
            pass

    try:
        # Diagnostics observe the service's already-loaded provider. Building
        # another mapper would re-read Keychain secrets and can prompt/block
        # merely because the user asked to preview a copy-safe report.
        provider_state = supervisor.manager.loaded_provider_state()
        deepseek_available = provider_state["available"] is True
    except Exception:
        provider_state = {"state": "unavailable", "available": False}
        deepseek_available = False

    try:
        candidate_mode = browser.browser_mode()
        browser_mode_value = candidate_mode if candidate_mode in {"live", "isolated", "test", "headless"} else "unknown"
    except Exception:
        browser_mode_value = "unknown"
    try:
        cdp_alive = bool(browser._alive())
    except Exception:
        cdp_alive = False

    state = repository_state(repo_root)
    identity = getattr(supervisor, "release_identity", {})
    digest = identity.get("source_sha256") if isinstance(identity, dict) else ""
    verified = (
        isinstance(identity, dict)
        and identity.get("status") == "verified"
        and isinstance(digest, str)
        and bool(re.fullmatch(r"[0-9a-f]{64}", digest))
    )
    payload = packaged_provenance(repo_root)
    matches_disk = (payload["source_verified_now"] and verified
                    and payload["source_sha256"] == digest) if payload is not None else None
    if not verified or (payload is not None and not payload["source_verified_now"]):
        recovery = {"reason": "release_unverified", "action": "reinstall_verified_app"}
    elif payload is not None and not matches_disk:
        recovery = {"reason": "release_changed", "action": "restart_verified_app"}
    elif payload is not None and not payload["runtime_verified_now"]:
        recovery = {"reason": "runtime_unverified", "action": "reinstall_verified_app"}
    elif not cdp_alive and browser_mode_value == "live":
        recovery = {"reason": "owned_browser_unavailable", "action": "reconnect_owned_browser"}
    elif not deepseek_available:
        recovery = {"reason": "provider_unavailable", "action": "check_provider_settings"}
    else:
        recovery = {"reason": "none", "action": "none"}
    return {
        "format": "application-executor-diagnostics-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recovery": recovery,
        "repository": state,
        "loaded_source": {
            "verified_at_start": verified,
            "sha256": digest if verified else "",
        },
        **({"packaged_release": {**payload, "loaded_source_matches_disk": matches_disk}}
           if payload is not None else {}),
        "system": {
            "supervisor": True,
            "worker_active": bool(supervisor.worker.active),
            "browser_mode": browser_mode_value,
            "cdp_alive": cdp_alive,
            "deepseek_available": bool(deepseek_available),
            "provider_state_basis": "loaded_configuration",
            "provider_state": provider_state["state"],
            "otp_waiting_tasks": len(waiting),
            "otp_buffered_tasks": buffered,
        },
        "tasks": tasks,
        "recent_events": events,
        "safety": {
            "final_click_actor": "user",
            "submit_capability": False,
            "raw_otp_in_report": False,
            "credentials_in_report": False,
            "profile_values_in_report": False,
        },
    }
