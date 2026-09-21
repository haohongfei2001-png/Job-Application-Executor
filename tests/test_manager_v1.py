from __future__ import annotations

from pathlib import Path

import pytest

from executor.autonomy.manager import (
    ManagerAction,
    ManagerController,
    ManagerDecision,
    ManagerTurn,
    safe_task_view,
)
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor
from executor.autonomy.worker import Worker


def spec(tmp_path: Path, *, url="https://jobs.example.test/apply?postId=role-1"):
    profile = tmp_path / "profile.json"
    profile.write_text('{"identity": {"full_name": "Synthetic Applicant"}}')
    return TaskSpec(
        company="Synthetic Co",
        role="AI Product Manager",
        target_url=url,
        job_id="role-1" if "role-1" in url else "",
        profile_ref=str(profile),
        attachment_refs={"resume": str(tmp_path / "resume.docx")},
    )


class FakeProvider:
    available = True

    def __init__(self, turn):
        self.turn = turn
        self.seen = None

    def decide(self, message, tasks):
        self.seen = {"message": message, "tasks": tasks}
        return self.turn


class UnavailableProvider:
    available = False

    def decide(self, *_):
        raise RuntimeError("offline")


def controller(tmp_path, turn, *, settings=None):
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    provider = FakeProvider(turn)
    manager = ManagerController(
        q,
        worker,
        provider=provider,
        settings=settings or {"profile_path": str(tmp_path / "canonical.json")},
    )
    return q, worker, provider, manager


def test_safe_task_view_redacts_private_references_and_url_query(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    task = q.enqueue(spec(tmp_path))
    view = safe_task_view(task)
    assert view["target_host"] == "jobs.example.test"
    assert "profile_ref" not in view
    assert "attachment_refs" not in view
    assert "target_url" not in view
    assert "resume.docx" not in str(view)


def test_manager_report_is_read_only(tmp_path):
    turn = ManagerTurn(reply="当前没有需要修改的状态。", decisions=[
        ManagerDecision(action=ManagerAction.REPORT, reason="status only")
    ])
    q, _, provider, manager = controller(tmp_path, turn)
    tid = q.enqueue(spec(tmp_path))["task_id"]
    result = manager.handle("现在什么情况？")
    assert result["actions"] == [{"action": "REPORT", "status": "observed"}]
    assert q.get(tid)["stage"] == "DISCOVERED"
    assert provider.seen["tasks"][0]["target_host"] == "jobs.example.test"


def test_cancel_requires_explicit_user_intent(tmp_path):
    turn = ManagerTurn(reply="建议取消。", decisions=[
        ManagerDecision(action=ManagerAction.CANCEL, task_id="placeholder")
    ])
    q, _, provider, manager = controller(tmp_path, turn)
    tid = q.enqueue(spec(tmp_path))["task_id"]
    provider.turn.decisions[0].task_id = tid
    result = manager.handle("这个岗位现在怎么样？")
    assert result["actions"][0]["status"] == "denied"
    assert result["actions"][0]["reason"] == "explicit_cancel_required"
    assert q.get(tid)["stage"] == "DISCOVERED"

    result = manager.handle("取消这个岗位")
    assert result["actions"][0]["status"] == "accepted"
    assert q.get(tid)["stage"] == "CANCELLED"


def test_pending_answer_must_be_pending_and_verbatim_user_fact(tmp_path):
    field = "family.father.political_status"
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    tid = q.enqueue(spec(tmp_path))["task_id"]
    claimed = q.claim("test-worker")
    q.checkpoint(
        tid,
        claimed["owner"],
        "NEEDS_USER_INPUT",
        blocker="unknown_facts",
        details={"unresolved_keys": [field]},
        release=True,
    )
    provider = FakeProvider(ManagerTurn(reply="收到。", decisions=[
        ManagerDecision(
            action=ManagerAction.ANSWER_PENDING,
            task_id=tid,
            field_key=field,
            value="群众",
        )
    ]))
    manager = ManagerController(q, worker, provider=provider, settings={"profile_path": "unused"})

    denied = manager.handle("继续处理")
    assert denied["actions"][0]["status"] == "denied"
    assert q.get(tid)["stage"] == "NEEDS_USER_INPUT"

    accepted = manager.handle("父亲政治面貌填群众，继续")
    assert accepted["actions"][0]["status"] == "accepted"
    assert q.get(tid)["stage"] != "NEEDS_USER_INPUT"
    assert worker.answers[tid][field] == "群众"


def test_create_task_requires_explicit_apply_exact_url_and_local_profile(tmp_path):
    url = "https://jobs.example.test/apply?postId=new-role"
    profile = tmp_path / "canonical.json"
    profile.write_text("{}")
    turn = ManagerTurn(reply="开始处理。", decisions=[
        ManagerDecision(
            action=ManagerAction.CREATE_TASK,
            company="Example",
            role="AI产品经理",
            target_url=url,
        )
    ])
    q, _, provider, manager = controller(
        tmp_path,
        turn,
        settings={"profile_path": str(profile)},
    )
    denied = manager.handle("帮我看看这个职位")
    assert denied["actions"][0]["status"] == "denied"
    assert not q.tasks()

    accepted = manager.handle("投递这个职位：" + url)
    assert accepted["actions"][0]["status"] == "accepted"
    task = q.get(accepted["actions"][0]["task_id"])
    assert task["spec"]["profile_ref"] == str(profile)
    assert task["spec"]["live_authorized"] is True


def test_resume_ready_to_submit_is_never_allowed(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    tid = q.enqueue(spec(tmp_path))["task_id"]
    claimed = q.claim("test-worker")
    q.checkpoint(tid, claimed["owner"], "READY_TO_SUBMIT", release=True)
    provider = FakeProvider(ManagerTurn(reply="继续。", decisions=[
        ManagerDecision(action=ManagerAction.RESUME, task_id=tid)
    ]))
    manager = ManagerController(q, worker, provider=provider, settings={"profile_path": "unused"})
    result = manager.handle("继续这个岗位")
    assert result["actions"][0] == {
        "action": "RESUME",
        "status": "denied",
        "reason": "manual_submit_gate",
    }
    assert q.get(tid)["stage"] == "READY_TO_SUBMIT"


def test_unavailable_manager_fails_closed_without_mutating_queue(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    tid = q.enqueue(spec(tmp_path))["task_id"]
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    manager = ManagerController(
        q,
        worker,
        provider=UnavailableProvider(),
        settings={"profile_path": "unused"},
    )
    result = manager.handle("继续")
    assert result["actions"] == []
    assert "unavailable" in result["reply"].lower()
    assert q.get(tid)["stage"] == "DISCOVERED"


def test_ui_ticket_is_one_time_and_session_is_ephemeral(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    manager = ManagerController(
        q,
        worker,
        provider=UnavailableProvider(),
        settings={"profile_path": "unused"},
    )
    supervisor = Supervisor(q, worker=worker, token="x" * 40, manager=manager)
    ticket = supervisor.issue_ui_ticket()
    session = supervisor.consume_ui_ticket(ticket)
    assert session
    assert supervisor.consume_ui_ticket(ticket) is None
    assert supervisor.valid_ui_session(session)
    assert "final_click_actor" in supervisor.ui_state()


def test_supervisor_chat_route_has_no_submit_action(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    manager = ManagerController(
        q,
        worker,
        provider=FakeProvider(ManagerTurn(reply="只报告。", decisions=[])),
        settings={"profile_path": "unused"},
    )
    supervisor = Supervisor(q, worker=worker, token="x" * 40, manager=manager)
    result = supervisor.dispatch("POST", "/v1/chat", {"message": "查看状态"})
    assert result["reply"] == "只报告。"
    with pytest.raises(KeyError):
        supervisor.dispatch("POST", "/v1/submit", {})
