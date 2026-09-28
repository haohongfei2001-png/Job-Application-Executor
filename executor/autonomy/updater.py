from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from .queue import TaskQueue
from .release import is_packaged_source


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
    if path.is_symlink():
        return {"status": "failed", "old_version": "", "new_version": "", "reason": "state_invalid"}
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
    reason = data.get("reason", "")
    return {
        "status": status,
        "old_version": old_version if isinstance(old_version, str) and re.fullmatch(r"[0-9a-fA-F]{1,12}", old_version) else "",
        "new_version": new_version if isinstance(new_version, str) and re.fullmatch(r"[0-9a-fA-F]{1,12}", new_version) else "",
        "reason": reason if isinstance(reason, str) and re.fullmatch(r"[a-z_]{0,80}", reason) else "state_invalid",
    }


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
    *, repo_root: str | Path, runtime: str | Path, port: int,
    python_executable: str | None = None,
) -> dict:
    """Read-only compatibility response; never start the checkout writer."""
    reason = ("packaged_update_not_ready" if is_packaged_source(repo_root)
              else "legacy_update_retired")
    return {"ok": False, "status": "denied", "reason": reason}


def perform_update(
    repo_root: str | Path, runtime: str | Path, port: int, *,
    python_executable: str | None = None, restart_only: bool = False,
) -> int:
    """The historical public engine entry is retired before any state I/O."""
    return 1


# The checkout Git update engine is intentionally absent from shipped source.
# Historical behavior remains in tests/historical_updater_fixture.py only.

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--lock-fd", type=int)
    parser.add_argument("--restart-only", action="store_true")
    args = parser.parse_args(argv)

    # Even an inherited lock or --restart-only invocation cannot re-enable the
    # retired CLI. Argument parsing is compatibility-only: no runtime creation,
    # lock claim, checkout operation, service stop/start or applicant read.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
