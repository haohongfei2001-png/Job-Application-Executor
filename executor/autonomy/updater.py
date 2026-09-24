from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .queue import TaskQueue


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
        "restart_required",
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
        "reason": safe_reason if status in {"failed", "restart_required"} else "",
    }
    path = _state_path(root)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=".update-state-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


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
        "restart_required",
        "success",
        "failed",
    }:
        status = "failed"
    old_version = data.get("old_version")
    new_version = data.get("new_version")
    reason = data.get("reason")
    return {
        "status": status,
        "old_version": old_version if isinstance(old_version, str) and re.fullmatch(r"[0-9a-fA-F]{1,12}", old_version) else "",
        "new_version": new_version if isinstance(new_version, str) and re.fullmatch(r"[0-9a-fA-F]{1,12}", new_version) else "",
        "reason": reason if isinstance(reason, str) and re.fullmatch(r"[a-z_]{0,80}", reason) else "state_invalid",
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
        if (
            task.get("stage") == "NEEDS_USER_ACTION"
            and task.get("blocker") in {"otp_waiting", "otp_ambiguous"}
        ) or (
            task.get("stage") == "BLOCKED"
            and task.get("blocker") in {
                "user_paused_from_otp_waiting",
                "user_paused_from_otp_ambiguous",
                # Legacy generic human-action pauses may have originated from OTP.
                "user_paused_from_action",
            }
        ):
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


def _update_lock_path(runtime: Path) -> Path:
    return runtime / "update.lock"


def acquire_update_lock(runtime: str | Path) -> int | None:
    root = Path(runtime).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    fd = os.open(_update_lock_path(root), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except Exception:
        os.close(fd)
        raise


def update_lock_held(runtime: str | Path) -> bool:
    root = Path(runtime).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    fd = os.open(_update_lock_path(root), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def reconciled_update_state(runtime: str | Path) -> dict:
    state = read_update_state(runtime)
    if state.get("status") not in {"checking", "updating", "restarting"}:
        return state

    lock_fd = acquire_update_lock(runtime)
    if lock_fd is None:
        return state
    try:
        # Re-read only after the updater lock is held. Another launcher may
        # have replaced the stale state between the first read and lock claim.
        current = read_update_state(runtime)
        status = current.get("status")
        if status not in {"checking", "updating", "restarting"}:
            return current

        # A stale checking state cannot have mutated the checkout yet.
        # Updating/restarting may have crossed the Git boundary, so keep
        # normal task mutations fenced until a restart/update retry proves
        # the loaded service matches the checkout again.
        recovered = "failed" if status == "checking" else "restart_required"
        write_update_state(
            runtime,
            recovered,
            reason="stale_update_recovered",
        )
        return read_update_state(runtime)
    finally:
        os.close(lock_fd)


def spawn_update(
    *,
    repo_root: str | Path,
    runtime: str | Path,
    port: int,
    python_executable: str | None = None,
) -> dict:
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


def perform_update(
    repo_root: str | Path,
    runtime: str | Path,
    port: int,
    *,
    python_executable: str | None = None,
    restart_only: bool = False,
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--lock-fd", type=int)
    parser.add_argument("--restart-only", action="store_true")
    args = parser.parse_args(argv)

    owned_lock = None
    if args.lock_fd is None:
        owned_lock = acquire_update_lock(args.runtime)
        if owned_lock is None:
            return 1
    else:
        try:
            os.fstat(args.lock_fd)
        except OSError:
            return 1

    try:
        time.sleep(0.8)
        return perform_update(
            args.repo,
            args.runtime,
            args.port,
            restart_only=args.restart_only,
        )
    finally:
        if owned_lock is not None:
            os.close(owned_lock)


if __name__ == "__main__":
    raise SystemExit(main())
