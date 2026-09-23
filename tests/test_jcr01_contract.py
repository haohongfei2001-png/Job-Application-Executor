from __future__ import annotations

import sqlite3
import threading
import time
import http.cookiejar
import json
import random
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

from executor.autonomy.commands import CommandEnvelope
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.manager import ManagerAction, ManagerController, ManagerDecision, ManagerTurn
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker
from executor.adapters.generic_web import GenericWebAdapter
from executor.models import ApplicationPlan, FieldResolution, ResolutionStatus
from executor.models import WebField
from executor.review import _covered
from executor.target_resolver import _location_matches


def task(queue, tmp_path):
    return queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=jcr01",
        profile_ref=str(tmp_path / "synthetic-profile.json"),
    ))


def test_command_receipt_replay_and_stale_revision(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    original = task(queue, tmp_path)
    tid = original["task_id"]
    command = CommandEnvelope(command_id="pause-jcr01-01", task_id=tid,
                              action="PAUSE", expected_revision=original["revision"])
    first = queue.control(**command.model_dump())
    assert first == queue.command_receipt(command.command_id)
    assert queue.control(**command.model_dump()) == first
    assert queue.get(tid)["revision"] == first["revision"]
    assert queue.get(tid)["run_state"] == "PAUSED"
    assert queue.get(tid)["paused_from"] == "DISCOVERED"
    with pytest.raises(RuntimeError, match="stale"):
        queue.control("RESUME", tid, command_id="resume-jcr01-01",
                      expected_revision=original["revision"])
    with pytest.raises(ValueError, match="reused"):
        queue.control("CANCEL", tid, command_id=command.command_id,
                      expected_revision=first["revision"])
    resumed = queue.control("RESUME", tid, command_id="resume-jcr01-02",
                            expected_revision=first["revision"])
    assert resumed["stage"] == "DISCOVERED"
    assert queue.get(tid)["paused_from"] is None


def test_command_cas_one_winner_across_queue_instances(tmp_path):
    root = tmp_path / "runtime"
    queue = TaskQueue(root)
    original = task(queue, tmp_path)
    barrier = threading.Barrier(2)

    def run(number):
        second = TaskQueue(root)
        barrier.wait()
        try:
            return second.control("PAUSE", original["task_id"],
                                  command_id=f"pause-race-{number:02d}",
                                  expected_revision=original["revision"])
        except RuntimeError:
            return "STALE"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, (1, 2)))
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert outcomes.count("STALE") == 1


def test_existing_task_database_migrates_without_changing_identity(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    path = root / "tasks.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE tasks(task_id TEXT PRIMARY KEY,idempotency_key TEXT UNIQUE NOT NULL,
                spec TEXT NOT NULL,stage TEXT NOT NULL,checkpoint TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,owner TEXT,lease_until REAL,
                next_run REAL NOT NULL DEFAULT 0,blocker TEXT,details TEXT NOT NULL DEFAULT '{}',
                created REAL NOT NULL,updated REAL NOT NULL,checkpoint_url TEXT);
            CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,at REAL,
                kind TEXT NOT NULL,stage TEXT NOT NULL);
        """)
        db.execute("INSERT INTO tasks(task_id,idempotency_key,spec,stage,checkpoint,created,updated) "
                   "VALUES('existing-task','old-key','{}','DISCOVERED','DISCOVERED',1,1)")
    queue = TaskQueue(root)
    existing = queue.get("existing-task")
    assert existing["task_id"] == "existing-task"
    assert existing["phase"] == "DISCOVERED"
    assert existing["revision"] == 0
    assert existing["run_state"] == "RUNNABLE"
    backup = root / "tasks.sqlite3.pre-jcr01.sqlite3"
    assert backup.exists()
    assert backup.stat().st_mode & 0o077 == 0
    with sqlite3.connect(backup) as old:
        assert old.execute("SELECT task_id FROM tasks").fetchone()[0] == "existing-task"
        assert "revision" not in {row[1] for row in old.execute("PRAGMA table_info(tasks)")}


def test_project_title_contains_does_not_certify_different_project():
    assert _covered("AI Product", ["AI Product Research"]) is False
    assert _covered("AI Product", ["AI Product"]) is True


def test_requested_city_cannot_match_different_or_unknown_city():
    assert _location_matches("北京", "北京市 / 海淀区")
    assert not _location_matches("北京", "上海市 / 浦东新区")
    assert not _location_matches("北京", "")


def test_city_select_rerender_cannot_claim_validated(tmp_path):
    html = tmp_path / "city.html"
    html.write_text('''<label>首选城市<select id="city" required>
        <option value=""></option><option value="bj">北京</option>
        <option value="sh">上海</option></select></label>''', encoding="utf-8")
    expected = FieldResolution(field_id="city", selector="#city", label="首选城市",
                               status=ResolutionStatus.RESOLVED, value="北京")
    plan = ApplicationPlan(execution_id="city-oracle", target_url=html.as_uri(),
                           site_id="generic_web", fields=[expected])
    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.apply_resolutions([expected])[0]["ok"]
        assert adapter.validate(plan).ok
        adapter.page.locator("#city").select_option("sh")
        result = adapter.validate(plan)
        assert not result.ok
        assert any("selected option" in error for error in result.errors)


def test_missing_field_check_only_covers_current_page(monkeypatch):
    adapter = GenericWebAdapter("https://jobs.example.test/apply")
    monkeypatch.setattr(adapter, "discover_fields", lambda: [
        WebField(field_id="city", selector="#city", label="City",
                 current_value="Beijing"),
    ])
    old = FieldResolution(field_id="name", selector="#name", label="Name",
                          status=ResolutionStatus.RESOLVED, value="Synthetic")
    current = FieldResolution(field_id="city", selector="#city", label="City",
                              status=ResolutionStatus.RESOLVED, value="Beijing")
    plan = ApplicationPlan(execution_id="multipage", target_url="https://jobs.example.test/apply",
                           site_id="generic_web", fields=[old, current],
                           metadata={"current_page_selectors": ["#city"]})
    assert adapter.validate(plan).ok
    plan.metadata["current_page_selectors"] = ["#city", "#missing"]
    plan.fields.append(FieldResolution(field_id="missing", selector="#missing",
        label="Missing", status=ResolutionStatus.RESOLVED, value="expected"))
    assert not adapter.validate(plan).ok


def test_command_cannot_resume_between_update_safety_check_and_start(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "runtime")
    tid = task(queue, tmp_path)["task_id"]
    queue.pause(tid)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    supervisor = Supervisor(queue, worker=worker)
    checking, proceed = threading.Event(), threading.Event()
    fenced = [False]
    monkeypatch.setattr(supervisor, "mutation_fenced", lambda: fenced[0])

    def check(_):
        checking.set()
        assert proceed.wait(3)
        return True, None

    def start(**_):
        fenced[0] = True
        return {"ok": True, "status": "started"}

    monkeypatch.setattr("executor.autonomy.supervisor.safe_to_update", check)
    monkeypatch.setattr("executor.autonomy.supervisor.spawn_update", start)
    monkeypatch.setattr(supervisor, "update_in_progress", lambda: False)
    results = {}
    update_thread = threading.Thread(target=lambda: results.update(supervisor.begin_update(9344)))
    update_thread.start()
    assert checking.wait(2)
    paused = queue.get(tid)
    command = CommandEnvelope(command_id="update-race-resume-01", task_id=tid,
        action="RESUME", expected_revision=paused["revision"])
    outcome = []

    def resume():
        try:
            outcome.append(supervisor.run_local_command(command))
        except RuntimeError:
            outcome.append("FENCED")

    command_thread = threading.Thread(target=resume)
    command_thread.start()
    proceed.set()
    update_thread.join(3)
    command_thread.join(3)
    assert results["ok"]
    assert outcome == ["FENCED"]
    assert queue.get(tid)["run_state"] == "PAUSED"


def test_ui_pause_does_not_wait_for_model_timeout(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowProvider:
        available = True

        def decide(self, *_):
            entered.set()
            release.wait(5)
            return ManagerTurn(reply="resume proposed", decisions=[
                ManagerDecision(action=ManagerAction.RESUME, task_id=original["task_id"])
            ])

    queue = TaskQueue(tmp_path / "runtime")
    original = task(queue, tmp_path)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    manager = ManagerController(queue, worker, provider=SlowProvider(), settings={})
    supervisor = Supervisor(queue, worker=worker, manager=manager)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()

    def post(path, body):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        return json.load(opener.open(request))

    chat_result = {}
    chat = threading.Thread(target=lambda: chat_result.update(
        post("/ui/api/chat", {"message": "继续任务"})), daemon=True)
    try:
        chat.start()
        assert entered.wait(2)
        start = time.monotonic()
        receipt = post("/ui/api/command", {"command_id": "local-pause-001",
            "task_id": original["task_id"], "action": "PAUSE",
            "expected_revision": original["revision"]})
        assert time.monotonic() - start < 2
        assert receipt["status"] == "accepted"
        assert queue.get(original["task_id"])["run_state"] == "PAUSED"
    finally:
        release.set()
        chat.join(3)
        assert chat_result["actions"][0]["reason"] == "stale_task_revision"
        assert queue.get(original["task_id"])["run_state"] == "PAUSED"
        server.shutdown()
        server.server_close()


def test_one_thousand_seeded_control_steps_preserve_manual_boundary(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    operations = 0
    for seed in range(50):
        row = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
            target_url=f"https://jobs.example.test/apply?postId=sequence-{seed}",
            profile_ref=str(tmp_path / "synthetic-profile.json")))
        tid = row["task_id"]
        rng = random.Random(seed)
        for step in range(20):
            before = queue.get(tid)
            action = rng.choice(("PAUSE", "RESUME", "CANCEL"))
            command_id = f"sequence-{seed:02d}-{step:02d}"
            try:
                receipt = queue.control(action, tid, command_id=command_id,
                                        expected_revision=before["revision"])
                assert receipt["revision"] == before["revision"] + 1
                assert queue.command_receipt(command_id) == receipt
            except (ValueError, RuntimeError):
                assert queue.get(tid)["revision"] == before["revision"]
            after = queue.get(tid)
            assert after["task_id"] == tid
            assert after["stage"] in {"DISCOVERED", "BLOCKED", "CANCELLED"}
            assert after["stage"] not in {"SUBMITTED", "VERIFIED"}
            operations += 1
    assert operations == 1000


@pytest.mark.parametrize("message", ["不要暂停这个任务", "我不想暂停", "do not pause this task", "别暂停"])
def test_negated_pause_cannot_be_authorized_by_model(tmp_path, message):
    queue = TaskQueue(tmp_path / "runtime")
    tid = task(queue, tmp_path)["task_id"]
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})

    class PauseProposal:
        available = True

        def decide(self, *_):
            return ManagerTurn(reply="pause proposed", decisions=[
                ManagerDecision(action=ManagerAction.PAUSE, task_id=tid)
            ])

    manager = ManagerController(queue, worker, provider=PauseProposal(), settings={})
    result = manager.handle(message)
    assert result["actions"][0]["status"] == "denied"
    assert queue.get(tid)["stage"] == "DISCOVERED"


def test_model_cannot_pause_task_a_when_user_names_task_b(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    a = task(queue, tmp_path)
    b = queue.enqueue(TaskSpec(company="Other Company", role="Designer",
        target_url="https://jobs.example.test/apply?postId=other",
        profile_ref=str(tmp_path / "synthetic-profile.json")))
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})

    class WrongProposal:
        available = True

        def decide(self, *_):
            return ManagerTurn(reply="pause proposed", decisions=[
                ManagerDecision(action=ManagerAction.PAUSE, task_id=a["task_id"])
            ])

    manager = ManagerController(queue, worker, provider=WrongProposal(), settings={})
    wrong = manager.handle("暂停 Other Company")
    assert wrong["actions"][0]["reason"] == "ambiguous_task_reference"
    assert queue.get(a["task_id"])["stage"] == "DISCOVERED"
    assert queue.get(b["task_id"])["stage"] == "DISCOVERED"
    right = manager.handle("暂停 Synthetic")
    assert right["actions"][0]["status"] == "accepted"
    assert queue.get(a["task_id"])["stage"] == "BLOCKED"
