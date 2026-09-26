"""Value-free field intents retain uncertainty; DOM readback is not server proof."""
import hashlib
import json

import pytest

from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import OperationalAudit
from executor.browser import BrowserOwnershipError
from executor.models import FieldResolution, ResolutionStatus


def _owned(tmp_path, *, clock=None):
    ticks = [100.0] if clock is None else clock
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: ticks[0])
    task = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
        target_url="https://example.invalid/jobs/field-intent",
        profile_ref=str(tmp_path / "profile.json")))
    claimed = queue.claim("synthetic-worker", lease_seconds=10)
    attempt = queue.begin_run_attempt(task["task_id"], claimed["owner"])
    return queue, task["task_id"], claimed["owner"], attempt, ticks


def test_interrupted_field_survives_restart_and_refuses_run_return_and_replay(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    digest = hashlib.sha256(b"synthetic-control").hexdigest()
    action = queue.begin_field_action(attempt, digest)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert rebuilt.field_actions(tid)[0]["action_id"] == action
    assert rebuilt.field_actions(tid)[0]["outcome"] == "ATTEMPTED"
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"other").hexdigest())
    clock[0] += 11
    assert rebuilt.claim("replacement-worker") is None
    assert rebuilt.get(tid)["blocker"] == "unknown_outcome"
    assert rebuilt.run_attempts(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
    assert rebuilt.field_actions(tid)[0]["outcome"] == "ATTEMPTED"
    with pytest.raises(RuntimeError, match="lease lost"):
        rebuilt.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    rebuilt.finish_field_action(action, "UNKNOWN_OUTCOME")
    assert rebuilt.field_actions(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"


def test_field_readback_cannot_authorize_repeat_or_server_certification(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    digest = hashlib.sha256(b"same-control").hexdigest()
    action = queue.begin_field_action(attempt, digest)
    for unsupported in ("SERVER_VERIFIED", "READY_TO_SUBMIT", "RETURNED_UNVERIFIED"):
        with pytest.raises(ValueError):
            queue.finish_field_action(action, unsupported)
    queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.begin_field_action(attempt, digest)
    with pytest.raises(RuntimeError, match="conflict"):
        queue.finish_field_action(action, "UNKNOWN_OUTCOME")
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert queue.field_actions(tid)[0]["outcome"] == "DOM_READBACK_UNVERIFIED"
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"


@pytest.mark.parametrize("digest", [None, True, "PRIVATE_FIELD_CANARY", "f" * 63, "F" * 64])
def test_arbitrary_control_text_never_enters_field_journal(tmp_path, digest):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    with pytest.raises(ValueError):
        queue.begin_field_action(attempt, digest)
    assert queue.field_actions(tid) == []


def _audit(queue, tid, owner, attempt):
    def guard():
        task = queue.get(tid)
        if task["owner"] != owner or task["lease_until"] <= queue.clock():
            raise RuntimeError("lease lost")
    audit = OperationalAudit(queue.root, tid, lambda *_args, **_kwargs: None, guard)
    audit.bind_run_attempt(queue, attempt)
    return audit


def test_real_field_intent_is_committed_before_input_and_unknown_stops_next_field(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "fault.html"
    html.write_text("""<!doctype html><body>
      <label>Private label<input id="first" oninput="this.value=''"></label>
      <label>Other label<input id="second"></label>
      <button type="button" onclick="window.submits=(window.submits||0)+1">Submit application</button>
    """, encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        intents = []
        def before(field_id, selector, *, document_epoch):
            assert adapter.page.locator(selector).input_value() == ""
            action = audit.begin_field_action(field_id, selector, document_epoch=document_epoch)
            fresh = TaskQueue(queue.root, clock=lambda: clock[0])
            assert fresh.field_actions(tid)[-1]["outcome"] == "ATTEMPTED"
            intents.append(action)
            return action
        adapter.field_action_begin = before
        adapter.field_action_finish = audit.finish_field_action
        fields = [FieldResolution(field_id="PRIVATE_CONTROL_CANARY_" + key,
            selector="#" + key, label="PRIVATE_LABEL_CANARY", value="PRIVATE_VALUE_CANARY",
            status=ResolutionStatus.RESOLVED) for key in ("first", "second")]
        with pytest.raises(BrowserOwnershipError, match="outcome unknown"):
            adapter.apply_resolutions(fields)
        assert len(intents) == 1
        assert adapter.page.locator("#second").input_value() == ""
        assert adapter.page.evaluate("window.submits||0") == 0
    assert queue.field_actions(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.require_field_action_readbacks(attempt)
    for path in queue.root.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            for canary in (b"PRIVATE_CONTROL_CANARY", b"PRIVATE_LABEL_CANARY", b"PRIVATE_VALUE_CANARY"):
                assert canary not in payload


def test_production_application_wires_daemon_audit_without_claiming_saved_draft(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "application.html"
    html.write_text("""<!doctype html><body>
      <label>姓名<input id="name" name="full_name" required></label>
      <button type="button">Submit application</button>
    """, encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.full_name": {
        "value": "PRIVATE_VALUE_CANARY", "confidence": 1.0, "user_confirmed": True}}}))
    audit = _audit(queue, tid, owner, attempt)
    runner = ApplicationExecutor(html.as_uri(), profile,
        {"deepseek": {"enabled": False}}, audit_store=audit)
    plan = runner.run(max_pages=1)
    actions = queue.field_actions(tid)
    assert len(actions) == 1
    assert actions[0]["outcome"] == "DOM_READBACK_UNVERIFIED"
    assert (plan.metadata.get("review_certificate") or {}).get("status") != "PASS"
    assert "PRIVATE_VALUE_CANARY" not in json.dumps(actions)
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")


def test_new_real_document_does_not_inherit_prior_same_selector_intent(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    pages = []
    for number in (1, 2):
        html = tmp_path / f"page-{number}.html"
        html.write_text("<!doctype html><body><label>姓名<input id='name'></label>",
                        encoding="utf-8")
        pages.append(html)
    audit = _audit(queue, tid, owner, attempt)
    field = FieldResolution(field_id="name", selector="#name", label="姓名",
        value="Synthetic", status=ResolutionStatus.RESOLVED)
    with GenericWebAdapter(pages[0].as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        assert adapter.apply_resolutions([field])[0]["ok"] is True
        adapter.page.goto(pages[1].as_uri())
        assert adapter.page.locator("#name").input_value() == ""
        assert adapter.apply_resolutions([field])[0]["ok"] is True
        assert adapter.page.locator("#name").input_value() == "Synthetic"
        # Re-entering the same control in that same document cannot create a
        # second journal-authorized operation, even with a new resolution value.
        field.value = "Do not replay"
        with pytest.raises(BrowserOwnershipError):
            adapter.apply_resolutions([field])
        assert adapter.page.locator("#name").input_value() == "Synthetic"
    actions = queue.field_actions(tid)
    assert len(actions) == 2
    assert len({row["field_sha256"] for row in actions}) == 2
    assert {row["outcome"] for row in actions} == {"DOM_READBACK_UNVERIFIED"}


def _blocked_field(queue, tid, attempt, clock, *, outcome="ATTEMPTED"):
    action = queue.begin_field_action(attempt, hashlib.sha256(b"PRIVATE_FIELD_REFERENCE").hexdigest())
    if outcome != "ATTEMPTED":
        queue.finish_field_action(action, outcome)
    clock[0] += 11
    assert queue.claim("replacement-worker") is None
    assert queue.get(tid)["blocker"] == "unknown_outcome"
    return action


@pytest.mark.parametrize("outcome", ["ATTEMPTED", "DOM_READBACK_UNVERIFIED", "UNKNOWN_OUTCOME"])
def test_field_recovery_snapshot_preserves_restart_uncertainty_and_never_authorizes_replay(tmp_path, outcome):
    queue, tid, _owner, attempt, clock = _owned(tmp_path)
    _blocked_field(queue, tid, attempt, clock, outcome=outcome)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    before = (rebuilt.get(tid), rebuilt.run_attempts(tid), rebuilt.field_actions(tid), rebuilt.recent_events(100))
    snapshot = rebuilt.recovery_snapshot(tid, expected_revision=rebuilt.get(tid)["revision"])
    assert snapshot["field_actions"][outcome.lower()] == 1
    assert snapshot["field_actions"]["total"] == 1
    assert snapshot["run_attempts"]["unknown_outcome"] == 1
    assert snapshot["binding"] is None
    assert "PRIVATE_FIELD_REFERENCE" not in json.dumps(snapshot)
    assert before == (rebuilt.get(tid), rebuilt.run_attempts(tid), rebuilt.field_actions(tid), rebuilt.recent_events(100))
    with pytest.raises(ValueError, match="read-only reconciliation"):
        rebuilt.resume(tid)


def _supervisor(queue):
    from types import SimpleNamespace
    from executor.autonomy.supervisor import Supervisor
    from executor.autonomy.manager import safe_task_view
    return Supervisor(queue, worker=SimpleNamespace(active=False),
        manager=SimpleNamespace(state=lambda: {"tasks": [safe_task_view(task) for task in queue.tasks()]}),
        token="synthetic-private-service-token-0123456789")


def test_field_recovery_snapshot_rejects_active_cancelled_and_stale_tasks(tmp_path):
    queue, tid, _owner, attempt, clock = _owned(tmp_path)
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.recovery_snapshot(tid)
    _blocked_field(queue, tid, attempt, clock)
    revision = queue.get(tid)["revision"]
    for invalid in (True, -1, "1"):
        with pytest.raises(ValueError, match="revision"):
            queue.recovery_snapshot(tid, expected_revision=invalid)
    with pytest.raises(ValueError, match="changed"):
        queue.recovery_snapshot(tid, expected_revision=revision - 1)
    queue.cancel(tid)
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.recovery_snapshot(tid, expected_revision=revision)


@pytest.mark.parametrize("mutation", ["cancel", "late_field_outcome", "binding"])
def test_readonly_field_recovery_refuses_mixed_snapshot_during_browser_observation(tmp_path, monkeypatch, mutation):
    from executor import browser
    queue, tid, _owner, attempt, clock = _owned(tmp_path)
    action = _blocked_field(queue, tid, attempt, clock)
    supervisor = _supervisor(queue)
    def observe(*_args):
        if mutation == "cancel":
            queue.cancel(tid)
        elif mutation == "late_field_outcome":
            queue.finish_field_action(action, "UNKNOWN_OUTCOME")
        else:
            with queue.tx() as db:
                db.execute("INSERT INTO browser_bindings VALUES(?,?,?,?,?)",
                    (tid, "epoch12345", "target12345", clock[0], "100.0"))
        return {"status": "BOUND_DOCUMENT_OBSERVED", "replay_allowed": False}
    monkeypatch.setattr(browser, "observe_bound_draft", observe)
    with pytest.raises(ValueError):
        supervisor.observe_task(tid)
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"
    if mutation != "cancel":
        with pytest.raises(ValueError, match="read-only reconciliation"):
            queue.resume(tid)


def test_readonly_field_recovery_never_echoes_unknown_journal_payload_or_claims_server_proof(tmp_path, monkeypatch):
    from executor import browser
    queue, tid, _owner, attempt, clock = _owned(tmp_path)
    action = _blocked_field(queue, tid, attempt, clock)
    with queue.tx() as db:
        db.execute("UPDATE field_actions SET outcome=? WHERE action_id=?",
                   ("PRIVATE_JOURNAL_CANARY", action))
    monkeypatch.setattr(browser, "observe_bound_draft",
        lambda *_args: {"status": "NO_TASK_BINDING", "replay_allowed": False})
    result = _supervisor(queue).observe_task(tid)
    assert result["field_actions"]["unrecognized"] == 1
    assert result["field_actions"]["total"] == 1
    assert result["journal_scope"] == "recorded_local_intents_only"
    for key in ("server_persistence_verified", "draft_identity_verified", "replay_allowed", "submit_capability"):
        assert result[key] is False
    assert "PRIVATE_JOURNAL_CANARY" not in json.dumps(result)
    assert "binding" not in result
    assert "field_sha256" not in json.dumps(result)


def _recovery_server(queue, monkeypatch):
    from contextlib import contextmanager
    from threading import Thread
    from executor import browser
    from executor.autonomy.supervisor import create_server
    @contextmanager
    def running():
        supervisor = _supervisor(queue)
        monkeypatch.setattr(supervisor, "readiness", lambda: {"ready_for_live_e2e": False, "checks": {}})
        observed = []
        monkeypatch.setattr(browser, "observe_bound_draft",
            lambda *_args: observed.append(True) or {"status": "NO_TASK_BINDING", "replay_allowed": False})
        server = create_server(supervisor, port=0)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield supervisor, server.server_address[1], observed
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    return running()


def test_real_authenticated_field_recovery_http_keeps_journal_and_denies_wrong_client(tmp_path, monkeypatch):
    from http.client import HTTPConnection
    queue, tid, _owner, attempt, clock = _owned(tmp_path)
    _blocked_field(queue, tid, attempt, clock, outcome="DOM_READBACK_UNVERIFIED")
    before = (queue.get(tid), queue.run_attempts(tid), queue.field_actions(tid), queue.recent_events(100))
    with _recovery_server(queue, monkeypatch) as (supervisor, port, observed):
        client = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            client.request("GET", "/ui/api/observe?task_id=" + tid)
            response = client.getresponse();assert response.status == 401;response.read();assert observed == []
            client.request("GET", "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            response = client.getresponse();assert response.status == 303
            cookie = response.getheader("Set-Cookie");assert "HttpOnly" in cookie;response.read()
            headers = {"Cookie": cookie.split(";", 1)[0]}
            # The real dashboard requires a functioning state response before
            # an observation button can exist. Keep the fixture worker contract
            # complete and make a missing/failed state response fail directly.
            client.request("GET", "/ui/api/state", headers=headers)
            response = client.getresponse();assert response.status == 200
            state = json.loads(response.read())
            assert state["worker_active"] is False
            assert len(state["tasks"]) == 1
            assert state["tasks"][0]["task_id"] == tid
            assert state["tasks"][0]["stage"] == "BLOCKED"
            assert state["tasks"][0]["blocker"] == "unknown_outcome"
            client.request("GET", "/ui/api/observe?task_id=" + tid, headers=headers)
            response = client.getresponse();assert response.status == 200;result = json.loads(response.read())
            assert result["field_actions"]["dom_readback_unverified"] == 1
            assert result["revision"] == queue.get(tid)["revision"]
            assert result["server_persistence_verified"] is False
            assert result["draft_identity_verified"] is False
            assert result["replay_allowed"] is False
            assert result["submit_capability"] is False
            assert len(observed) == 1
            client.request("GET", "/ui/api/observe?task_id=" + tid,
                headers={**headers, "Origin": "https://outside.invalid"})
            response = client.getresponse();assert response.status == 403;response.read();assert len(observed) == 1
        finally:
            client.close()
    assert before == (queue.get(tid), queue.run_attempts(tid), queue.field_actions(tid), queue.recent_events(100))


def test_real_field_recovery_consumer_ui_is_read_only_revision_bound_and_clears_on_expiry(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    queue, tid, _owner, attempt, clock = _owned(tmp_path)
    _blocked_field(queue, tid, attempt, clock)
    with _recovery_server(queue, monkeypatch) as (supervisor, port, observed):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            requests = []
            page.on("request", lambda request: requests.append((request.method, request.url)))
            try:
                page.goto(f"http://127.0.0.1:{port}/ui-login?ticket=" + supervisor.issue_ui_ticket())
                page.get_by_role("button", name="只读核对", exact=True).click()
                report = page.locator("[data-field-recovery]")
                expect(report).to_be_visible()
                assert "1 项中断后结果未知" in report.inner_text()
                assert "服务端保存和草稿身份仍未证明" in report.inner_text()
                assert "不会自动重放" in report.inner_text()
                assert page.get_by_role("button", name="继续", exact=True).count() == 0
                assert all(method == "GET" for method, _url in requests)
                assert len(observed) == 1
                queue.pause(tid)
                page.evaluate("state()")
                expect(report).to_have_count(0)
                page.get_by_role("button", name="只读核对", exact=True).click()
                expect(report).to_be_visible()
                page.locator("#message").fill("UNSENT_RECOVERY_NOTE")
                with supervisor._ui_lock:
                    supervisor._ui_sessions.clear()
                page.evaluate("state()")
                expect(page.locator("#session-expired")).to_be_visible()
                expect(report).to_have_count(0)
                assert page.locator("#message").input_value() == "UNSENT_RECOVERY_NOTE"
                assert page.evaluate("recoveryObservations.size") == 0
                assert page.get_by_role("button", name="继续", exact=True).count() == 0
                assert all(method == "GET" for method, _url in requests)
                assert queue.get(tid)["stage"] == "BLOCKED"
                assert queue.run_attempts(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
                with pytest.raises(ValueError, match="read-only reconciliation"):
                    queue.resume(tid)
            finally:
                browser.close()
