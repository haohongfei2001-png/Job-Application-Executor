from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import threading
import time
import urllib.request

import pytest

from executor.autonomy import diagnostics, updater
from executor.autonomy.manager import ManagerController, ManagerTurn
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker


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
    claimed = q.claim("diagnostic-worker")
    q.checkpoint(
        task["task_id"],
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="otp_waiting",
        release=True,
    )
    code = "48291357"
    pushed = worker.broker.push(message="验证码 " + code, task_id=task["task_id"])
    assert pushed["accepted"] is True

    monkeypatch.setattr(diagnostics.browser, "browser_mode", lambda: "live")
    monkeypatch.setattr(diagnostics.browser, "_alive", lambda: True)

    class FakeMapper:
        def __init__(self, *_):
            self.available = True

    monkeypatch.setattr(diagnostics, "DeepSeekMapper", FakeMapper)
    report = diagnostics.collect_diagnostics(
        supervisor,
        repo_root=tmp_path,
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["format"] == "application-executor-diagnostics-v1"
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
    assert report["tasks"][0]["target_host"] == "jobs.example.test"


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
        denied = updater.spawn_update(
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
    assert update_result["status"] == "started"
    assert len(spawned) == 1


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

    rc = updater.perform_update(
        tmp_path / "repo",
        tmp_path / "runtime",
        9344,
    )

    assert rc == 1
    assert ["git", "merge", "--ff-only", "origin/main"] not in git_calls
    state = updater.read_update_state(tmp_path / "runtime")
    assert state["status"] == "failed"
    assert state["reason"] == "tracked_changes_present"


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

    rc = updater.perform_update(
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

    rc = updater.perform_update(
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

    rc = updater.perform_update(
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
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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
