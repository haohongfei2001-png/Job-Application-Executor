from __future__ import annotations

import json

import pytest

from executor.autonomy.manager import ManagerController
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor
from executor.autonomy.worker import Worker
from executor.models import ApplicationPlan, FieldResolution, ResolutionStatus


class _UnavailableProvider:
    available = False

    def decide(self, *_):
        raise RuntimeError("offline")


def _waiting_task(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text('{"fields":{}}', encoding="utf-8")
    queue = TaskQueue(tmp_path / "runtime")
    worker = Worker(queue, settings={"deepseek": {"enabled": False},
                                     "profile_path": str(profile)})
    manager = ManagerController(queue, worker, provider=_UnavailableProvider(),
                                settings={"profile_path": str(profile)})
    supervisor = Supervisor(queue, worker=worker, token="x" * 40, manager=manager)
    task = queue.enqueue(TaskSpec(company="Synthetic Co", role="Engineer",
                                  target_url="https://jobs.example.test/apply?postId=city-1",
                                  job_id="city-1", profile_ref=str(profile),
                                  live_authorized=True))
    tid = task["task_id"]
    claimed = queue.claim("question-worker")
    queue.checkpoint(tid, claimed["owner"], "NEEDS_USER_INPUT",
                     blocker="unknown_facts",
                     details={"unresolved_keys": ["identity.current_city"]},
                     release=True)
    current = queue.get(tid)
    return queue, worker, supervisor, current


def _plan(task, fields):
    return ApplicationPlan(execution_id=task["task_id"],
                           target_url=task["spec"]["target_url"],
                           site_id="synthetic", unresolved_fields=fields)


def test_local_question_requires_current_site_label_and_revision(tmp_path):
    queue, worker, supervisor, task = _waiting_task(tmp_path)
    tid, revision = task["task_id"], task["revision"]
    label = "当前居住城市（招聘网站原文）"

    assert supervisor.ui_state()["tasks"][0]["question_context"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="question context"):
        supervisor.run_local_fact(tid, "identity.current_city", "上海", revision)

    field = FieldResolution(field_id="city-field", selector="#city", label=label,
                            canonical_key="identity.current_city",
                            status=ResolutionStatus.UNRESOLVED, required=True)
    worker._remember_question_context(tid, _plan(task, [field]), revision)

    assert supervisor.ui_state()["tasks"][0]["question_context"] == {
        "status": "current",
        "items": [{"key": "identity.current_city", "label": label,
                   "required": True, "scope_sha256": None}],
    }
    assert label not in json.dumps(supervisor.manager.state(), ensure_ascii=False)
    with pytest.raises(ValueError, match="question context"):
        supervisor.run_local_fact(tid, "identity.current_city", "上海", revision + 1)

    accepted = supervisor.run_local_fact(tid, "identity.current_city", "上海", revision)
    assert accepted["status"] == "accepted"
    assert queue.get(tid)["stage"] != "NEEDS_USER_INPUT"
    assert worker.question_context(tid, revision) is None


def test_question_context_rejects_ambiguous_and_changed_target(tmp_path):
    queue, worker, supervisor, task = _waiting_task(tmp_path)
    tid, revision = task["task_id"], task["revision"]
    fields = [
        FieldResolution(field_id=f"city-{i}", selector=f"#city-{i}",
                        label=f"城市问题 {i}", canonical_key="identity.current_city",
                        status=ResolutionStatus.UNRESOLVED)
        for i in (1, 2)
    ]
    worker._remember_question_context(tid, _plan(task, fields), revision)
    assert supervisor.ui_state()["tasks"][0]["question_context"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="question context"):
        supervisor.run_local_fact(tid, "identity.current_city", "上海", revision)

    worker._remember_question_context(tid, _plan(task, fields[:1]), revision)
    assert worker.question_context(tid, revision)["status"] == "current"
    with queue.tx() as db:
        row = db.execute("SELECT spec FROM tasks WHERE task_id=?", (tid,)).fetchone()
        spec = json.loads(row[0])
        spec["target_url"] = "https://jobs.example.test/apply?postId=city-2"
        db.execute("UPDATE tasks SET spec=? WHERE task_id=?",
                   (json.dumps(spec), tid))
    assert worker.question_context(tid, revision) is None
