from __future__ import annotations

from pathlib import Path
import http.cookiejar
import json
import threading
import urllib.error
import urllib.request

import pytest

from executor.autonomy.manager import (
    ManagerAction,
    ManagerController,
    ManagerDecision,
    ManagerTurn,
    safe_task_view,
)
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server
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
    assert provider.seen["tasks"][0]["task_id"] == tid
    assert "target_host" not in provider.seen["tasks"][0]


def test_chat_apply_without_created_task_gives_local_form_guidance(tmp_path):
    q, _, provider, manager = controller(
        tmp_path, ManagerTurn(reply="我已经开始投递。", decisions=[]),
    )
    result = manager.handle("请申请 https://jobs.example.test/roles/role-1")
    assert result["actions"] == []
    assert not q.tasks()
    assert "未创建任务" in result["reply"]
    assert "左侧" in result["reply"]
    assert "jobs.example.test/roles" not in provider.seen["message"]


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
    assert json.loads(provider.seen["message"])["private_answer_pending"] is True
    assert "群众" not in provider.seen["message"]


def test_create_task_requires_explicit_apply_exact_url_and_local_profile(tmp_path):
    url = "https://jobs.example.test/apply?position=AI,ML"
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

    # "apply" inside the URL is not independent user intent.
    denied = manager.handle("帮我看看这个链接：" + url)
    assert denied["actions"][0]["status"] == "denied"
    assert denied["actions"][0]["reason"] == "explicit_apply_required"
    assert not q.tasks()

    # The model cannot shorten or otherwise mutate a precise user-supplied URL.
    provider.turn.decisions[0].target_url = "https://jobs.example.test/apply"
    denied = manager.handle("投递这个职位：" + url)
    assert denied["actions"][0]["status"] == "denied"
    assert denied["actions"][0]["reason"] == "exact_url_required"
    assert not q.tasks()
    provider.turn.decisions[0].target_url = url

    accepted = manager.handle(url + "， 请帮我申请")
    assert accepted["actions"][0]["status"] == "accepted"
    task = q.get(accepted["actions"][0]["task_id"])
    assert task["spec"]["profile_ref"] == str(profile)
    assert task["spec"]["live_authorized"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.example.test/apply?position=AI,ML",
        "https://jobs.example.test/apply?position=AI;ML",
        "https://jobs.example.test/roles/engineer_(ml)",
    ],
)
def test_create_task_preserves_legal_url_punctuation(tmp_path, url):
    profile = tmp_path / "canonical.json"
    profile.write_text("{}")
    turn = ManagerTurn(reply="开始处理。", decisions=[
        ManagerDecision(
            action=ManagerAction.CREATE_TASK,
            company="Example",
            role="Engineer",
            target_url=url,
        )
    ])
    q, _, provider, manager = controller(
        tmp_path,
        turn,
        settings={"profile_path": str(profile)},
    )

    accepted = manager.handle("请申请这个岗位 " + url)
    assert accepted["actions"][0]["status"] == "accepted"

    # A model-proposed prefix of a longer URL is never accepted as exact.
    if url.endswith(")"):
        q2, worker2, _, _ = controller(
            tmp_path / "truncated",
            ManagerTurn(reply="开始处理。", decisions=[]),
            settings={"profile_path": str(profile)},
        )
        truncated = url[:-1]
        provider2 = FakeProvider(ManagerTurn(reply="开始处理。", decisions=[
            ManagerDecision(
                action=ManagerAction.CREATE_TASK,
                company="Example",
                role="Engineer",
                target_url=truncated,
            )
        ]))
        manager2 = ManagerController(
            q2,
            worker2,
            provider=provider2,
            settings={"profile_path": str(profile)},
        )
        denied = manager2.handle("请申请这个岗位 " + url)
        assert denied["actions"][0]["status"] == "denied"
        assert denied["actions"][0]["reason"] == "exact_url_required"


@pytest.mark.parametrize("suffix", [".", "!", "?", ")"])
def test_create_task_accepts_ascii_sentence_punctuation(tmp_path, suffix):
    url = "https://jobs.example.test/apply?postId=punctuated"
    profile = tmp_path / "canonical.json"
    profile.write_text("{}")
    turn = ManagerTurn(reply="开始处理。", decisions=[
        ManagerDecision(
            action=ManagerAction.CREATE_TASK,
            company="Example",
            role="Engineer",
            target_url=url,
        )
    ])
    q, _, _, manager = controller(
        tmp_path,
        turn,
        settings={"profile_path": str(profile)},
    )
    result = manager.handle("Apply " + url + suffix)
    assert result["actions"][0]["status"] == "accepted"
    assert q.get(result["actions"][0]["task_id"])["spec"]["target_url"] == url


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


@pytest.mark.parametrize("message", [
    "验证码是 482913",
    "验证码是482913五分钟有效",
    "OTP: 72941836",
    "Your verification code is 7294",
    "password: super-secret-value",
    "my password is hunter2",
    "token = abcdefghijklmnop",
    '{"password":"hunter2"}',
    '{"token":"abcdefghijklmnop"}',
    '{"otp":"7294"}',
    '{"密码":"hunter2"}',
    '{"验证码":"7294"}',
    '{"令牌":"abcdefghijklmnop"}',
    '{"password":"my very secret passphrase"}',
    '{"密码":"我的 私密 口令"}',
    "password hunter2",
    "my password is a very long passphrase",
    "password abc",
    "token abcdefghijklmnop",
    "密码 hunter2",
    "我的密码是a very long passphrase",
    "令牌 abcdefghijklmnop",
    "Bearer x",
    "Bearer abcdefghijklmnop",
    "OPENAI_API_KEY=sk-test",
    "ACCESS_TOKEN=abc",
    "CLIENT_PASSWORD=a",
    "AWS_SECRET_ACCESS_KEY=abc",
    '"password"="abc"',
    '{"OPENAI_API_KEY":"sk-test"}',
    "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
    "-----BEGIN PGP PRIVATE KEY BLOCK-----\nabc\n-----END PGP PRIVATE KEY BLOCK-----",
])
def test_sensitive_chat_is_rejected_before_provider(tmp_path, message):
    turn = ManagerTurn(reply="不应调用模型。", decisions=[])
    _, _, provider, manager = controller(tmp_path, turn)
    result = manager.handle(message)
    assert result["actions"] == []
    assert provider.seen is None
    assert "DeepSeek" in result["reply"]


@pytest.mark.parametrize("blocker", ["otp_waiting", "otp_ambiguous"])
@pytest.mark.parametrize("paused", [False, True])
def test_unlabeled_otp_is_local_only_while_otp_is_waiting(tmp_path, blocker, paused):
    turn = ManagerTurn(reply="不应调用模型。", decisions=[])
    q, _, provider, manager = controller(tmp_path, turn)
    tid = q.enqueue(spec(tmp_path))["task_id"]
    claimed = q.claim("test-worker")
    q.checkpoint(
        tid,
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker=blocker,
        release=True,
    )
    if paused:
        paused_task = q.pause(tid)
        assert paused_task["blocker"] == "user_paused_from_" + blocker

    result = manager.handle("482913")
    assert result["actions"] == []
    assert provider.seen is None
    assert "DeepSeek" in result["reply"]


def test_unlabeled_numeric_chat_is_minimized_before_provider(tmp_path):
    turn = ManagerTurn(reply="只报告。", decisions=[])
    _, _, provider, manager = controller(tmp_path, turn)
    result = manager.handle("482913")
    assert result["reply"] == "只报告。"
    assert "482913" not in provider.seen["message"]


def test_create_task_rejects_prefix_before_internal_localized_punctuation(tmp_path):
    full_url = "https://jobs.example.test/roles/工程，研发"
    prefix = "https://jobs.example.test/roles/工程"
    profile = tmp_path / "canonical.json"
    profile.write_text("{}")
    turn = ManagerTurn(reply="开始处理。", decisions=[
        ManagerDecision(
            action=ManagerAction.CREATE_TASK,
            company="Example",
            role="Engineer",
            target_url=prefix,
        )
    ])
    q, _, _, manager = controller(
        tmp_path,
        turn,
        settings={"profile_path": str(profile)},
    )
    denied = manager.handle("请申请这个岗位 " + full_url)
    assert denied["actions"][0]["status"] == "denied"
    assert denied["actions"][0]["reason"] == "exact_url_required"
    assert not q.tasks()


def test_numeric_pending_answer_requires_complete_number_boundary(tmp_path):
    field = "experience.years"
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
            value=1,
        )
    ]))
    manager = ManagerController(q, worker, provider=provider, settings={"profile_path": "unused"})

    for message in (
        "我有10年相关经验",
        "I have 1.5 years of experience",
        "I have 1,5 years of experience",
        "我有1，5年相关经验",
        "I have -1 years",
        "I have 1,000 hours",
        "I have 1e3 hours",
        "release v1",
    ):
        denied = manager.handle(message)
        assert denied["actions"][0]["status"] == "denied"
        assert q.get(tid)["stage"] == "NEEDS_USER_INPUT"

    accepted = manager.handle("我有1年相关经验")
    assert accepted["actions"][0]["status"] == "accepted"
    assert worker.answers[tid][field] == 1


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


def test_ui_ticket_and_session_expire_without_auth_relaxation(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("executor.autonomy.supervisor.time.monotonic", lambda: now[0])
    q = TaskQueue(tmp_path / "runtime")
    supervisor = Supervisor(q, token="x" * 40)
    expired_ticket = supervisor.issue_ui_ticket()
    now[0] += 61
    assert supervisor.consume_ui_ticket(expired_ticket) is None
    session = supervisor.consume_ui_ticket(supervisor.issue_ui_ticket())
    assert supervisor.valid_ui_session(session)
    now[0] += 3601
    assert not supervisor.valid_ui_session(session)


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


def test_pause_requires_explicit_intent_and_is_resumable(tmp_path):
    turn = ManagerTurn(reply="先暂停。", decisions=[
        ManagerDecision(action=ManagerAction.PAUSE, task_id="placeholder")
    ])
    q, _, provider, manager = controller(tmp_path, turn)
    tid = q.enqueue(spec(tmp_path))["task_id"]
    provider.turn.decisions[0].task_id = tid

    denied = manager.handle("这个岗位先看看")
    assert denied["actions"][0]["status"] == "denied"
    assert q.get(tid)["stage"] == "DISCOVERED"

    accepted = manager.handle("先暂停这个岗位")
    assert accepted["actions"][0]["status"] == "accepted"
    assert q.get(tid)["stage"] == "BLOCKED"
    assert q.get(tid)["blocker"] == "user_paused"

    resume_turn = ManagerTurn(reply="继续。", decisions=[
        ManagerDecision(action=ManagerAction.RESUME, task_id=tid)
    ])
    provider.turn = resume_turn
    resumed = manager.handle("继续这个岗位")
    assert resumed["actions"][0]["status"] == "accepted"
    assert q.get(tid)["stage"] == "DISCOVERED"


def test_boolean_answer_does_not_flip_negated_chinese_text(tmp_path):
    field = "preferences.accept_role_adjustment"
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
            value=True,
        )
    ]))
    manager = ManagerController(q, worker, provider=provider, settings={"profile_path": "unused"})
    result = manager.handle("不接受岗位调剂")
    assert result["actions"][0]["status"] == "denied"
    assert q.get(tid)["stage"] == "NEEDS_USER_INPUT"


def test_dashboard_http_ticket_cookie_and_same_origin_chat(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    provider = FakeProvider(ManagerTurn(reply="状态正常。", decisions=[]))
    manager = ManagerController(
        q,
        worker,
        provider=provider,
        settings={"profile_path": "unused"},
    )
    supervisor = Supervisor(q, worker=worker, token="x" * 40, manager=manager)
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
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(base + "/ui-login?ticket=" + ticket) as response:
            assert response.geturl().endswith("/ui")
            assert "AI 投递经理" in response.read().decode("utf-8")

        with opener.open(base + "/ui/api/state") as response:
            state = json.load(response)
        assert state["final_click_actor"] == "user"

        chat_request = urllib.request.Request(
            base + "/ui/api/chat",
            data=json.dumps({"message": "查看状态"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": base,
            },
            method="POST",
        )
        with opener.open(chat_request) as response:
            assert json.load(response)["reply"] == "状态正常。"

        create_request = urllib.request.Request(
            base + "/ui/api/tasks",
            data=json.dumps({
                "company": "Synthetic Co", "role": "AI Product Manager",
                "target_url": "https://jobs.example.test/roles/role-1",
            }).encode(),
            headers={"Content-Type": "application/json", "Origin": base},
            method="POST",
        )
        with opener.open(create_request) as response:
            created = json.load(response)
        assert q.get(created["task_id"])["spec"]["target_url"] == "https://jobs.example.test/roles/role-1"
        assert q.get(created["task_id"])["spec"]["live_authorized"] is False
        assert "jobs.example.test/roles" not in provider.seen["message"]

        evil = urllib.request.Request(
            base + "/ui/api/chat",
            data=json.dumps({"message": "查看状态"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.test",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(evil)
        assert exc.value.code == 403

        wrong_host = urllib.request.Request(base + "/ui/api/state", headers={"Host": "evil.test"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(wrong_host)
        assert exc.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_pause_fences_active_worker_lease(tmp_path):
    q = TaskQueue(tmp_path / "runtime")
    tid = q.enqueue(spec(tmp_path))["task_id"]
    claimed = q.claim("active-worker")
    paused = q.pause(tid)
    assert paused["stage"] == "BLOCKED"
    assert paused["blocker"] == "user_paused"
    assert paused["owner"] is None
    assert not q.renew(tid, claimed["owner"])
    with pytest.raises(RuntimeError):
        q.checkpoint(tid, claimed["owner"], "FORM_FILLED")


def test_explicit_resume_of_single_human_action_wait_is_local_and_deterministic(tmp_path):
    turn = ManagerTurn(
        reply="请先手动完成安全验证。",
        decisions=[ManagerDecision(action=ManagerAction.REPORT, reason="challenge")],
    )
    q, _, provider, manager = controller(tmp_path, turn)
    tid = q.enqueue(spec(tmp_path))["task_id"]
    claimed = q.claim("test-worker")
    q.checkpoint(
        tid,
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="security_challenge",
        release=True,
    )

    result = manager.handle("继续这个岗位")

    assert result["actions"] == [{
        "action": "RESUME",
        "status": "accepted",
        "task_id": tid,
    }]
    assert q.get(tid)["stage"] == "DISCOVERED"
    assert q.get(tid)["blocker"] is None
    assert provider.seen is None
    assert "重新检查当前页面" in result["reply"]


def test_explicit_resume_does_not_guess_between_multiple_human_action_tasks(tmp_path):
    turn = ManagerTurn(
        reply="需要明确岗位。",
        decisions=[ManagerDecision(action=ManagerAction.REPORT, reason="ambiguous")],
    )
    q, _, provider, manager = controller(tmp_path, turn)
    tids = []
    for suffix in ("role-1", "role-2"):
        task = q.enqueue(spec(
            tmp_path,
            url=f"https://jobs.example.test/apply?postId={suffix}",
        ))
        tids.append(task["task_id"])
        claimed = q.claim("test-worker")
        q.checkpoint(
            task["task_id"],
            claimed["owner"],
            "NEEDS_USER_ACTION",
            blocker="security_challenge",
            release=True,
        )

    result = manager.handle("继续这个岗位")

    assert result["actions"] == [{"action": "REPORT", "status": "observed"}]
    assert provider.seen is not None
    assert all(q.get(tid)["stage"] == "NEEDS_USER_ACTION" for tid in tids)


def test_conflicting_resume_and_pause_text_never_uses_local_resume_fast_path(tmp_path):
    turn = ManagerTurn(
        reply="先暂停。",
        decisions=[ManagerDecision(action=ManagerAction.PAUSE, task_id="placeholder")],
    )
    q, _, provider, manager = controller(tmp_path, turn)
    tid = q.enqueue(spec(tmp_path))["task_id"]
    claimed = q.claim("test-worker")
    q.checkpoint(
        tid,
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="security_challenge",
        release=True,
    )
    provider.turn.decisions[0].task_id = tid

    result = manager.handle("继续但先暂停这个岗位")

    assert provider.seen is not None
    assert result["actions"][0]["action"] == "PAUSE"
    assert result["actions"][0]["status"] == "accepted"
    assert q.get(tid)["stage"] == "BLOCKED"


def test_create_oppo_landing_task_resolves_to_unique_exact_job(tmp_path, monkeypatch):
    from executor.target_resolver import Candidate, OPPO_CAMPUS_ROOT

    profile = tmp_path / "canonical.json"
    profile.write_text("{}")
    turn = ManagerTurn(reply="开始处理。", decisions=[
        ManagerDecision(
            action=ManagerAction.CREATE_TASK,
            company="OPPO",
            role="AI产品经理",
            target_url=OPPO_CAMPUS_ROOT,
        )
    ])
    q, _, _, manager = controller(
        tmp_path,
        turn,
        settings={"profile_path": str(profile)},
    )
    candidate = Candidate(
        title="AI 产品经理",
        job_id="2048",
        location="北京市",
        category="产品类",
        job_url=OPPO_CAMPUS_ROOT + "/post/2048?recruitType=Campus",
        exact_title=True,
    )
    monkeypatch.setattr(
        "executor.autonomy.manager.resolve_known_landing",
        lambda company, role, url: candidate,
    )

    result = manager.handle(
        "投递 OPPO 的 AI产品经理：" + OPPO_CAMPUS_ROOT
    )

    action = result["actions"][0]
    assert action["status"] == "accepted"
    assert action["resolved_target"] is True
    task = q.get(action["task_id"])
    assert task["spec"]["target_url"] == candidate.job_url
    assert task["spec"]["job_id"] == "2048"


def test_create_oppo_landing_task_fails_closed_without_unique_exact_job(
    tmp_path, monkeypatch
):
    from executor.target_resolver import OPPO_CAMPUS_ROOT

    profile = tmp_path / "canonical.json"
    profile.write_text("{}")
    turn = ManagerTurn(reply="开始处理。", decisions=[
        ManagerDecision(
            action=ManagerAction.CREATE_TASK,
            company="OPPO",
            role="AI产品经理",
            target_url=OPPO_CAMPUS_ROOT,
        )
    ])
    q, _, _, manager = controller(
        tmp_path,
        turn,
        settings={"profile_path": str(profile)},
    )
    monkeypatch.setattr(
        "executor.autonomy.manager.resolve_known_landing",
        lambda *_: None,
    )

    result = manager.handle(
        "投递 OPPO 的 AI产品经理：" + OPPO_CAMPUS_ROOT
    )

    assert result["actions"][0] == {
        "action": "CREATE_TASK",
        "status": "denied",
        "reason": "exact_job_resolution_required",
    }
    assert q.tasks() == []


def test_resume_existing_oppo_landing_task_retargets_same_task(tmp_path, monkeypatch):
    from executor.target_resolver import Candidate, OPPO_CAMPUS_ROOT

    q = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    task = q.enqueue(TaskSpec(
        company="OPPO",
        role="AI产品经理",
        target_url=OPPO_CAMPUS_ROOT,
        profile_ref=str(profile),
        live_authorized=True,
    ))
    claimed = q.claim("test-worker")
    q.checkpoint(
        task["task_id"],
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="security_challenge",
        release=True,
    )
    candidate = Candidate(
        title="AI 产品经理",
        job_id="2048",
        location="北京市",
        category="产品类",
        job_url=OPPO_CAMPUS_ROOT + "/post/2048?recruitType=Campus",
        exact_title=True,
    )
    monkeypatch.setattr(
        "executor.autonomy.manager.resolve_known_landing",
        lambda *_: candidate,
    )
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    provider = FakeProvider(ManagerTurn(
        reply="不应调用模型。",
        decisions=[ManagerDecision(action=ManagerAction.REPORT)],
    ))
    manager = ManagerController(
        q,
        worker,
        provider=provider,
        settings={"profile_path": str(profile)},
    )

    result = manager.handle("继续这个岗位")

    assert result["actions"][0]["action"] == "RESUME"
    assert result["actions"][0]["status"] == "accepted"
    assert result["actions"][0]["resolved_target"] is True
    assert provider.seen is None
    retargeted = q.get(task["task_id"])
    assert retargeted["stage"] == "DISCOVERED"
    assert retargeted["checkpoint"] == "DISCOVERED"
    assert retargeted["blocker"] is None
    assert retargeted["attempts"] == 0
    assert retargeted["spec"]["target_url"] == candidate.job_url
    assert retargeted["spec"]["job_id"] == "2048"


def test_queue_retarget_rejects_cross_origin_or_non_detail_target(tmp_path):
    from executor.target_resolver import OPPO_CAMPUS_ROOT

    q = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    task = q.enqueue(TaskSpec(
        company="OPPO",
        role="AI产品经理",
        target_url=OPPO_CAMPUS_ROOT,
        profile_ref=str(profile),
        live_authorized=True,
    ))

    with pytest.raises(ValueError):
        q.retarget_same_origin_landing(
            task["task_id"],
            "https://evil.example/post/2048",
            job_id="2048",
        )
    with pytest.raises(ValueError):
        q.retarget_same_origin_landing(
            task["task_id"],
            OPPO_CAMPUS_ROOT + "/about",
            job_id="2048",
        )

    unchanged = q.get(task["task_id"])
    assert unchanged["stage"] == "DISCOVERED"
    assert unchanged["spec"]["target_url"] == OPPO_CAMPUS_ROOT


def test_model_selected_oppo_resume_also_retargets_landing(tmp_path, monkeypatch):
    from executor.target_resolver import Candidate, OPPO_CAMPUS_ROOT

    q = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    oppo = q.enqueue(TaskSpec(
        company="OPPO 2027届校园招聘",
        role="AI产品经理",
        target_url=OPPO_CAMPUS_ROOT,
        profile_ref=str(profile),
        live_authorized=True,
    ))
    claimed = q.claim("worker-oppo")
    q.checkpoint(
        oppo["task_id"],
        claimed["owner"],
        "NEEDS_USER_ACTION",
        blocker="security_challenge",
        release=True,
    )

    other = q.enqueue(TaskSpec(
        company="Other Co",
        role="Other Role",
        target_url="https://jobs.example.test/apply?postId=other",
        job_id="other",
        profile_ref=str(profile),
        live_authorized=True,
    ))
    claimed_other = q.claim("worker-other")
    q.checkpoint(
        other["task_id"],
        claimed_other["owner"],
        "NEEDS_USER_ACTION",
        blocker="security_challenge",
        release=True,
    )

    candidate = Candidate(
        title="AI 产品经理",
        job_id="2048",
        location="北京市",
        category="产品类",
        job_url=OPPO_CAMPUS_ROOT + "/post/2048?recruitType=Campus",
        exact_title=True,
    )
    monkeypatch.setattr(
        "executor.autonomy.manager.resolve_known_landing",
        lambda *_: candidate,
    )
    provider = FakeProvider(ManagerTurn(
        reply="继续 OPPO。",
        decisions=[
            ManagerDecision(
                action=ManagerAction.RESUME,
                task_id=oppo["task_id"],
            )
        ],
    ))
    worker = Worker(q, settings={"deepseek": {"enabled": False}})
    manager = ManagerController(
        q,
        worker,
        provider=provider,
        settings={"profile_path": str(profile)},
    )

    result = manager.handle("继续 OPPO 这个岗位")

    assert provider.seen is not None
    assert result["actions"][0]["status"] == "accepted"
    assert result["actions"][0]["resolved_target"] is True
    assert q.get(oppo["task_id"])["spec"]["target_url"] == candidate.job_url
    assert q.get(oppo["task_id"])["stage"] == "DISCOVERED"
    assert q.get(other["task_id"])["stage"] == "NEEDS_USER_ACTION"
