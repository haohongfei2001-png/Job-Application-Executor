"""Historical checkout updater oracle, never packaged in an app release.

These original routines remain test-only so the existing failure, restart and
provenance regressions retain their exact behavior after runtime retirement.
The release source manifest includes executor/*.py only, not tests/*.py.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from executor.autonomy.release import is_packaged_source
from executor.autonomy.updater import (
    acquire_update_lock, read_update_state,
    runtime_safe_to_update,
    write_update_state,
)

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


def repository_update_preconditions(repo_root: str | Path) -> dict:
    # A packaged app is never a writable Git checkout, even if its release
    # manifest has been removed or somebody placed a .git directory beside it.
    if is_packaged_source(repo_root):
        return {"ok": False, "reason": "packaged_update_not_ready"}
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


def _legacy_spawn_update(
    *,
    repo_root: str | Path,
    runtime: str | Path,
    port: int,
    python_executable: str | None = None,
) -> dict:
    if is_packaged_source(repo_root):
        return {"ok": False, "status": "denied", "reason": "packaged_update_not_ready"}
    repo = Path(repo_root).expanduser().resolve()
    runtime_path = Path(runtime).expanduser().resolve()
    lock_fd = acquire_update_lock(runtime_path)
    if lock_fd is None:
        return {"ok": False, "status": "denied", "reason": "update_in_progress"}
    try:
        pre = repository_update_preconditions(repo)
        if not pre.get("ok"):
            return {"ok": False, "status": "denied", "reason": pre["reason"]}

        previous = read_update_state(runtime_path)
        restart_only = previous.get("status") == "restart_required"
        write_update_state(
            runtime_path,
            "restarting" if restart_only else "checking",
            old_version=pre["head"],
            new_version=pre["head"] if restart_only else "",
        )
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
                    "--lock-fd",
                    str(lock_fd),
                    *(["--restart-only"] if restart_only else []),
                ],
                cwd=repo,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=stream,
                start_new_session=True,
                pass_fds=(lock_fd,),
            )
        return {
            "ok": True,
            "status": "started",
            "old_version": pre["head"][:12],
        }
    except Exception:
        write_update_state(
            runtime_path,
            "failed",
            reason="update_launch_failed",
        )
        return {"ok": False, "status": "denied", "reason": "update_launch_failed"}
    finally:
        os.close(lock_fd)


def _restart_service(
    repo: Path,
    runtime_path: Path,
    port: int,
    python: str,
    *,
    old_version: str,
    new_version: str,
) -> int:
    write_update_state(
        runtime_path,
        "restarting",
        old_version=old_version,
        new_version=new_version,
    )

    try:
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
    except subprocess.TimeoutExpired:
        write_update_state(
            runtime_path,
            "restart_required",
            old_version=old_version,
            new_version=new_version,
            reason="service_restart_timeout",
        )
        return 1
    except Exception:
        write_update_state(
            runtime_path,
            "restart_required",
            old_version=old_version,
            new_version=new_version,
            reason="service_restart_failed",
        )
        return 1

    if stop.returncode != 0:
        write_update_state(
            runtime_path,
            "restart_required",
            old_version=old_version,
            new_version=new_version,
            reason="service_stop_failed",
        )
        return 1

    try:
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
    except subprocess.TimeoutExpired:
        write_update_state(
            runtime_path,
            "restart_required",
            old_version=old_version,
            new_version=new_version,
            reason="service_restart_timeout",
        )
        return 1
    except Exception:
        write_update_state(
            runtime_path,
            "restart_required",
            old_version=old_version,
            new_version=new_version,
            reason="service_restart_failed",
        )
        return 1

    if start.returncode != 0:
        write_update_state(
            runtime_path,
            "restart_required",
            old_version=old_version,
            new_version=new_version,
            reason="service_start_failed",
        )
        return 1

    write_update_state(
        runtime_path,
        "success",
        old_version=old_version,
        new_version=new_version,
    )
    try:
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
    except Exception:
        pass
    return 0


def _legacy_perform_update(
    repo_root: str | Path,
    runtime: str | Path,
    port: int,
    *,
    python_executable: str | None = None,
    restart_only: bool = False,
) -> int:
    if is_packaged_source(repo_root):
        return 1
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
    write_update_state(
        runtime_path,
        "restarting" if restart_only else "checking",
        old_version=old,
        new_version=old if restart_only else "",
    )
    git_mutation_started = False

    try:
        if restart_only:
            return _restart_service(
                repo,
                runtime_path,
                port,
                python,
                old_version=old,
                new_version=old,
            )

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

        refreshed = repository_update_preconditions(repo)
        if not refreshed.get("ok"):
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason=refreshed["reason"],
            )
            return 1
        if refreshed.get("head") != old:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason="repository_changed_during_update",
            )
            return 1

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

        final_repo = repository_update_preconditions(repo)
        if not final_repo.get("ok"):
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason=final_repo["reason"],
            )
            return 1
        if final_repo.get("head") != old:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason="repository_changed_during_update",
            )
            return 1
        if _git(repo, "rev-parse", "origin/main") != remote:
            write_update_state(
                runtime_path,
                "failed",
                old_version=old,
                new_version=remote,
                reason="remote_changed_during_update",
            )
            return 1

        write_update_state(
            runtime_path,
            "updating",
            old_version=old,
            new_version=remote,
        )
        git_mutation_started = True
        _run(repo, ["git", "merge", "--ff-only", "origin/main"], timeout=60)

        new_head = _git(repo, "rev-parse", "HEAD")
        if new_head != remote:
            raise RuntimeError("head mismatch after update")

        return _restart_service(
            repo,
            runtime_path,
            port,
            python,
            old_version=old,
            new_version=new_head,
        )
    except subprocess.TimeoutExpired:
        post_mutation = git_mutation_started or restart_only
        write_update_state(
            runtime_path,
            "restart_required" if post_mutation else "failed",
            old_version=old,
            reason="update_timeout",
        )
        return 1
    except Exception:
        post_mutation = git_mutation_started or restart_only
        write_update_state(
            runtime_path,
            "restart_required" if post_mutation else "failed",
            old_version=old,
            reason="update_failed",
        )
        return 1
