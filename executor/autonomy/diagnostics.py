from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .. import browser
from ..resolver import DeepSeekMapper
from .manager import safe_task_view


def _git(repo: Path, *args: str, timeout: int = 5) -> str:
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
        return ""


def repository_state(repo_root: str | Path) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    sha = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "branch", "--show-current")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {
        "version": sha[:12] if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else "unknown",
        "branch": branch if re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", branch or "") else "unknown",
        "worktree_clean": dirty == "",
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
        deepseek_available = DeepSeekMapper(supervisor.worker.settings).available
    except Exception:
        deepseek_available = False

    try:
        browser_mode_value = browser.browser_mode()
    except Exception:
        browser_mode_value = "unknown"
    try:
        cdp_alive = bool(browser._alive())
    except Exception:
        cdp_alive = False

    state = repository_state(repo_root)
    return {
        "format": "application-executor-diagnostics-v1",
        "repository": state,
        "system": {
            "supervisor": True,
            "worker_active": bool(supervisor.worker.active),
            "browser_mode": browser_mode_value,
            "cdp_alive": cdp_alive,
            "deepseek_available": bool(deepseek_available),
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
