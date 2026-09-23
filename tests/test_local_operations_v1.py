from __future__ import annotations

import http.cookiejar
import json
import subprocess
import threading
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
        "executor.autonomy.supervisor.read_update_state",
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

    # Read-only diagnostics remain available during an update.
    monkeypatch.setattr(
        supervisor,
        "diagnostics",
        lambda: {"format": "application-executor-diagnostics-v1"},
    )
    assert supervisor.dispatch("GET", "/v1/diagnostics", {})["format"] == (
        "application-executor-diagnostics-v1"
    )
