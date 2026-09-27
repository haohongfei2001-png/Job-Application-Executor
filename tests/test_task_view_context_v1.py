"""Explicit local view persistence never confers task or browser authority."""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.state_compatibility import stage_task_state_backup, verify_task_state_backup


def _task(q, suffix):
    return q.enqueue(TaskSpec(company="SYNTHETIC_PRIVATE_COMPANY", role="Synthetic",
        target_url="https://jobs.example.test/job/" + suffix,
        profile_ref="SYNTHETIC_PRIVATE_PROFILE", live_authorized=False))


def test_task_view_context_is_explicit_restart_durable_and_not_task_authority(tmp_path):
    q = TaskQueue(tmp_path / "state")
    a, b = _task(q, "one"), _task(q, "two")
    before_tasks, before_events = q.tasks(), q.events()
    assert q.task_view_context() == {"task_id": None, "revision": 0}
    saved = q.remember_task_view(b["task_id"], expected_revision=0)
    assert saved == {"task_id": b["task_id"], "revision": 1}
    restarted = TaskQueue(q.root)
    assert restarted.task_view_context() == saved
    assert restarted.tasks() == before_tasks
    assert restarted.events() == before_events
    assert q.path.stat().st_mode & 0o077 == 0
    assert q.root.stat().st_mode & 0o077 == 0
    assert "SYNTHETIC_PRIVATE" not in json.dumps(saved)
    assert restarted.remember_task_view(None, expected_revision=1) == {"task_id": None, "revision": 2}
    assert TaskQueue(q.root).task_view_context() == {"task_id": None, "revision": 2}
    assert q.get(a["task_id"]) == a
    assert q.get(b["task_id"]) == b


@pytest.mark.parametrize("task_id,revision", [
    (True, 0), ({"task_id": "private"}, 0), ("x" * 121, 0),
    ("private value\nbody", 0), (None, True), (None, 1.0), (None, -1),
])
def test_task_view_context_rejects_non_metadata_before_any_write(tmp_path, task_id, revision):
    q = TaskQueue(tmp_path / "state")
    _task(q, "one")
    before = q.tasks(), q.events(), q.task_view_context()
    with pytest.raises(ValueError):
        q.remember_task_view(task_id, expected_revision=revision)
    assert (q.tasks(), q.events(), q.task_view_context()) == before


def test_task_view_context_missing_target_and_lost_reply_never_retarget_or_replay(tmp_path):
    q = TaskQueue(tmp_path / "state")
    a, b = _task(q, "one"), _task(q, "two")
    q.remember_task_view(a["task_id"], expected_revision=0)
    with pytest.raises(RuntimeError):
        q.remember_task_view(b["task_id"], expected_revision=0)
    assert q.task_view_context() == {"task_id": a["task_id"], "revision": 1}
    with pytest.raises(ValueError):
        q.remember_task_view("missing", expected_revision=1)
    assert q.task_view_context() == {"task_id": a["task_id"], "revision": 1}
    with q.tx() as db:
        db.execute("DELETE FROM tasks WHERE task_id=?", (a["task_id"],))
    assert q.task_view_context() == {"task_id": None, "revision": 1}
    with q.tx() as db:
        assert db.execute("SELECT task_id FROM task_view_context").fetchone()[0] == a["task_id"]
    assert q.get(b["task_id"]) == b
    assert q.remember_task_view(None, expected_revision=1) == {"task_id": None, "revision": 2}


def test_task_view_context_concurrent_windows_use_same_journal_cas(tmp_path):
    q = TaskQueue(tmp_path / "state")
    a, b = _task(q, "one"), _task(q, "two")
    other = TaskQueue(q.root)
    gate = threading.Barrier(2)
    def remember(queue, target):
        gate.wait(timeout=5)
        try:
            return queue.remember_task_view(target, expected_revision=0)
        except RuntimeError:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(remember, q, a["task_id"]), pool.submit(remember, other, b["task_id"])]
        outcomes = [future.result(timeout=5) for future in futures]
    assert outcomes.count("stale") == 1
    winner = next(result for result in outcomes if result != "stale")
    assert winner["revision"] == 1
    assert TaskQueue(q.root).task_view_context() == winner
    assert q.get(a["task_id"]) == a and q.get(b["task_id"]) == b


def test_task_view_context_remains_in_whole_journal_backup_without_activation(tmp_path):
    q = TaskQueue(tmp_path / "state")
    task = _task(q, "one")
    expected = q.remember_task_view(task["task_id"], expected_revision=0)
    capsule = tmp_path / "backup"
    receipt = stage_task_state_backup(q.root, capsule)
    assert receipt["activation"] == "NOT_AUTHORIZED"
    assert receipt["source_authority"] == "unchanged"
    assert verify_task_state_backup(capsule, receipt)
    with sqlite3.connect(capsule / "tasks.sqlite3") as db:
        row = db.execute("SELECT task_id,revision FROM task_view_context WHERE slot=1").fetchone()
        assert row == (expected["task_id"], expected["revision"])
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert q.get(task["task_id"]) == task
    assert q.task_view_context() == expected


def test_task_view_context_http_session_origin_update_fence_and_exact_envelope(tmp_path, monkeypatch):
    q = TaskQueue(tmp_path / "state")
    task = _task(q, "one")
    supervisor = Supervisor(q, token="x" * 40)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    session = supervisor.consume_ui_ticket(supervisor.issue_ui_ticket())
    def post(data, *, authenticated=True, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            headers = {"Content-Type": "application/json", "Origin": origin or f"http://127.0.0.1:{port}"}
            if authenticated:
                headers["Cookie"] = "application_executor_session=" + session
            connection.request("POST", "/ui/api/task-view-context", json.dumps(data), headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    data = {"task_id": task["task_id"], "expected_revision": 0}
    try:
        assert post(data, authenticated=False) == (401, {"error": "ui_session_required"})
        assert post(data, origin="https://untrusted.example.test") == (403, {"error": "local_origin_required"})
        assert post({**data, "answer": "SECRET_CANARY"}) == (400, {"error": "invalid_request"})
        assert q.task_view_context() == {"task_id": None, "revision": 0}
        monkeypatch.setattr(supervisor, "mutation_fenced", lambda: True)
        assert post(data) == (409, {"error": "state_conflict"})
        assert q.task_view_context()["revision"] == 0
        monkeypatch.setattr(supervisor, "mutation_fenced", lambda: False)
        assert post(data) == (200, {"task_id": task["task_id"], "revision": 1})
        assert post(data) == (409, {"error": "state_conflict"})
        supervisor._ui_sessions.clear()
        assert post({"task_id": None, "expected_revision": 1})[0] == 401
        assert q.task_view_context() == {"task_id": task["task_id"], "revision": 1}
        assert q.get(task["task_id"]) == task
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
