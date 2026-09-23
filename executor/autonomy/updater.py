from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


EXPECTED_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)"
    r"haohongfei2001-png/Job-Application-Executor(?:\.git)?$"
)


def _run(
    repo: Path,
    args: list[str],
    *,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        args,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        env=env,
    )


def _git(repo: Path, *args: str, timeout: int = 20) -> str:
    return _run(repo, ["git", *args], timeout=timeout).stdout.strip()


def _state_path(runtime: Path) -> Path:
    return runtime / "update-state.json"


def write_update_state(
    runtime: str | Path,
    status: str,
    *,
    old_version: str = "",
    new_version: str = "",
    reason: str = "",
) -> None:
    root = Path(runtime).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    allowed = {
        "idle",
        "checking",
        "up_to_date",
        "updating",
        "restarting",
        "success",
        "failed",
    }
    if status not in allowed:
        raise ValueError("invalid update status")
    safe_reason = reason if re.fullmatch(r"[a-z_]{0,80}", reason or "") else "update_failed"
    payload = {
        "status": status,
        "old_version": old_version[:12] if re.fullmatch(r"[0-9a-fA-F]{40}", old_version or "") else "",
        "new_version": new_version[:12] if re.fullmatch(r"[0-9a-fA-F]{40}", new_version or "") else "",
        "reason": safe_reason if status == "failed" else "",
    }
    path = _state_path(root)
    path.write_text(json.dumps(payload, ensure_ascii=False))
    path.chmod(0o600)


def read_update_state(runtime: str | Path) -> dict:
    path = _state_path(Path(runtime).expanduser().resolve())
    if not path.is_file():
        return {"status": "idle", "old_version": "", "new_version": "", "reason": ""}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"status": "failed", "old_version": "", "new_version": "", "reason": "state_invalid"}
    if not isinstance(data, dict):
        return {"status": "failed", "old_version": "", "new_version": "", "reason": "state_invalid"}
    status = str(data.get("status") or "")
    if status not in {
        "idle",
        "checking",
        "up_to_date",
        "updating",
        "restarting",
        "success",
        "failed",
    }:
        status = "failed"
    return {
        "status": status,
        "old_version": str(data.get("old_version") or "")[:12],
        "new_version": str(data.get("new_version") or "")[:12],
        "reason": str(data.get("reason") or "")[:80],
    }


def repository_update_preconditions(repo_root: str | Path) -> dict:
    repo = Path(repo_root).expanduser().resolve()
    try:
        branch = _git(repo, "branch", "--show-current")
        dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
        remote = _git(repo, "remote", "get-url", "origin")
        head = _git(repo, "rev-parse", "HEAD")
    except Exception:
        return {"ok": False, "reason": "git_state_unavailable"}

    if branch != "main":
        return {"ok": False, "reason": "not_on_main"}
    if dirty:
        return {"ok": False, "reason": "tracked_changes_present"}
    if not EXPECTED_REMOTE.fullmatch(remote):
        return {"ok": False, "reason": "unexpected_origin"}
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        return {"ok": False, "reason": "invalid_head"}
    return {"ok": True, "head": head}


def _tasks_safe_for_update(tasks: list[dict], *, now: float | None = None) -> tuple[bool, str]:
    runnable = {
        "DISCOVERED",
        "PROFILE_RESOLVED",
        "FORM_FILLED",
        "VALIDATED",
    }
    for task in tasks:
        if task.get("stage") == "NEEDS_USER_ACTION" and task.get("blocker") in {
            "otp_waiting",
            "otp_ambiguous",
        }:
            return False, "otp_in_flight"
        if task.get("owner") and (
            now is None or float(task.get("lease_until") or 0) > now
        ):
            return False, "worker_active"
        if task.get("stage") in runnable:
            return False, "runnable_task_pending"
        if task.get("stage") == "ERROR" and task.get("blocker") == "retry_pending":
            return False, "runnable_task_pending"
    return True, ""


def safe_to_update(supervisor) -> tuple[bool, str]:
    if supervisor.worker.active:
        return False, "worker_active"
    return _tasks_safe_for_update(
        supervisor.queue.tasks(),
        now=supervisor.queue.clock(),
    )


def runtime_safe_to_update(runtime: str | Path) -> tuple[bool, str]:
    try:
        queue = TaskQueue(Path(runtime).expanduser().resolve())
        return _tasks_safe_for_update(queue.tasks(), now=queue.clock())
    except Exception:
        return False, "runtime_state_unavailable"


def spawn_update(
    *,
    repo_root: str | Path,
    runtime: str | Path,
    port: int,
    python_executable: str | None = None,
) -> dict:
    repo = Path(repo_root).expanduser().resolve()
    runtime_path = Path(runtime).expanduser().resolve()
    pre = repository_update_preconditions(repo)
    if not pre.get("ok"):
        return {"ok": False, "status": "denied", "reason": pre["reason"]}

    write_update_state(runtime_path, "checking", old_version=pre["head"])
    log = runtime_path / "updater.log"
    runtime_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with log.open("ab") as stream:
        log.chmod(0o600)
        subprocess.Popen(
            [
                python_executable or sys.executable,
                "-m",
                "executor.autonomy.updater",
                "--repo",
                str(repo),
                "--runtime",
                str(runtime_path),
                "--port",
                str(port),
            ],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    return {
        "ok": True,
        "status": "started",
        "old_version": pre["head"][:12],
    }


def perform_update(
    repo_root: str | Path,
    runtime: str | Path,
    port: int,
    *,
    python_executable: str | None = None,
) -> int:
    repo = Path(repo_root).expanduser().resolve()
    runtime_path = Path(runtime).expanduser().resolve()
    python = python_executable or sys.executable

    safe, safety_reason = runtime_safe_to_update(runtime_path)
    if not safe:
        write_update_state(runtime_path, "failed", reason=safety_reason)
        return 1

    pre = repository_update_preconditions(repo)
    if not pre.get("ok"):
        write_update_state(runtime_path, "failed", reason=pre["reason"])
        return 1
    old = pre["head"]
    write_update_state(runtime_path, "checking", old_version=old)

    try:
        _run(
            repo,
            [
                "git",
                "-c",
                "http.version=HTTP/1.1",
                "fetch",
                "--prune",
                "origin",
                "main",
            ],
            timeout=120,
        )
        remote = _git(repo, "rev-parse", "origin/main")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", remote):
            raise RuntimeError("invalid remote head")
        if remote == old:
            write_update_state(
                runtime_path,
                "up_to_date",
                old_version=old,
                new_version=remote,
            )
            return 0

        safe, safety_reason = runtime_safe_to_update(runtime_path)
        if not safe:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason=safety_reason,
            )
            return 1

        ancestor = _run(
            repo,
            ["git", "merge-base", "--is-ancestor", old, remote],
            timeout=20,
            check=False,
        )
        if ancestor.returncode != 0:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason="fast_forward_required",
            )
            return 1

        write_update_state(
            runtime_path,
            "updating",
            old_version=old,
            new_version=remote,
        )
        _run(repo, ["git", "merge", "--ff-only", "origin/main"], timeout=60)

        new_head = _git(repo, "rev-parse", "HEAD")
        if new_head != remote:
            raise RuntimeError("head mismatch after update")

        write_update_state(
            runtime_path,
            "restarting",
            old_version=old,
            new_version=new_head,
        )

        stop = subprocess.run(
            [
                python,
                "-m",
                "executor.autonomy.cli",
                "--runtime",
                str(runtime_path),
                "--port",
                str(port),
                "stop",
            ],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if stop.returncode != 0:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=new_head,
                reason="service_stop_failed",
            )
            return 1

        start = subprocess.run(
            [
                python,
                "-m",
                "executor.autonomy.cli",
                "--runtime",
                str(runtime_path),
                "--port",
                str(port),
                "start",
            ],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if start.returncode != 0:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=new_head,
                reason="service_start_failed",
            )
            return 1

        write_update_state(
            runtime_path,
            "success",
            old_version=old,
            new_version=new_head,
        )
        subprocess.Popen(
            [
                python,
                "-m",
                "executor.autonomy.cli",
                "--runtime",
                str(runtime_path),
                "--port",
                str(port),
                "ui",
            ],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return 0
    except subprocess.TimeoutExpired:
        write_update_state(
            runtime_path,
            "failed",
            old_version=old,
            reason="update_timeout",
        )
        return 1
    except Exception:
        write_update_state(
            runtime_path,
            "failed",
            old_version=old,
            reason="update_failed",
        )
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    time.sleep(0.8)
    return perform_update(args.repo, args.runtime, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
