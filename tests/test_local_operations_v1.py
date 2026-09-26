from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
from pathlib import Path
import threading
import time
from datetime import datetime, timezone
import urllib.request

import pytest

from executor.autonomy import diagnostics, updater

# The canonical consumer contract retires public Git mutation entrypoints.
# Historical engine cases below still exercise every existing safety failure,
# timeout, restart, provenance and concurrency assertion through private retained
# engine functions; no production consumer/CLI may dispatch those functions.
from executor.autonomy.manager import ManagerController, ManagerTurn
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker
from executor.auth.attempts import AuthAttemptStore


class _UnavailableProvider:
    available = False

    def decide(self, *_):
        raise RuntimeError("offline")


def _spec(tmp_path, *, url="https://jobs.example.test/apply?postId=diag-1"):
    profile = tmp_path / "private-profile.json"
    profile.write_text('{"fields":{"identity.phone":{"value":"13800138000"}}}')
    return TaskSpec(
        company="Synthetic Co",
        role="AI Product Manager",
        target_url=url,
        job_id="diag-1",
        profile_ref=str(profile),
        live_authorized=True,
    )


def _supervisor(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(
        q,
        settings={
            "deepseek": {"enabled": False},
            "profile_path": str(tmp_path / "very-private-profile.json"),
        },
    )
    manager = ManagerController(
        q,
        worker,
        provider=_UnavailableProvider(),
        settings={"profile_path": str(tmp_path / "very-private-profile.json")},
    )
    return q, worker, Supervisor(
        q,
        worker=worker,
        token="x" * 40,
        manager=manager,
    )


def test_diagnostics_are_copy_safe_and_never_emit_buffered_otp(
    tmp_path, monkeypatch
):
    q, worker, supervisor = _supervisor(tmp_path)
    task = q.enqueue(_spec(tmp_path))
    q.enqueue(TaskSpec(
        company="CANARY_NOVEL_FAMILY_VALUE",
        role="Applicant Alice Nouvel",
        target_url="https://private-canary.example.test/roles/other",
        profile_ref=str(tmp_path / "private-profile.json"),
    ))
    claimed = q.claim("diagnostic-worker")
    attempts = AuthAttemptStore(q)
    attempt = attempts.begin(task["task_id"], claimed["owner"], "jobs.example.test",
                             task["spec"]["target_url"])
    attempts.record_send_intent(attempt["attempt_id"], task["task_id"], claimed["owner"])
    attempts.record_send_result(attempt["attempt_id"], outcome="CLICK_OBSERVED")
    q.checkpoint(
        task["task_id"],
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="otp_waiting",
        release=True,
    )
    code = "48291357"
    pushed = worker.broker.push(message="验证码 " + code, task_id=task["task_id"],
                                attempt_id=attempt["attempt_id"])
    assert pushed["accepted"] is True

    monkeypatch.setattr(diagnostics.browser, "browser_mode", lambda: "live")
    monkeypatch.setattr(diagnostics.browser, "_alive", lambda: True)

    class FakeMapper:
        def __init__(self, *_):
            self.available = True

    supervisor.manager = ManagerController(
        q, worker, provider=FakeMapper(), settings=supervisor.manager.settings)
    supervisor.release_identity = {"status": "verified", "source_sha256": "a" * 64}
    report = diagnostics.collect_diagnostics(
        supervisor,
        repo_root=tmp_path,
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["format"] == "application-executor-diagnostics-v1"
    assert report["loaded_source"] == {
        "verified_at_start": True, "sha256": "a" * 64
    }
    assert datetime.fromisoformat(report["captured_at_utc"]).tzinfo == timezone.utc
    assert report["recovery"] == {"reason": "none", "action": "none"}
    assert report["system"]["cdp_alive"] is True
    assert report["system"]["deepseek_available"] is True
    assert report["system"]["otp_waiting_tasks"] == 1
    assert report["system"]["otp_buffered_tasks"] == 1
    assert report["safety"]["submit_capability"] is False
    assert code not in serialized
    assert "13800138000" not in serialized
    assert "very-private-profile" not in serialized
    assert "private-profile.json" not in serialized
    assert "postId=diag-1" not in serialized
    for private in ("CANARY_NOVEL_FAMILY_VALUE", "Alice Nouvel", "private-canary.example.test"):
        assert private not in serialized
    assert report["tasks"][0]["task"] == task["task_id"][:8]
    assert "target_host" not in report["tasks"][0]
    monkeypatch.setattr(diagnostics.browser, "browser_mode", lambda: "CANARY_PRIVATE_MODE_VALUE")
    bounded = diagnostics.collect_diagnostics(supervisor, repo_root=tmp_path)
    assert bounded["system"]["browser_mode"] == "unknown"
    assert "CANARY_PRIVATE_MODE_VALUE" not in json.dumps(bounded)


def test_diagnostic_repository_state_redacts_local_branch_names(tmp_path, monkeypatch):
    def fake_git(_repo, *args, **_kwargs):
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("branch", "--show-current"):
            return "CANARY_PRIVATE_APPLICANT"
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(diagnostics, "_git", fake_git)
    state = diagnostics.repository_state(tmp_path)
    assert state == {"version": "a" * 12, "branch": "other", "worktree_clean": True}


def _init_git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    tracked = path / "tracked.txt"
    tracked.write_text("v1")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "origin",
            "https://github.com/haohongfei2001-png/Job-Application-Executor.git",
        ],
        cwd=path,
        check=True,
    )
    return tracked


def test_packaged_app_never_invokes_legacy_git_updater(tmp_path, monkeypatch):
    _queue, _worker, supervisor = _supervisor(tmp_path)
    supervisor.release_identity = {"status": "verified", "source_sha256": "a" * 64}

    def forbidden_writer(**_kwargs):
        raise AssertionError("packaged app must not launch the Git writer")

    monkeypatch.setattr("executor.autonomy.supervisor.spawn_update", forbidden_writer)
    result = supervisor.begin_update(9344)
    assert result == {
        "ok": False,
        "status": "denied",
        "reason": "packaged_update_not_ready",
    }


def test_unverified_packaged_app_never_invokes_legacy_git_updater(tmp_path, monkeypatch):
    _queue, _worker, supervisor = _supervisor(tmp_path)
    supervisor.release_identity = {"status": "unverified", "source_sha256": ""}
    monkeypatch.setattr("executor.autonomy.supervisor.is_packaged_source", lambda _: True)
    monkeypatch.setattr(
        "executor.autonomy.supervisor.spawn_update",
        lambda **_: pytest.fail("unverified packaged app must not launch Git updater"),
    )
    assert supervisor.begin_update(9344) == {
        "ok": False, "status": "denied", "reason": "packaged_update_not_ready",
    }


def test_direct_legacy_updater_refuses_packaged_source_before_git_or_runtime_write(
    tmp_path, monkeypatch
):
    release = tmp_path / "AI Manager.app" / "Contents" / "Resources" / "release"
    release.mkdir(parents=True)
    runtime = tmp_path / "runtime"

    def forbidden_git(*_args, **_kwargs):
        pytest.fail("packaged release must never reach Git")

    monkeypatch.setattr(updater, "_git", forbidden_git)
    monkeypatch.setattr(updater, "_run", forbidden_git)
    monkeypatch.setattr(updater.subprocess, "Popen", forbidden_git)
    assert updater.repository_update_preconditions(release) == {
        "ok": False, "reason": "packaged_update_not_ready"
    }
    assert updater.spawn_update(repo_root=release, runtime=runtime, port=9344) == {
        "ok": False, "status": "denied", "reason": "packaged_update_not_ready"
    }
    assert updater.perform_update(release, runtime, 9344) == 1
    assert not runtime.exists()

    # The bundle-layout fence survives a missing source manifest. A fake Git
    # checkout cannot turn an installed app back into an in-place update target.
    (release / ".git").mkdir()
    assert updater.repository_update_preconditions(release)["reason"] == "packaged_update_not_ready"
    assert updater.perform_update(release, runtime, 9344, restart_only=True) == 1
    assert not runtime.exists()


def test_updater_requires_clean_main_and_expected_origin(tmp_path):
    repo = tmp_path / "repo"
    tracked = _init_git_repo(repo)

    ready = updater.repository_update_preconditions(repo)
    assert ready["ok"] is True

    tracked.write_text("changed")
    dirty = updater.repository_update_preconditions(repo)
    assert dirty == {"ok": False, "reason": "tracked_changes_present"}

    subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    wrong_branch = updater.repository_update_preconditions(repo)
    assert wrong_branch == {"ok": False, "reason": "not_on_main"}


def test_safe_update_refuses_active_or_runnable_task(tmp_path):
    q, worker, supervisor = _supervisor(tmp_path)

    worker.active = "active-task"
    assert updater.safe_to_update(supervisor) == (False, "worker_active")
    worker.active = None

    task = q.enqueue(_spec(tmp_path))
    assert updater.safe_to_update(supervisor) == (False, "runnable_task_pending")

    claimed = q.claim("update-worker")
    q.checkpoint(
        task["task_id"],
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="otp_waiting",
        release=True,
    )
    assert updater.safe_to_update(supervisor) == (False, "otp_in_flight")

    with q.tx() as db:
        db.execute(
            "UPDATE tasks SET blocker='security_challenge' WHERE task_id=?",
            (task["task_id"],),
        )
    assert updater.safe_to_update(supervisor) == (True, "")

    with q.tx() as db:
        db.execute(
            "UPDATE tasks SET stage='BLOCKED',blocker='user_paused_from_otp_waiting' WHERE task_id=?",
            (task["task_id"],),
        )
    assert updater.safe_to_update(supervisor) == (False, "otp_in_flight")


def test_update_state_is_written_with_atomic_replace(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    calls = []
    original = updater.os.replace

    def wrapped(src, dst):
        calls.append((str(src), str(dst)))
        return original(src, dst)

    monkeypatch.setattr(updater.os, "replace", wrapped)
    updater.write_update_state(
        runtime,
        "checking",
        old_version="a" * 40,
    )

    assert len(calls) == 1
    assert calls[0][1].endswith("update-state.json")
    assert updater.read_update_state(runtime)["status"] == "checking"
    assert not list(runtime.glob(".update-state-*.tmp"))


def test_update_lock_serializes_concurrent_launches(tmp_path):
    runtime = tmp_path / "runtime"
    first = updater.acquire_update_lock(runtime)
    assert first is not None
    try:
        second = updater.acquire_update_lock(runtime)
        assert second is None
        denied = updater._legacy_spawn_update(
            repo_root=tmp_path / "repo",
            runtime=runtime,
            port=9344,
        )
        assert denied == {
            "ok": False,
            "status": "denied",
            "reason": "update_in_progress",
        }
    finally:
        os.close(first)

    third = updater.acquire_update_lock(runtime)
    assert third is not None
    os.close(third)


def test_recent_events_returns_actual_tail_beyond_first_200(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    task = q.enqueue(_spec(tmp_path))
    with q.tx() as db:
        for _ in range(220):
            q._event(db, task["task_id"], "diagnostic_tick", "DISCOVERED")

    recent = q.recent_events(12)

    assert len(recent) == 12
    assert [row["seq"] for row in recent] == sorted(row["seq"] for row in recent)
    assert recent[0]["seq"] > 200
    assert recent[-1]["kind"] == "diagnostic_tick"


def test_reconciled_update_state_recovers_stale_busy_file(tmp_path):
    runtime = tmp_path / "runtime"
    updater.write_update_state(
        runtime,
        "checking",
        old_version="a" * 40,
    )

    assert updater.update_lock_held(runtime) is False
    state = updater.reconciled_update_state(runtime)

    assert state["status"] == "failed"
    assert state["reason"] == "stale_update_recovered"


def test_reconcile_holds_update_lock_through_stale_state_write(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    updater.write_update_state(
        runtime,
        "checking",
        old_version="a" * 40,
    )
    original_write = updater.write_update_state
    entered = threading.Event()
    release = threading.Event()
    result = {}

    def blocking_write(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(updater, "write_update_state", blocking_write)

    def reconcile():
        result.update(updater.reconciled_update_state(runtime))

    thread = threading.Thread(target=reconcile)
    thread.start()
    assert entered.wait(1)

    competing = updater.acquire_update_lock(runtime)
    assert competing is None

    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert result["status"] == "failed"
    assert result["reason"] == "stale_update_recovered"


@pytest.mark.parametrize("status", ["updating", "restarting"])
def test_reconciled_post_mutation_stale_state_requires_restart(tmp_path, status):
    runtime = tmp_path / "runtime"
    updater.write_update_state(
        runtime,
        status,
        old_version="a" * 40,
        new_version="b" * 40,
    )

    assert updater.update_lock_held(runtime) is False
    state = updater.reconciled_update_state(runtime)

    assert state["status"] == "restart_required"
    assert state["reason"] == "stale_update_recovered"


def test_begin_update_waits_for_inflight_mutation_to_drain(tmp_path, monkeypatch):
    _, _, supervisor = _supervisor(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    spawned = []

    def blocking_handle(_message):
        entered.set()
        assert release.wait(2)
        return {"reply": "done", "actions": []}

    monkeypatch.setattr(supervisor.manager, "handle", blocking_handle)
    monkeypatch.setattr(
        "executor.autonomy.supervisor.safe_to_update",
        lambda sup: (True, ""),
    )
    monkeypatch.setattr(
        "executor.autonomy.supervisor.spawn_update",
        lambda **kwargs: spawned.append(kwargs) or {
            "ok": True,
            "status": "started",
            "old_version": "abc123",
        },
    )

    def mutate():
        supervisor.run_mutation(lambda: supervisor.manager.handle("slow"))
        finished.set()

    mutation_thread = threading.Thread(target=mutate)
    mutation_thread.start()
    assert entered.wait(1)

    update_result = {}

    def update():
        update_result.update(supervisor.begin_update(9344))

    update_thread = threading.Thread(target=update)
    update_thread.start()
    time.sleep(0.05)

    assert spawned == []
    assert update_thread.is_alive()

    release.set()
    mutation_thread.join(2)
    update_thread.join(2)

    assert finished.is_set()
    assert update_result == {
        "ok": False, "status": "denied", "reason": "legacy_update_retired",
    }
    assert spawned == [], "drained mutation cannot admit the retired Git writer"


def test_repo_is_revalidated_after_fetch_and_immediately_before_merge(
    tmp_path, monkeypatch
):
    old = "a" * 40
    new = "b" * 40
    preconditions = [
        {"ok": True, "head": old},
        {"ok": True, "head": old},
        {"ok": False, "reason": "tracked_changes_present"},
    ]
    git_calls = []

    monkeypatch.setattr(
        updater,
        "runtime_safe_to_update",
        lambda runtime: (True, ""),
    )
    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda repo: preconditions.pop(0),
    )

    def fake_run(repo, args, **kwargs):
        git_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(updater, "_run", fake_run)
    monkeypatch.setattr(
        updater,
        "_git",
        lambda repo, *args, **kwargs: new
        if args == ("rev-parse", "origin/main")
        else (_ for _ in ()).throw(AssertionError(args)),
    )

    rc = updater._legacy_perform_update(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
    )

    assert rc == 1
    assert ["git", "merge", "--ff-only", "origin/main"] not in git_calls
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "failed"
    assert state["reason"] == "tracked_changes_present"


def test_spawn_update_preserves_restart_retry_intent(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    repo = tmp_path / "repo"
    old = "a" * 40
    captured = []

    updater.write_update_state(
        runtime,
        "restart_required",
        old_version=old,
        new_version=old,
        reason="service_start_failed",
    )
    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda path: {"ok": True, "head": old},
    )

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured.append((args, kwargs))

    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)

    result = updater._legacy_spawn_update(
        repo_root=repo,
        runtime=runtime,
        port=9344,
        python_executable="/private/python",
    )

    assert result["status"] == "started"
    args, kwargs = captured[0]
    assert "--restart-only" in args
    assert "--lock-fd" in args
    assert kwargs["pass_fds"]
    state = updater.read_update_state(runtime)
    assert state["status"] == "restarting"


def test_restart_required_can_be_retried_when_code_is_already_current(
    tmp_path, monkeypatch
):
    old = "a" * 40
    runtime = tmp_path / "runtime"
    updater.write_update_state(
        runtime,
        "restart_required",
        old_version=old,
        new_version=old,
        reason="service_stop_failed",
    )
    assert updater.read_update_state(runtime)["reason"] == "service_stop_failed"

    monkeypatch.setattr(
        updater,
        "runtime_safe_to_update",
        lambda runtime: (True, ""),
    )
    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda repo: {"ok": True, "head": old},
    )
    monkeypatch.setattr(
        updater,
        "_run",
        lambda repo, args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(
        updater,
        "_git",
        lambda repo, *args, **kwargs: old
        if args == ("rev-parse", "origin/main")
        else (_ for _ in ()).throw(AssertionError(args)),
    )

    service_calls = []

    def fake_service(args, **kwargs):
        service_calls.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    class FakePopen:
        def __init__(self, args, **kwargs):
            service_calls.append(args[-1])

    monkeypatch.setattr(updater.subprocess, "run", fake_service)
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)

    rc = updater._legacy_perform_update(
        tmp_path / "repo",
        runtime,
        9344,
        python_executable="/private/python",
        restart_only=True,
    )

    assert rc == 0
    assert service_calls == ["stop", "start", "ui"]
    state = updater.read_update_state(runtime)
    assert state["status"] == "success"


@pytest.mark.parametrize("timeout_on", ["stop", "start"])
def test_restart_only_bypasses_git_and_timeout_keeps_restart_fence(
    tmp_path, monkeypatch, timeout_on
):
    old = "a" * 40
    monkeypatch.setattr(
        updater,
        "runtime_safe_to_update",
        lambda runtime: (True, ""),
    )
    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda repo: {"ok": True, "head": old},
    )

    def forbidden_git(*args, **kwargs):
        raise AssertionError("restart-only retry must not fetch or merge")

    service_calls = []

    def fake_service(args, **kwargs):
        action = args[-1]
        service_calls.append(action)
        if action == timeout_on:
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout", 20))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(updater, "_run", forbidden_git)
    monkeypatch.setattr(updater.subprocess, "run", fake_service)

    rc = updater._legacy_perform_update(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
        restart_only=True,
    )

    assert rc == 1
    assert service_calls == (
        ["stop"] if timeout_on == "stop" else ["stop", "start"]
    )
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "restart_required"
    assert state["reason"] == "service_restart_timeout"


def test_failure_after_git_mutation_stays_restart_required(
    tmp_path, monkeypatch
):
    old = "a" * 40
    new = "b" * 40
    preconditions = [
        {"ok": True, "head": old},
        {"ok": True, "head": old},
        {"ok": True, "head": old},
    ]

    monkeypatch.setattr(
        updater,
        "runtime_safe_to_update",
        lambda runtime: (True, ""),
    )
    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda repo: preconditions.pop(0),
    )

    def fake_git(repo, *args, **kwargs):
        if args == ("rev-parse", "origin/main"):
            return new
        raise AssertionError(args)

    def fake_run(repo, args, **kwargs):
        if args[:3] == ["git", "-c", "http.version=HTTP/1.1"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["git", "merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["git", "merge", "--ff-only", "origin/main"]:
            raise subprocess.TimeoutExpired(args, 60)
        raise AssertionError(args)

    monkeypatch.setattr(updater, "_git", fake_git)
    monkeypatch.setattr(updater, "_run", fake_run)

    rc = updater._legacy_perform_update(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
    )

    assert rc == 1
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "restart_required"
    assert state["reason"] == "update_timeout"


def test_restart_failure_persists_restart_required_state(tmp_path, monkeypatch):
    old = "a" * 40
    calls = []

    def fake_service(args, **kwargs):
        calls.append(args[-1])
        return subprocess.CompletedProcess(args, 1 if args[-1] == "stop" else 0, "", "")

    monkeypatch.setattr(updater.subprocess, "run", fake_service)

    rc = updater._restart_service(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
        "/private/python",
        old_version=old,
        new_version=old,
    )

    assert rc == 1
    assert calls == ["stop"]
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "restart_required"
    assert state["reason"] == "service_stop_failed"


def test_update_check_uses_http11_and_leaves_code_unchanged_when_current(
    tmp_path, monkeypatch
):
    old = "a" * 40
    calls = []

    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda repo: {"ok": True, "head": old},
    )

    def fake_run(repo, args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_git(repo, *args, **kwargs):
        assert args == ("rev-parse", "origin/main")
        return old

    monkeypatch.setattr(updater, "_run", fake_run)
    monkeypatch.setattr(updater, "_git", fake_git)

    rc = updater._legacy_perform_update(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
    )

    assert rc == 0
    assert calls == [[
        "git",
        "-c",
        "http.version=HTTP/1.1",
        "fetch",
        "--prune",
        "origin",
        "main",
    ]]
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "up_to_date"
    assert state["old_version"] == old[:12]
    assert state["new_version"] == old[:12]


def test_fast_forward_update_restarts_service_and_records_success(
    tmp_path, monkeypatch
):
    old = "a" * 40
    new = "b" * 40
    git_calls = []
    service_calls = []
    ui_calls = []

    monkeypatch.setattr(
        updater,
        "repository_update_preconditions",
        lambda repo: {"ok": True, "head": old},
    )

    def fake_run(repo, args, **kwargs):
        git_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def fake_git(repo, *args, **kwargs):
        if args == ("rev-parse", "origin/main"):
            return new
        if args == ("rev-parse", "HEAD"):
            return new
        raise AssertionError(args)

    def fake_subprocess_run(args, **kwargs):
        service_calls.append(args[-1])
        return subprocess.CompletedProcess(args, 0, "", "")

    class FakePopen:
        def __init__(self, args, **kwargs):
            ui_calls.append(args[-1])

    monkeypatch.setattr(updater, "_run", fake_run)
    monkeypatch.setattr(updater, "_git", fake_git)
    monkeypatch.setattr(updater.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)

    rc = updater._legacy_perform_update(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
        python_executable="/private/python",
    )

    assert rc == 0
    assert ["git", "merge-base", "--is-ancestor", old, new] in git_calls
    assert ["git", "merge", "--ff-only", "origin/main"] in git_calls
    assert service_calls == ["stop", "start"]
    assert ui_calls == ["ui"]
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "success"
    assert state["old_version"] == old[:12]
    assert state["new_version"] == new[:12]


def test_ui_diagnostics_and_update_routes_require_valid_ui_session(
    tmp_path, monkeypatch
):
    q, worker, supervisor = _supervisor(tmp_path)
    monkeypatch.setattr(
        supervisor,
        "readiness",
        lambda: {
            "ok": False,
            "ready_for_live_e2e": False,
            "message": "个人资料尚未配置。",
            "remediation": ["configure_profile_path"],
            "submit_capability": False,
        },
    )
    monkeypatch.setattr(
        supervisor, "observe_task",
        lambda task_id: {"ok": True, "task_id": task_id, "status": "NO_TASK_BINDING", "replay_allowed": False},
    )
    monkeypatch.setattr(
        supervisor,
        "diagnostics",
        lambda: {
            "format": "application-executor-diagnostics-v1",
            "safety": {"submit_capability": False},
        },
    )
    monkeypatch.setattr(
        supervisor,
        "begin_update",
        lambda port: {"ok": True, "status": "started", "old_version": "abc123"},
    )
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        ticket_request = urllib.request.Request(
            base + "/v1/ui-ticket",
            data=b"{}",
            headers={
                "Authorization": "Bearer " + "x" * 40,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(ticket_request) as response:
            ticket = json.load(response)["ticket"]

        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar)
        )
        with opener.open(base + "/ui-login?ticket=" + ticket):
            pass

        with opener.open(base + "/ui/api/diagnostics") as response:
            report = json.load(response)
        assert report["format"] == "application-executor-diagnostics-v1"
        assert report["safety"]["submit_capability"] is False

        with opener.open(base + "/ui/api/readiness") as response:
            readiness = json.load(response)
        assert readiness["ready_for_live_e2e"] is False
        assert readiness["submit_capability"] is False
        assert readiness["remediation"] == ["configure_profile_path"]
        with opener.open(base + "/ui/api/observe?task_id=synthetic-task") as response:
            observation = json.load(response)
        assert observation["status"] == "NO_TASK_BINDING"
        assert observation["replay_allowed"] is False

        update_request = urllib.request.Request(
            base + "/ui/api/update",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Origin": base,
            },
            method="POST",
        )
        with opener.open(update_request) as response:
            result = json.load(response)
        assert result["status"] == "started"

        unauthenticated = urllib.request.Request(base + "/ui/api/diagnostics")
        with pytest.raises(Exception):
            urllib.request.urlopen(unauthenticated)
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base + "/ui/api/readiness")
        assert denied.value.code == 401
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base + "/ui/api/observe?task_id=synthetic-task")
        assert denied.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join()



def test_update_state_readback_never_exposes_corrupt_private_text(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    private = "CANARY_PRIVATE_APPLICANT_VALUE"
    (runtime / "update-state.json").write_text(json.dumps({
        "status": "failed",
        "old_version": private,
        "new_version": private,
        "reason": private,
    }), encoding="utf-8")

    state = updater.read_update_state(runtime)
    assert state == {
        "status": "failed",
        "old_version": "",
        "new_version": "",
        "reason": "state_invalid",
    }
    assert private not in json.dumps(state)



def test_update_state_readback_refuses_symlink_to_private_file(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    private = tmp_path / "private-applicant.txt"
    private.write_text("CANARY_PRIVATE_APPLICANT_VALUE", encoding="utf-8")
    (runtime / "update-state.json").symlink_to(private)

    state = updater.read_update_state(runtime)
    assert state == {
        "status": "failed",
        "old_version": "",
        "new_version": "",
        "reason": "state_invalid",
    }
    assert "CANARY_PRIVATE_APPLICANT_VALUE" not in json.dumps(state)


def test_diagnostics_do_not_claim_clean_checkout_when_git_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_git", lambda *args, **kwargs: None)
    state = diagnostics.repository_state(tmp_path)
    assert state == {"version": "unknown", "branch": "unknown", "worktree_clean": None}


def test_supervisor_readonly_observation_preserves_unknown_task(tmp_path, monkeypatch):
    queue, worker, supervisor = _supervisor(tmp_path)
    created = queue.enqueue(_spec(tmp_path))
    claimed = queue.claim("synthetic-observer")
    queue.checkpoint(created["task_id"], claimed["owner"], "BLOCKED",
                     blocker="unknown_outcome", release=True)
    before = queue.get(created["task_id"])
    observed = []
    monkeypatch.setattr(
        "executor.autonomy.supervisor.browser.observe_bound_draft",
        lambda target, binding: observed.append((target, binding)) or {
            "status": "NO_TASK_BINDING", "draft_identity_verified": False,
            "write_outcome_verified": False, "replay_allowed": False,
        },
    )
    result = supervisor.dispatch("GET", "/v1/tasks/" + created["task_id"] + "/observe", {})
    assert result["status"] == "NO_TASK_BINDING"
    assert result["replay_allowed"] is False
    assert observed == [(before["spec"]["target_url"], None)]
    assert queue.get(created["task_id"])["revision"] == before["revision"]
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.resume(created["task_id"])


def test_supervisor_fences_mutations_while_update_is_running(
    tmp_path, monkeypatch
):
    q, worker, supervisor = _supervisor(tmp_path)
    task = q.enqueue(_spec(tmp_path))
    claimed = q.claim("fence-worker")
    q.checkpoint(
        task["task_id"],
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="security_challenge",
        release=True,
    )
    monkeypatch.setattr(
        "executor.autonomy.supervisor.reconciled_update_state",
        lambda root: {
            "status": "updating",
            "old_version": "a" * 12,
            "new_version": "b" * 12,
            "reason": "",
        },
    )

    with pytest.raises(RuntimeError):
        supervisor.dispatch(
            "POST",
            "/v1/tasks/" + task["task_id"] + "/resume",
            {},
        )

    monkeypatch.setattr(
        "executor.autonomy.supervisor.reconciled_update_state",
        lambda root: {
            "status": "restart_required",
            "old_version": "a" * 12,
            "new_version": "b" * 12,
            "reason": "service_start_failed",
        },
    )
    with pytest.raises(RuntimeError):
        supervisor.dispatch(
            "POST",
            "/v1/tasks/" + task["task_id"] + "/resume",
            {},
        )

    # Read-only diagnostics remain available during an update.
    monkeypatch.setattr(
        supervisor,
        "diagnostics",
        lambda: {"format": "application-executor-diagnostics-v1"},
    )
    assert supervisor.dispatch("GET", "/v1/diagnostics", {})["format"] == (
        "application-executor-diagnostics-v1"
    )


@pytest.mark.parametrize("task_gate", ["empty", "worker", "runnable", "otp", "security"])
def test_consumer_retired_update_admission_never_spawns_checkout_writer(
    tmp_path, monkeypatch, task_gate
):
    q, worker, supervisor = _supervisor(tmp_path)
    supervisor.release_identity = {"status": "unverified", "source_sha256": ""}
    monkeypatch.setattr("executor.autonomy.supervisor.is_packaged_source", lambda _: False)
    monkeypatch.setattr(
        "executor.autonomy.supervisor.spawn_update",
        lambda **_: pytest.fail("consumer must never admit the retired Git writer"),
    )
    monkeypatch.setattr(updater, "_run", lambda *a, **k: pytest.fail("no Git process"))
    if task_gate == "worker":
        worker.active = "synthetic-active"
    elif task_gate != "empty":
        task = q.enqueue(_spec(tmp_path))
        if task_gate in {"otp", "security"}:
            claimed = q.claim("synthetic-owner")
            q.checkpoint(task["task_id"], claimed["owner"], "NEEDS_USER_ACTION",
                         blocker="otp_waiting" if task_gate == "otp" else "security_challenge",
                         release=True)
    tasks, events = q.tasks(), q.recent_events(1000)
    expected = {"worker": "worker_active", "runnable": "runnable_task_pending",
                "otp": "otp_in_flight"}.get(task_gate, "legacy_update_retired")
    assert supervisor.begin_update(9344) == {
        "ok": False, "status": "denied", "reason": expected,
    }
    assert q.tasks() == tasks
    assert q.recent_events(1000) == events
    assert not (q.root / "updater.log").exists()
    assert not (q.root / "update-state.json").exists()


def test_authenticated_consumer_update_route_is_readonly_after_legacy_retirement(
    tmp_path, monkeypatch
):
    q, _, supervisor = _supervisor(tmp_path)
    supervisor.release_identity = {"status": "unverified", "source_sha256": ""}
    monkeypatch.setattr("executor.autonomy.supervisor.is_packaged_source", lambda _: False)
    monkeypatch.setattr(
        "executor.autonomy.supervisor.spawn_update",
        lambda **_: pytest.fail("actual authenticated UI cannot launch Git writer"),
    )
    tasks, events = q.tasks(), q.recent_events(1000)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    try:
        ticket_request = urllib.request.Request(
            base + "/v1/ui-ticket", data=b"{}",
            headers={"Authorization": "Bearer " + "x" * 40,
                     "Content-Type": "application/json"}, method="POST")
        with opener.open(ticket_request, timeout=5) as response:
            ticket = json.load(response)["ticket"]
        with opener.open(base + "/ui-login?ticket=" + ticket, timeout=5):
            pass
        for _ in range(2):
            request = urllib.request.Request(
                base + "/ui/api/update", data=b"{}",
                headers={"Content-Type": "application/json", "Origin": base},
                method="POST")
            with pytest.raises(urllib.error.HTTPError) as refused:
                opener.open(request, timeout=5)
            assert refused.value.code == 409
            assert json.load(refused.value) == {
                "ok": False, "status": "denied", "reason": "legacy_update_retired",
            }
        with opener.open(base + "/ui/api/update-status", timeout=5) as response:
            assert json.load(response)["status"] == "idle"
        assert q.tasks() == tasks
        assert q.recent_events(1000) == events
        assert not (q.root / "updater.log").exists()
        assert not (q.root / "update-state.json").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("packaged", [False, True])
@pytest.mark.parametrize("existing_runtime", [False, True])
def test_retired_public_updater_entries_are_readonly(
    tmp_path, monkeypatch, packaged, existing_runtime
):
    repo, runtime = tmp_path / "code", tmp_path / "state"
    repo.mkdir()
    code = repo / "source-canary.py"
    code.write_bytes(b"CODE_AUTHORITY_UNCHANGED\n")
    if existing_runtime:
        runtime.mkdir(mode=0o700)
        for name in ["worker.lock", "migration.lock", "update.lock", "update-state.json",
                     "tasks.sqlite3", "tasks.sqlite3-wal", "task-state.key"]:
            (runtime / name).write_bytes(b"PRIVATE_STATE_CANARY_DO_NOT_READ_OR_WRITE")
            (runtime / name).chmod(0o600)
    def inventory(root):
        return sorted((str(p.relative_to(root)), p.read_bytes(), p.stat().st_mode)
                      for p in root.rglob("*") if p.is_file()) if root.exists() else None
    before_code, before_state = inventory(repo), inventory(runtime)
    monkeypatch.setattr(updater, "is_packaged_source", lambda _: packaged)
    def forbidden(*args, **kwargs):
        pytest.fail("retired entrypoint must not call the legacy writer or read state")
    for name in ["_legacy_spawn_update", "_legacy_perform_update",
                 "acquire_update_lock", "repository_update_preconditions",
                 "runtime_safe_to_update", "read_update_state", "write_update_state",
                 "_git", "_run", "_stop_service", "_start_service"]:
        if hasattr(updater, name):
            monkeypatch.setattr(updater, name, forbidden)
    monkeypatch.setattr(updater.subprocess, "Popen", forbidden)
    for _ in range(2):
        assert updater.spawn_update(repo_root=repo, runtime=runtime, port=9344) == {
            "ok": False, "status": "denied",
            "reason": "packaged_update_not_ready" if packaged else "legacy_update_retired",
        }
        for restart_only in [False, True]:
            assert updater.perform_update(repo, runtime, 9344,
                                          restart_only=restart_only) == 1
    assert inventory(repo) == before_code
    assert inventory(runtime) == before_state


@pytest.mark.parametrize("restart_only", [False, True])
def test_retired_module_cli_refuses_before_runtime_or_inherited_lock_io(
    tmp_path, restart_only
):
    repo, runtime = tmp_path / "legacy-code", tmp_path / "missing-state"
    repo.mkdir()
    code = repo / "source-canary.py"
    code.write_bytes(b"OLD_CODE_CANNOT_BE_REPLACED\n")
    inherited = tmp_path / "caller-owned-update.lock"
    inherited.write_bytes(b"CALLER_LOCK_AUTHORITY")
    inherited.chmod(0o600)
    fd = os.open(inherited, os.O_RDWR)
    original = (inherited.read_bytes(), inherited.stat().st_mode, inherited.stat().st_ino)
    try:
        command = [
            sys.executable, "-m", "executor.autonomy.updater",
            "--repo", str(repo), "--runtime", str(runtime), "--port", "9344",
            "--lock-fd", str(fd),
        ]
        if restart_only:
            command.append("--restart-only")
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=5,
                                pass_fds=(fd,))
        assert result.returncode == 1
        assert result.stdout == "" and result.stderr == ""
        assert not runtime.exists()
        assert code.read_bytes() == b"OLD_CODE_CANNOT_BE_REPLACED\n"
        assert (inherited.read_bytes(), inherited.stat().st_mode,
                inherited.stat().st_ino) == original
        assert os.fstat(fd).st_ino == original[2]
        assert sorted(p.name for p in repo.iterdir()) == ["source-canary.py"]
    finally:
        os.close(fd)


def test_retired_cli_never_touches_supplied_lock_or_restart_flags(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("CLI retirement happens before lock or restart I/O")
    for name in ["acquire_update_lock", "_legacy_perform_update", "perform_update",
                 "runtime_safe_to_update", "_git", "_run", "write_update_state"]:
        monkeypatch.setattr(updater, name, forbidden)
    monkeypatch.setattr(updater.time, "sleep", forbidden)
    assert updater.main([
        "--repo", str(tmp_path / "nonexistent-code"),
        "--runtime", str(tmp_path / "nonexistent-state"),
        "--port", "9344", "--lock-fd", "987654", "--restart-only",
    ]) == 1
    assert list(tmp_path.iterdir()) == []


# The production diagnostics must work without a checkout or subprocess.
# These are real manifest/byte fixtures, not mocked integrity decisions.
def _diagnostic_packaged_fixture(tmp_path):
    from executor.autonomy import release

    source = tmp_path / "source"
    package = source / "executor"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text("VALUE = 'synthetic-release'\n")
    (source / "requirements.txt").write_text("synthetic-package==1.0\n")
    root = tmp_path / "Synthetic Manager.app" / "Contents" / "Resources"
    candidate = root / "release"
    source_identity = release.copy_source_candidate(source, candidate)
    runtime = root / "runtime"
    (runtime / "bin").mkdir(parents=True)
    # If diagnostics executed this fake interpreter, the test would fail.
    (runtime / "bin" / "python").write_text("#!/bin/sh\nexit 99\n")
    (runtime / "bin" / "python").chmod(0o700)
    (runtime / "owned-dependency.py").write_text("VALUE = 'synthetic-owned-dependency'\n")
    runtime_identity = release.runtime_manifest(runtime, candidate)
    (runtime / release.RUNTIME_MANIFEST_NAME).write_text(json.dumps(runtime_identity))
    return candidate, runtime, source_identity, runtime_identity


def _diagnostics_forbid_git_and_processes(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("packaged diagnostics must never invoke Git or a subprocess")

    monkeypatch.setattr(diagnostics, "_git", forbidden)
    monkeypatch.setattr(diagnostics.subprocess, "run", forbidden)


def test_packaged_diagnostics_use_real_manifest_identity_without_git_or_execution(
    tmp_path, monkeypatch
):
    candidate, runtime, source, payload = _diagnostic_packaged_fixture(tmp_path)
    _diagnostics_forbid_git_and_processes(monkeypatch)
    before = {str(path.relative_to(candidate.parent)): path.read_bytes()
              for path in candidate.parent.rglob("*") if path.is_file()}
    assert diagnostics.repository_state(candidate) == {
        "version": source["source_sha256"][:12],
        "branch": "packaged", "worktree_clean": None,
    }
    report = diagnostics.packaged_provenance(candidate)
    assert report == {
        "source_verified_now": True,
        "source_sha256": source["source_sha256"],
        "runtime_verified_now": True,
        "runtime_sha256": payload["runtime_sha256"],
        "requirements_sha256": payload["requirements_sha256"],
        # The unit runner is not the synthetic app interpreter. Manifest
        # integrity cannot turn this host process into an owned runtime.
        "interpreter_owned": False,
        "verification_scope": "payload_integrity_only",
        "signed_distribution_certified": False,
    }
    assert before == {str(path.relative_to(candidate.parent)): path.read_bytes()
                      for path in candidate.parent.rglob("*") if path.is_file()}
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize("damage", [
    "missing_source_manifest", "changed_source", "unexpected_git",
    "runtime_dependency_change", "missing_runtime_manifest", "runtime_alias",
])
def test_damaged_packaged_diagnostics_never_fall_back_to_git_or_claim_integrity(
    tmp_path, monkeypatch, damage
):
    from executor.autonomy import release

    candidate, runtime, _source, _payload = _diagnostic_packaged_fixture(tmp_path)
    if damage == "missing_source_manifest":
        (candidate / release.MANIFEST_NAME).unlink()
    elif damage == "changed_source":
        (candidate / "executor" / "main.py").write_text("PRIVATE_SOURCE_CANARY")
    elif damage == "unexpected_git":
        (candidate / ".git").mkdir()
        (candidate / ".git" / "config").write_text("PRIVATE_GIT_CANARY")
    elif damage == "runtime_dependency_change":
        (runtime / "owned-dependency.py").write_text("PRIVATE_DEPENDENCY_CANARY")
    elif damage == "missing_runtime_manifest":
        (runtime / release.RUNTIME_MANIFEST_NAME).unlink()
    else:
        actual = runtime.with_name("private-runtime-canary")
        runtime.rename(actual)
        runtime.symlink_to(actual, target_is_directory=True)
    _diagnostics_forbid_git_and_processes(monkeypatch)
    state = diagnostics.repository_state(candidate)
    report = diagnostics.packaged_provenance(candidate)
    assert state["branch"] == "packaged"
    assert state["worktree_clean"] is None
    if damage in {"missing_source_manifest", "changed_source", "unexpected_git"}:
        assert state["version"] == "unknown"
        assert report["source_verified_now"] is False
        assert report["source_sha256"] == ""
    else:
        assert report["source_verified_now"] is True
    assert report["runtime_verified_now"] is False
    assert report["runtime_sha256"] == ""
    assert report["requirements_sha256"] == ""
    assert report["interpreter_owned"] is False
    serialized = json.dumps(report)
    assert "PRIVATE_" not in serialized
    assert "private-runtime-canary" not in serialized
    assert str(tmp_path) not in serialized


def test_packaged_diagnostics_report_current_drift_without_rewriting_loaded_identity(
    tmp_path, monkeypatch
):
    from executor.autonomy import release

    candidate, runtime, source, payload = _diagnostic_packaged_fixture(tmp_path)
    queue, _worker, supervisor = _supervisor(tmp_path)
    supervisor.release_identity = {
        "status": "verified", "source_sha256": source["source_sha256"],
    }
    _diagnostics_forbid_git_and_processes(monkeypatch)
    monkeypatch.setattr(diagnostics.browser, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(diagnostics.browser, "_alive", lambda: False)
    before_tasks, before_events = queue.tasks(), queue.recent_events(1000)

    initial = diagnostics.collect_diagnostics(supervisor, repo_root=candidate)
    assert initial["packaged_release"]["loaded_source_matches_disk"] is True
    assert initial["packaged_release"]["runtime_verified_now"] is True
    assert initial["loaded_source"]["sha256"] == source["source_sha256"]

    (candidate / "executor" / "main.py").write_text("PRIVATE_RUNTIME_DRIFT_CANARY")
    drift = diagnostics.collect_diagnostics(supervisor, repo_root=candidate)
    assert drift["packaged_release"]["source_verified_now"] is False
    assert drift["packaged_release"]["loaded_source_matches_disk"] is False
    assert drift["loaded_source"] == initial["loaded_source"]
    assert drift["recovery"] == {
        "reason": "release_unverified", "action": "reinstall_verified_app",
    }

    # A new valid disk snapshot is still not what this process loaded.
    next_source = release.source_manifest(candidate)
    (candidate / release.MANIFEST_NAME).write_text(json.dumps(next_source))
    changed = diagnostics.collect_diagnostics(supervisor, repo_root=candidate)
    assert changed["packaged_release"]["source_verified_now"] is True
    assert changed["packaged_release"]["loaded_source_matches_disk"] is False
    assert changed["loaded_source"] == initial["loaded_source"]
    assert changed["recovery"] == {
        "reason": "release_changed", "action": "restart_verified_app",
    }
    assert queue.tasks() == before_tasks
    assert queue.recent_events(1000) == before_events
    for report in (initial, drift, changed):
        serialized = json.dumps(report)
        assert "PRIVATE_RUNTIME_DRIFT_CANARY" not in serialized
        assert "very-private-profile" not in serialized
        assert str(tmp_path) not in serialized
        assert report["safety"]["final_click_actor"] == "user"
        assert report["safety"]["submit_capability"] is False


def test_packaged_provenance_refuses_manifest_switched_during_runtime_verification(
    tmp_path, monkeypatch
):
    from executor.autonomy import release

    candidate, runtime, _source, _payload = _diagnostic_packaged_fixture(tmp_path)
    verify = diagnostics.verify_runtime_candidate
    def switched(root, source):
        passed = verify(root, source)
        assert passed is True
        path = runtime / release.RUNTIME_MANIFEST_NAME
        forged = json.loads(path.read_text())
        forged["runtime_sha256"] = "f" * 64
        path.write_text(json.dumps(forged))
        return passed
    monkeypatch.setattr(diagnostics, "verify_runtime_candidate", switched)
    _diagnostics_forbid_git_and_processes(monkeypatch)
    report = diagnostics.packaged_provenance(candidate)
    assert report["runtime_verified_now"] is False
    assert report["runtime_sha256"] == ""
    assert report["requirements_sha256"] == ""


@pytest.mark.parametrize("available", [True, False, "fault", None])
def test_diagnostic_provider_state_never_loads_credentials_or_starts_processes(
    tmp_path, monkeypatch, available
):
    candidate, _runtime, source, _payload = _diagnostic_packaged_fixture(tmp_path)
    queue, _worker, supervisor = _supervisor(tmp_path)
    supervisor.release_identity = {
        "status": "verified", "source_sha256": source["source_sha256"],
    }
    _diagnostics_forbid_git_and_processes(monkeypatch)
    monkeypatch.setattr(diagnostics.browser, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(diagnostics.browser, "_alive", lambda: False)
    from executor import resolver
    monkeypatch.setattr(
        resolver, "_keychain_key",
        lambda *_args, **_kwargs: pytest.fail("diagnostics must not read Keychain"),
    )
    class ExistingProvider:
        @property
        def available(self):
            if available == "fault":
                raise RuntimeError("PRIVATE_PROVIDER_FAILURE_CANARY")
            return available

        def decide(self, *_args, **_kwargs):
            pytest.fail("diagnostics must never invoke the model")

    monkeypatch.setattr(
        "executor.autonomy.manager.DeepSeekManagerProvider",
        lambda *_args, **_kwargs: pytest.fail("diagnostics must not initialize provider"),
    )
    supervisor.manager = ManagerController(
        queue, _worker, provider=ExistingProvider() if available is not None else None,
        settings=supervisor.manager.settings,
    )
    before = (queue.tasks(), queue.recent_events(1000))
    report = diagnostics.collect_diagnostics(supervisor, repo_root=candidate)
    assert report["system"]["deepseek_available"] is (available is True)
    assert report["system"]["provider_state_basis"] == "loaded_configuration"
    assert report["system"]["provider_state"] == (
        "available" if available is True else
        "not_loaded" if available is None else "unavailable")
    assert report["recovery"] == (
        {"reason": "none", "action": "none"} if available is True else
        {"reason": "provider_unavailable", "action": "check_provider_settings"}
    )
    assert report["loaded_source"]["sha256"] == source["source_sha256"]
    assert report["packaged_release"]["loaded_source_matches_disk"] is True
    assert report["packaged_release"]["runtime_verified_now"] is True
    assert before == (queue.tasks(), queue.recent_events(1000))
    serialized = json.dumps(report)
    assert "PRIVATE_PROVIDER_FAILURE_CANARY" not in serialized
    assert "very-private-profile" not in serialized
    assert str(tmp_path) not in serialized
    assert report["safety"]["submit_capability"] is False
