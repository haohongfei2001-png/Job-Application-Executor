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


@pytest.mark.parametrize("component", ["native", "aria"])
def test_unavailable_choice_retains_unknown_journal_and_stops_later_write(tmp_path, component):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    if component == "native":
        body = """<select id='city'><option value=''>Choose</option>
          <option value='city' disabled>PRIVATE_VALUE_CANARY</option></select>"""
    else:
        body = """<div id='city' role='combobox' aria-controls='cities'
          tabindex='0' onclick="window.opens++;document.querySelector('#cities').hidden=false">Choose</div>
          <div id='cities' role='listbox' hidden>
            <div role='option' aria-disabled='true' onclick="window.choices++;
              document.querySelector('#city').innerText=this.innerText">PRIVATE_VALUE_CANARY</div>
          </div>"""
    html = tmp_path / "disabled-owned-choice.html"
    html.write_text("<!doctype html><body>" + body + """<input id='later'>
      <button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.opens=0;window.choices=0;window.submits=0;
        document.querySelector('#city').addEventListener('change',()=>window.choices++);
      </script>""", encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        resolutions = [FieldResolution(
            field_id="PRIVATE_CONTROL_CANARY_" + key, selector="#" + key,
            label="PRIVATE_LABEL_CANARY", value="PRIVATE_VALUE_CANARY",
            status=ResolutionStatus.RESOLVED) for key in ("city", "later")]
        with pytest.raises(BrowserOwnershipError, match="outcome unknown"):
            adapter.apply_resolutions(resolutions)
        assert adapter.page.locator("#later").input_value() == ""
        assert adapter.page.evaluate("[window.choices,window.submits]") == [0, 0]
        assert adapter.page.evaluate("window.opens") == (1 if component == "aria" else 0)
        if component == "native":
            assert adapter.page.locator("#city").input_value() == ""
        else:
            assert adapter.page.locator("#city").inner_text() == "Choose"
        # A refused option is not a server-save receipt or authority to replay
        # an already journaled action (opening the ARIA component may be an effect).
        with pytest.raises(BrowserOwnershipError):
            adapter.apply_resolutions(resolutions)
        assert adapter.page.evaluate("[window.choices,window.submits]") == [0, 0]
        assert adapter.page.evaluate("window.opens") == (1 if component == "aria" else 0)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    actions = rebuilt.field_actions(tid)
    assert len(actions) == 1
    assert actions[0]["outcome"] == "UNKNOWN_OUTCOME"
    assert rebuilt.get(tid)["stage"] != "READY_TO_SUBMIT"
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    for path in queue.root.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            for canary in (b"PRIVATE_CONTROL_CANARY", b"PRIVATE_LABEL_CANARY", b"PRIVATE_VALUE_CANARY"):
                assert canary not in payload


def _retained_controls(kind):
    if kind == "checked":
        return "<label>Private first<input id='first' type='checkbox'></label>", True, "e.checked=false"
    if kind == "select":
        return ("<label>Private first<select id='first'><option value=''>Choose</option>"
                "<option value='kept'>PRIVATE_VALUE_CANARY</option></select></label>",
                "PRIVATE_VALUE_CANARY", "e.selectedIndex=0")
    if kind == "aria":
        return ("""<div id='first' role='combobox' aria-controls='choices' tabindex='0'>Choose</div>
          <div id='choices' role='listbox'><div role='option' onclick="
            document.querySelector('#first').innerText=this.innerText">PRIVATE_VALUE_CANARY</div></div>""",
                "PRIVATE_VALUE_CANARY", "e.innerText='changed'")
    return "<label>Private first<input id='first'></label>", "PRIVATE_VALUE_CANARY", "e.value='changed'"


@pytest.mark.parametrize("kind", ["text", "checked", "select", "aria"])
@pytest.mark.parametrize("change", ["value", "identity", "removed", "duplicate"])
def test_later_field_collateral_change_revokes_complete_batch_before_next_write(tmp_path, kind, change):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    first, value, value_change = _retained_controls(kind)
    mutation = {
        "value": value_change,
        "identity": "e.setAttribute('aria-label','changed identity')",
        "removed": "e.remove()",
        "duplicate": "e.after(e.cloneNode(true))",
    }[change]
    html = tmp_path / "collateral.html"
    html.write_text("<!doctype html><body>" + first + """<input id='second'>
      <input id='third'><button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.submits=0;window.writes=0;
        document.querySelector('#second').addEventListener('input',()=>{
          window.writes++;const e=document.querySelector('#first');""" + mutation + """});
      </script>""", encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        resolutions = [FieldResolution(field_id="PRIVATE_CONTROL_CANARY_" + key,
            selector="#" + key, label="PRIVATE_LABEL_CANARY",
            value=value if key == "first" else "PRIVATE_VALUE_CANARY",
            status=ResolutionStatus.RESOLVED) for key in ("first", "second", "third")]
        with pytest.raises(BrowserOwnershipError, match="outcome unknown"):
            adapter.apply_resolutions(resolutions)
        assert adapter.page.locator("#second").input_value() == "PRIVATE_VALUE_CANARY"
        assert adapter.page.locator("#third").input_value() == ""
        assert adapter.page.evaluate("[window.writes,window.submits]") == [1, 0]
        with pytest.raises(BrowserOwnershipError):
            adapter.apply_resolutions(resolutions)
        assert adapter.page.evaluate("[window.writes,window.submits]") == [1, 0]
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert [row["outcome"] for row in rebuilt.field_actions(tid)] == ["UNKNOWN_OUTCOME"] * 2
    for operation in (lambda: rebuilt.require_field_action_readbacks(attempt),
                      lambda: rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")):
        with pytest.raises(RuntimeError, match="reconciliation"):
            operation()
    clock[0] += 11
    assert rebuilt.claim("replacement-worker") is None
    assert rebuilt.get(tid)["blocker"] == "unknown_outcome"
    snapshot = rebuilt.recovery_snapshot(tid)
    assert snapshot["field_actions"]["unknown_outcome"] == 2
    with pytest.raises(ValueError, match="read-only reconciliation"):
        rebuilt.resume(tid)
    for path in queue.root.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            for canary in (b"PRIVATE_CONTROL_CANARY", b"PRIVATE_LABEL_CANARY", b"PRIVATE_VALUE_CANARY"):
                assert canary not in payload


@pytest.mark.parametrize("kind", ["text", "checked", "select", "aria"])
def test_same_contract_reactive_replacement_keeps_whole_batch_dom_readback(tmp_path, kind):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    first, value, _mutation = _retained_controls(kind)
    html = tmp_path / "retained-redraw.html"
    html.write_text("<!doctype html><body>" + first + """<input id='second'>
      <input id='third'><button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.submits=0;
        document.querySelector('#second').addEventListener('input',()=>{
          const e=document.querySelector('#first'), clone=e.cloneNode(true);
          clone.value=e.value;clone.checked=e.checked;
          if(e.tagName==='SELECT')clone.selectedIndex=e.selectedIndex;
          e.replaceWith(clone);
        });
      </script>""", encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        resolutions = [FieldResolution(field_id=key, selector="#" + key,
            label="Private label", value=value if key == "first" else "PRIVATE_VALUE_CANARY",
            status=ResolutionStatus.RESOLVED) for key in ("first", "second", "third")]
        actions = adapter.apply_resolutions(resolutions)
        assert len(actions) == 3 and all(action["ok"] for action in actions)
        assert adapter.page.locator("#third").input_value() == "PRIVATE_VALUE_CANARY"
        assert adapter.page.evaluate("window.submits") == 0
    assert [row["outcome"] for row in queue.field_actions(tid)] == ["DOM_READBACK_UNVERIFIED"] * 3
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"


def test_between_actions_change_revokes_old_readback_before_new_intent(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "between-actions.html"
    html.write_text("<!doctype html><body><input id='first'><input id='second'>", encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        def finish(action_id, outcome):
            audit.finish_field_action(action_id, outcome)
            if outcome == "DOM_READBACK_UNVERIFIED":
                adapter.page.locator("#first").evaluate("e => e.value = 'changed after observation'")
        adapter.field_action_finish = finish
        fields = [FieldResolution(field_id=key, selector="#" + key, label="Private",
            value="PRIVATE_VALUE_CANARY", status=ResolutionStatus.RESOLVED)
            for key in ("first", "second")]
        with pytest.raises(BrowserOwnershipError, match="retained field outcome unknown"):
            adapter.apply_resolutions(fields)
        assert adapter.page.locator("#second").input_value() == ""
    assert len(queue.field_actions(tid)) == 1
    assert queue.field_actions(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.require_field_action_readbacks(attempt)


@pytest.mark.parametrize("fence", ["expired", "other_owner", "returned"])
def test_readback_invalidation_cannot_bypass_run_or_lease_fence(tmp_path, fence):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    action = queue.begin_field_action(attempt, hashlib.sha256(b"first").hexdigest())
    queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    if fence == "expired":
        clock[0] += 11
    elif fence == "other_owner":
        with queue.tx() as db:
            db.execute("UPDATE tasks SET owner=? WHERE task_id=?", ("other", tid))
    else:
        queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    before = queue.field_actions(tid)
    with pytest.raises(RuntimeError, match="invalidation lease lost"):
        queue.invalidate_field_readbacks(attempt)
    assert queue.field_actions(tid) == before


def test_readback_invalidation_is_atomic_idempotent_and_never_grants_replay(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    for key in ("first", "second"):
        action = queue.begin_field_action(attempt, hashlib.sha256(key.encode()).hexdigest())
        queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    current = queue.begin_field_action(attempt, hashlib.sha256(b"third").hexdigest())
    queue.invalidate_field_readbacks(attempt)
    queue.invalidate_field_readbacks(attempt)
    states = {row["action_id"]: row["outcome"] for row in queue.field_actions(tid)}
    assert states.pop(current) == "ATTEMPTED"
    assert len(states) == 2 and set(states.values()) == {"UNKNOWN_OUTCOME"}
    queue.finish_field_action(current, "UNKNOWN_OUTCOME")
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert {row["outcome"] for row in rebuilt.field_actions(tid)} == {"UNKNOWN_OUTCOME"}
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"fourth").hexdigest())


@pytest.mark.parametrize("current_failure", ["value", "identity"])
def test_current_readback_failure_also_revokes_collateral_old_evidence(tmp_path, current_failure):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    mutation = "this.value=''" if current_failure == "value" else "this.setAttribute('aria-label','changed')"
    html = tmp_path / "current-and-retained-fault.html"
    html.write_text("<!doctype html><body><input id='first'><input id='second' oninput=\""
        "document.querySelector('#first').value='changed';" + mutation
        + "\"><input id='third'>", encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        fields = [FieldResolution(field_id=key, selector="#" + key, label="Private",
            value="PRIVATE_VALUE_CANARY", status=ResolutionStatus.RESOLVED)
            for key in ("first", "second", "third")]
        with pytest.raises(BrowserOwnershipError, match="outcome unknown"):
            adapter.apply_resolutions(fields)
        assert adapter.page.locator("#third").input_value() == ""
    assert len(queue.field_actions(tid)) == 2
    assert {row["outcome"] for row in queue.field_actions(tid)} == {"UNKNOWN_OUTCOME"}
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.require_field_action_readbacks(attempt)


def test_final_batch_observation_cannot_return_stale_last_control_proof(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "last-action-fault.html"
    html.write_text("<!doctype html><body><input id='first'>", encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        def finish(action_id, outcome):
            audit.finish_field_action(action_id, outcome)
            adapter.page.locator("#first").evaluate("e => e.value = 'changed before return'")
        adapter.field_action_finish = finish
        field = FieldResolution(field_id="first", selector="#first", label="Private",
            value="PRIVATE_VALUE_CANARY", status=ResolutionStatus.RESOLVED)
        with pytest.raises(BrowserOwnershipError, match="retained field outcome unknown"):
            adapter.apply_resolutions([field])
    assert len(queue.field_actions(tid)) == 1
    assert queue.field_actions(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")

@pytest.mark.parametrize("component", ["iframe", "shadow"])
def test_reactive_opaque_form_stops_next_write_and_revokes_prior_dom_readback(
        tmp_path, component):
    queue, tid, owner, attempt, _clock = _owned(tmp_path)
    html = tmp_path / "reactive-opaque.html"
    html.write_text("""<!doctype html><meta charset='utf-8'><body>
      <label>First<input id='first'></label><label>Second<input id='second'></label>
      <button type='button' onclick='window.submits=(window.submits||0)+1'>Submit application</button>
      <script>
        document.getElementById('first').addEventListener('input', () => {
          if (document.getElementById('opaque')) return;
          const kind = '__COMPONENT__';
          const host = document.createElement(kind === 'iframe' ? 'iframe' : 'opaque-form');
          host.id = 'opaque';
          if (kind === 'iframe') host.srcdoc = '<input required aria-label="Nested form">';
          else host.attachShadow({mode:'open'}).innerHTML =
            '<input required aria-label="Shadow form">';
          document.body.append(host);
        }, {once:true});
      </script>
    """.replace("__COMPONENT__", component), encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        fields = [FieldResolution(
            field_id=name, selector="#" + name, label=name,
            value="Synthetic value", status=ResolutionStatus.RESOLVED)
            for name in ("first", "second")]
        with pytest.raises(BrowserOwnershipError, match="form structure outcome unknown"):
            adapter.apply_resolutions(fields)
        assert adapter.page.locator("#first").input_value() == "Synthetic value"
        assert adapter.page.locator("#second").input_value() == ""
        assert adapter.page.evaluate("window.submits||0") == 0
    actions = queue.field_actions(tid)
    assert len(actions) == 1
    assert actions[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.require_field_action_readbacks(attempt)

@pytest.mark.parametrize("component", ["iframe", "shadow"])
def test_final_reactive_opaque_form_revokes_last_dom_readback_before_return(
        tmp_path, component):
    queue, tid, owner, attempt, _clock = _owned(tmp_path)
    html = tmp_path / "final-reactive-opaque.html"
    html.write_text("""<!doctype html><meta charset='utf-8'><body>
      <label>Only field<input id='only'></label>
      <button type='button' onclick='window.submits=(window.submits||0)+1'>Submit application</button>
      <script>
        document.getElementById('only').addEventListener('input', () => {
          const kind = '__COMPONENT__';
          const host = document.createElement(kind === 'iframe' ? 'iframe' : 'opaque-form');
          host.id = 'opaque';
          if (kind === 'iframe') host.srcdoc = '<input required aria-label="Nested form">';
          else host.attachShadow({mode:'open'}).innerHTML =
            '<input required aria-label="Shadow form">';
          document.body.append(host);
        }, {once:true});
      </script>
    """.replace("__COMPONENT__", component), encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        field = FieldResolution(
            field_id="only", selector="#only", label="Only field",
            value="Synthetic value", status=ResolutionStatus.RESOLVED)
        with pytest.raises(BrowserOwnershipError, match="form structure outcome unknown"):
            adapter.apply_resolutions([field])
        assert adapter.page.locator("#only").input_value() == "Synthetic value"
        assert adapter.page.locator("#opaque").count() == 1
        assert adapter.page.evaluate("window.submits||0") == 0
        with pytest.raises(BrowserOwnershipError):
            adapter.apply_resolutions([field])
        assert adapter.page.evaluate("window.submits||0") == 0
    actions = queue.field_actions(tid)
    assert len(actions) == 1
    assert actions[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.require_field_action_readbacks(attempt)

@pytest.mark.parametrize("redraw", [
    "control_limit", "option_limit", "ambiguous_selector", "ambiguous_row",
])
@pytest.mark.parametrize("following", [False, True])
def test_reactive_incomplete_or_ambiguous_form_revokes_complete_batch(
        tmp_path, redraw, following):
    queue, tid, owner, attempt, _clock = _owned(tmp_path)
    html = tmp_path / "reactive-structural-floor.html"
    html.write_text("""<!doctype html><meta charset='utf-8'><body>
      <label>First<input id='first'></label>
      <label>Second<input id='second'></label>
      <button type='button' onclick='window.submits=(window.submits||0)+1'>Submit application</button>
      <script>
        document.getElementById('first').addEventListener('input', () => {
          const redraw = '__REDRAW__';
          if (redraw === 'control_limit') {
            for (let i=0; i<351; i++) {
              const input=document.createElement('input');
              input.id='extra-'+i; document.body.append(input);
            }
          } else if (redraw === 'option_limit') {
            const select=document.createElement('select'); select.id='extra-select';
            for (let i=0; i<201; i++) select.add(new Option('Choice '+i, String(i)));
            document.body.append(select);
          } else if (redraw === 'ambiguous_selector') {
            for (let i=0; i<2; i++) {
              const input=document.createElement('input');
              input.id='duplicate-extra'; document.body.append(input);
            }
          } else {
            for (let i=0; i<2; i++) {
              const row=document.createElement('div'); row.dataset.rowId='same-row';
              const input=document.createElement('input'); input.id='row-extra-'+i;
              row.append(input); document.body.append(row);
            }
          }
        }, {once:true});
      </script>
    """.replace("__REDRAW__", redraw), encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        fields = [FieldResolution(
            field_id=name, selector="#" + name, label=name,
            value="PRIVATE_VALUE_CANARY", status=ResolutionStatus.RESOLVED)
            for name in (("first", "second") if following else ("first",))]
        with pytest.raises(BrowserOwnershipError, match="form structure outcome unknown"):
            adapter.apply_resolutions(fields)
        assert adapter.page.locator("#first").input_value() == "PRIVATE_VALUE_CANARY"
        assert adapter.page.locator("#second").input_value() == ""
        observed = adapter.observe_form()
        if redraw.endswith("limit"):
            assert observed.collection_complete is False
            if redraw == "control_limit":
                assert adapter.page.locator("input").count() == 353
            else:
                assert adapter.page.locator("#extra-select option").count() == 201
        elif redraw == "ambiguous_selector":
            assert observed.ambiguous_selector_count == 1
        else:
            assert observed.ambiguous_row_count == 1
        assert adapter.page.evaluate("window.submits||0") == 0
    actions = queue.field_actions(tid)
    assert len(actions) == 1 and actions[0]["outcome"] == "UNKNOWN_OUTCOME"
    rebuilt = TaskQueue(queue.root, clock=lambda: _clock[0])
    assert rebuilt.field_actions(tid) == actions
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"another-write").hexdigest())
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert rebuilt.get(tid)["stage"] != "READY_TO_SUBMIT"


@pytest.mark.parametrize("drift", [
    "label", "labelledby", "aria_label", "placeholder", "name",
    "type", "required", "dependency", "row",
])
def test_reactive_next_question_drift_revokes_prior_proof_before_next_intent(
        tmp_path, drift):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    private_value = "PRIVATE_CONTRACT_VALUE_" + "招聘🔒" * 256
    mutations = {
        "label": "document.querySelector('#target-label-text').textContent='Different question'",
        "labelledby": "document.querySelector('#linked-prompt').textContent='Different linked question'",
        "aria_label": "e.setAttribute('aria-label','Different question')",
        "placeholder": "e.setAttribute('placeholder','Different question')",
        "name": "e.setAttribute('name','different_question')",
        "type": "e.setAttribute('type','password')",
        "required": "e.required=true",
        "dependency": "e.setAttribute('data-depends-on','#first')",
        "row": "e.closest('[data-row-id]').dataset.rowId='different-record'",
    }
    html = tmp_path / "reactive-question-drift.html"
    html.write_text("""<!doctype html><meta charset='utf-8'><body>
      <label>First<input id='first'></label>
      <div data-row-id='PRIVATE_ROW_CONTRACT_CANARY'>
        <label><span id='target-label-text'>Original question</span>
          <input id='target' name='original_question' placeholder='Original placeholder'
                 aria-labelledby='linked-prompt'></label>
      </div>
      <span id='linked-prompt'>Original linked question</span>
      <button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.targetWrites=0;window.submits=0;
        document.querySelector('#target').oninput=()=>window.targetWrites++;
        document.querySelector('#first').addEventListener('input',()=>{
          const e=document.querySelector('#target'); __MUTATION__;
        },{once:true});
      </script>
    """.replace("__MUTATION__", mutations[drift]), encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        before = {field.selector: field for field in adapter.discover_fields()}
        digest = before["#target"].metadata["control_contract"]
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        assert "PRIVATE_ROW_CONTRACT_CANARY" not in json.dumps(before["#target"].metadata)
        fields = [FieldResolution(field_id=key, selector="#" + key,
            label="Original question", value=private_value,
            status=ResolutionStatus.RESOLVED) for key in ("first", "target")]
        with pytest.raises(BrowserOwnershipError, match="field contract outcome unknown") as caught:
            adapter.apply_resolutions(fields)
        after = {field.selector: field for field in adapter.discover_fields()}
        assert after["#target"].metadata["control_contract"] != digest
        assert adapter.page.locator("#first").input_value() == private_value
        assert adapter.page.locator("#target").input_value() == ""
        assert adapter.page.evaluate("[window.targetWrites, window.submits]") == [0, 0]
        assert "PRIVATE_" not in str(caught.value)
    actions = queue.field_actions(tid)
    assert len(actions) == 1 and actions[0]["outcome"] == "UNKNOWN_OUTCOME"
    assert private_value not in json.dumps(actions)
    assert "PRIVATE_ROW_CONTRACT_CANARY" not in json.dumps(actions)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert rebuilt.field_actions(tid) == actions
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"refused-next").hexdigest())
    assert rebuilt.get(tid)["stage"] != "READY_TO_SUBMIT"


@pytest.mark.parametrize("kind", ["text", "checked", "select", "aria"])
def test_question_drift_at_primitive_ownership_fence_never_dispatches_target(
        tmp_path, kind):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    controls = {
        "text": "<input id='target' oninput='window.targetWrites++'>",
        "checked": "<input id='target' type='checkbox' onclick='window.targetWrites++'>",
        "select": ("<select id='target' onchange='window.targetWrites++'>"
                   "<option value=''>Choose</option><option value='choice'>Exact choice</option></select>"),
        "aria": ("<div id='target' role='combobox' aria-label='Target' aria-controls='choices' tabindex='0' "
                 "onclick=\"window.opens++;document.querySelector('#choices').hidden=false\">Choose</div>"
                 "<div id='choices' role='listbox' hidden><button id='choice' type='button' role='option' "
                 "onclick=\"window.targetWrites++;document.querySelector('#target').innerText=this.innerText\">"
                 "Exact choice</button></div>"),
    }
    html = tmp_path / "primitive-question-drift.html"
    html.write_text("<!doctype html><body><label>First<input id='first'></label>"
        "<label for='target'>Target</label>" + controls[kind] +
        "<button type='button' onclick='window.submits++'>Submit application</button>"
        "<script>window.targetWrites=0;window.submits=0;window.opens=0;</script>",
        encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    guards_after_intent = []
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        def guard():
            rows = queue.field_actions(tid)
            # created timestamps intentionally tie under the synthetic clock;
            # SQL orders ties by random action_id, not admission chronology.
            if len(rows) != 2 or sum(row["outcome"] == "ATTEMPTED" for row in rows) != 1:
                return
            guards_after_intent.append(True)
            # Native select has an outer guard then the exact option guard.
            # ARIA must open its known list before the option guard changes it.
            if kind == "select" and len(guards_after_intent) < 2:
                return
            if kind == "aria" and not adapter.page.locator("#choices").is_visible():
                return
            adapter.page.locator("#target").evaluate(
                "e=>e.setAttribute('name','PRIVATE_CHANGED_QUESTION_CANARY')")
        adapter.mutation_guard = guard
        value = True if kind == "checked" else ("Exact choice" if kind in {"select", "aria"}
                                                else "PRIVATE_CONTRACT_VALUE_CANARY")
        fields = [
            FieldResolution(field_id="first", selector="#first", label="First",
                value="PRIVATE_FIRST_VALUE_CANARY", status=ResolutionStatus.RESOLVED),
            FieldResolution(field_id="target", selector="#target", label="Target",
                value=value, status=ResolutionStatus.RESOLVED),
        ]
        with pytest.raises(BrowserOwnershipError, match="field write outcome unknown") as caught:
            adapter.apply_resolutions(fields)
        assert adapter.page.locator("#first").input_value() == "PRIVATE_FIRST_VALUE_CANARY"
        assert adapter.page.locator("#target").get_attribute("name") == "PRIVATE_CHANGED_QUESTION_CANARY"
        assert len(guards_after_intent) == (2 if kind in {"select", "aria"} else 1)
        if kind == "checked":
            assert adapter.page.locator("#target").is_checked() is False
        elif kind == "aria":
            assert adapter.page.locator("#target").inner_text() == "Choose"
            assert adapter.page.evaluate("window.opens") == 1
        else:
            assert adapter.page.locator("#target").input_value() == ""
        assert adapter.page.evaluate("[window.targetWrites,window.submits]") == [0, 0]
        assert "PRIVATE_" not in str(caught.value)
    actions = queue.field_actions(tid)
    assert len(actions) == 2 and {row["outcome"] for row in actions} == {"UNKNOWN_OUTCOME"}
    assert "PRIVATE_" not in json.dumps(actions)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert rebuilt.field_actions(tid) == actions
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"refused-next").hexdigest())
    assert rebuilt.get(tid)["stage"] != "READY_TO_SUBMIT"


@pytest.mark.parametrize("replacement", [False, True])
def test_parent_choice_redraw_retains_original_child_question_contract(
        tmp_path, replacement):
    queue, tid, owner, attempt, _clock = _owned(tmp_path)
    html = tmp_path / "parent-choice-contract.html"
    html.write_text("""<!doctype html><body>
      <label>Child question<select id='child' data-depends-on='#parent' required>
        <option value=''>Choose</option></select></label>
      <label>Parent question<select id='parent' required>
        <option value=''>Choose</option><option value='parent'>Parent choice</option></select></label>
      <button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.submits=0;window.targetWrites=0;
        document.addEventListener('change',event=>{
          if(event.target.id==='child')window.targetWrites++;
        });
        document.querySelector('#parent').onchange=()=>{
          let child=document.querySelector('#child');
          if(__REPLACEMENT__){const clone=child.cloneNode(true);child.replaceWith(clone);child=clone;}
          child.innerHTML='<option value="">Choose</option><option value="child">Child choice</option>';
        };
      </script>
    """.replace("__REPLACEMENT__", "true" if replacement else "false"), encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        before = {field.selector: field for field in adapter.discover_fields()}
        fields = [
            FieldResolution(field_id="child", selector="#child", label="Child question",
                value="Child choice", status=ResolutionStatus.RESOLVED),
            FieldResolution(field_id="parent", selector="#parent", label="Parent question",
                value="Parent choice", status=ResolutionStatus.RESOLVED),
        ]
        actions = adapter.apply_resolutions(fields)
        after = {field.selector: field for field in adapter.discover_fields()}
        assert before["#child"].options != after["#child"].options
        assert before["#child"].metadata["control_contract"] == after["#child"].metadata["control_contract"]
        assert [item["field_id"] for item in actions] == ["parent", "child"]
        assert all(item["ok"] and item["observed"] == "DOM_READBACK" for item in actions)
        assert adapter.page.locator("#parent").input_value() == "parent"
        assert adapter.page.locator("#child").input_value() == "child"
        assert adapter.page.evaluate("[window.targetWrites,window.submits]") == [1, 0]
    assert [row["outcome"] for row in queue.field_actions(tid)] == ["DOM_READBACK_UNVERIFIED"] * 2
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"


@pytest.mark.parametrize("journaled", [False, True])
def test_aria_open_cannot_trigger_a_companion_control_through_native_label(
        tmp_path, journaled):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "aria-implicit-label-activation.html"
    html.write_text("""<!doctype html><body>
      <label>First<input id='first'></label>
      <label>Target
        <div id='target' role='combobox' aria-controls='choices' tabindex='0'
          onclick="window.opens++;document.querySelector('#choices').hidden=false">Choose</div>
        <div id='choices' role='listbox' hidden>
          <button id='choice' type='button' role='option'
            onclick="window.targetWrites++;document.querySelector('#target').innerText=this.innerText">
            Exact choice</button>
        </div>
      </label>
      <button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.opens=0;window.targetWrites=0;window.submits=0;</script>
    """, encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        if journaled:
            adapter.field_action_begin = audit.begin_field_action
            adapter.field_action_finish = audit.finish_field_action
            adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        assert adapter.page.locator("#target").evaluate(
            "e=>e.closest('label').control.id") == "choice"
        fields = [
            FieldResolution(field_id="first", selector="#first", label="First",
                value="PRIVATE_VALUE_CANARY", status=ResolutionStatus.RESOLVED),
            FieldResolution(field_id="target", selector="#target", label="Target",
                value="Exact choice", status=ResolutionStatus.RESOLVED),
        ]
        if journaled:
            with pytest.raises(BrowserOwnershipError, match="field write outcome unknown"):
                adapter.apply_resolutions(fields)
        else:
            actions = adapter.apply_resolutions(fields)
            assert actions[0]["ok"] is True
            assert actions[1] == {"field_id":"target","ok":False,
                                 "reason":"combobox_exact_choice_unavailable"}
        assert adapter.page.locator("#first").input_value() == "PRIVATE_VALUE_CANARY"
        assert adapter.page.locator("#target").inner_text() == "Choose"
        assert adapter.page.locator("#choices").is_visible() is False
        assert adapter.page.evaluate("[window.opens,window.targetWrites,window.submits]") == [0,0,0]
    if journaled:
        actions = queue.field_actions(tid)
        assert len(actions) == 2 and {row["outcome"] for row in actions} == {"UNKNOWN_OUTCOME"}
        rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
        assert rebuilt.field_actions(tid) == actions
        with pytest.raises(RuntimeError, match="reconciliation"):
            rebuilt.require_field_action_readbacks(attempt)
        with pytest.raises(RuntimeError, match="reconciliation"):
            rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
        with pytest.raises(RuntimeError, match="reconciliation"):
            rebuilt.begin_field_action(attempt, hashlib.sha256(b"refused-label-replay").hexdigest())
    else:
        assert queue.field_actions(tid) == []
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"

@pytest.mark.parametrize("phase", ["open", "option"])
def test_reactive_label_companion_at_ownership_boundary_cannot_dispatch(tmp_path, phase):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "reactive-label-ownership.html"
    html.write_text("""<!doctype html><body>
      <label>First<input id='first'></label>
      <label for='target'>Target</label>
      <div id='target' role='combobox' aria-label='Target' aria-controls='choices' tabindex='0'
        onclick="window.opens++;document.querySelector('#choices').hidden=false">Choose</div>
      <div id='choices' role='listbox' hidden>
        <div id='choice' role='option' tabindex='0'
          onclick="window.targetWrites++;document.querySelector('#target').innerText=this.innerText">
          Exact choice</div>
      </div>
      <input id='foreign' type='checkbox' onclick="window.foreignWrites++">
      <button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.opens=0;window.targetWrites=0;window.foreignWrites=0;window.submits=0;</script>
    """, encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    injected = []
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        original = {field.selector: field for field in adapter.discover_fields()}
        def guard():
            rows = queue.field_actions(tid)
            if injected or len(rows) != 2 or sum(row["outcome"] == "ATTEMPTED" for row in rows) != 1:
                return
            opened = adapter.page.locator("#choices").is_visible()
            if opened != (phase == "option"):
                return
            selector = "#target" if phase == "open" else "#choice"
            adapter.page.locator(selector).evaluate("""e=>{
              const label=document.createElement('label');label.htmlFor='foreign';
              e.replaceWith(label);label.append(e);
            }""")
            assert adapter.page.locator(selector).evaluate(
                "e=>e.closest('label').control.id") == "foreign"
            injected.append(True)
        adapter.mutation_guard = guard
        fields = [
            FieldResolution(field_id="first", selector="#first", label="First",
                value="PRIVATE_REACTIVE_LABEL_CANARY", status=ResolutionStatus.RESOLVED),
            FieldResolution(field_id="target", selector="#target", label="Target",
                value="Exact choice", status=ResolutionStatus.RESOLVED),
        ]
        with pytest.raises(BrowserOwnershipError, match="field write outcome unknown") as caught:
            adapter.apply_resolutions(fields)
        assert injected == [True]
        current = {field.selector: field for field in adapter.discover_fields()}
        # The ordinary control contract is unchanged; this owns the separate
        # native activation relation which can change after an ownership check.
        assert current["#target"].metadata["control_contract"] == original["#target"].metadata["control_contract"]
        assert adapter.page.locator("#first").input_value() == "PRIVATE_REACTIVE_LABEL_CANARY"
        assert adapter.page.locator("#target").inner_text() == "Choose"
        assert adapter.page.locator("#foreign").is_checked() is False
        assert adapter.page.locator("#choices").is_visible() == (phase == "option")
        assert adapter.page.evaluate("[window.opens,window.targetWrites,window.foreignWrites,window.submits]") == [
            1 if phase == "option" else 0, 0, 0, 0]
        assert "PRIVATE_" not in str(caught.value)
    actions = queue.field_actions(tid)
    assert len(actions) == 2 and {row["outcome"] for row in actions} == {"UNKNOWN_OUTCOME"}
    assert "PRIVATE_" not in json.dumps(actions)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert rebuilt.field_actions(tid) == actions
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"refused-reactive-label-replay").hexdigest())
    assert rebuilt.get(tid)["stage"] != "READY_TO_SUBMIT"


def test_native_input_combobox_keeps_its_own_label_activation_control(tmp_path):
    queue, tid, owner, attempt, _clock = _owned(tmp_path)
    html = tmp_path / "native-own-label-combobox.html"
    html.write_text("""<!doctype html><body>
      <label>First<input id='first'></label>
      <label>Target<input id='target' role='combobox' aria-controls='choices'
        onclick="window.opens++;document.querySelector('#choices').hidden=false"></label>
      <div id='choices' role='listbox' hidden>
        <button id='choice' type='button' role='option'
          onclick="window.targetWrites++;document.querySelector('#target').value=this.innerText">
          Exact choice</button>
      </div>
      <button type='button' onclick='window.submits++'>Submit application</button>
      <script>window.opens=0;window.targetWrites=0;window.submits=0;</script>
    """, encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        assert adapter.page.locator("#target").evaluate("e=>e.closest('label').control===e") is True
        actions = adapter.apply_resolutions([
            FieldResolution(field_id="first", selector="#first", label="First",
                value="PRIVATE_NATIVE_LABEL_CANARY", status=ResolutionStatus.RESOLVED),
            FieldResolution(field_id="target", selector="#target", label="Target",
                value="Exact choice", status=ResolutionStatus.RESOLVED),
        ])
        assert len(actions) == 2 and all(item["ok"] and item["observed"] == "DOM_READBACK" for item in actions)
        assert adapter.page.locator("#first").input_value() == "PRIVATE_NATIVE_LABEL_CANARY"
        assert adapter.page.locator("#target").input_value() == "Exact choice"
        assert adapter.page.evaluate("[window.opens,window.targetWrites,window.submits]") == [1,1,0]
    assert {row["outcome"] for row in queue.field_actions(tid)} == {"DOM_READBACK_UNVERIFIED"}
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"

@pytest.mark.parametrize("fault", [
    "widget_default_submit", "widget_reset", "option_default_submit",
    "option_external_form", "option_reset", "reactive_option_submit",
])
def test_aria_choice_never_dispatches_native_submit_or_reset(tmp_path, fault):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    widget_type = "" if fault == "widget_default_submit" else (
        "type='reset'" if fault == "widget_reset" else "type='button'")
    option_type = "" if fault in {"option_default_submit", "option_external_form"} else (
        "type='reset'" if fault == "option_reset" else "type='button'")
    option_form = "form='external'" if fault == "option_external_form" else ""
    html = tmp_path / "aria-native-default-actions.html"
    html.write_text("""<!doctype html><body>
      <form id='app'>
        <label>First<input id='first'></label>
        <label for='target'>Target</label>
        <button id='target' role='combobox' aria-label='Target' aria-controls='choices'
          __WIDGET_TYPE__
          onclick="window.opens++;document.querySelector('#choices').hidden=false">Choose</button>
        <div id='choices' role='listbox' hidden>
          <button id='choice' role='option' __OPTION_TYPE__ __OPTION_FORM__
            onclick="window.targetWrites++;document.querySelector('#target').innerText=this.innerText">
            Exact choice</button>
        </div>
        <button id='final' type='submit'>Submit application</button>
      </form>
      <form id='external'></form>
      <script>
        window.opens=0;window.targetWrites=0;window.submits=0;window.resets=0;
        document.addEventListener('submit',event=>{window.submits++;event.preventDefault();});
        document.addEventListener('reset',event=>{window.resets++;event.preventDefault();});
      </script>
    """.replace("__WIDGET_TYPE__", widget_type).replace("__OPTION_TYPE__", option_type)
        .replace("__OPTION_FORM__", option_form), encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    injected = []
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        if fault == "reactive_option_submit":
            def guard():
                rows = queue.field_actions(tid)
                if (not injected and len(rows) == 2
                        and sum(row["outcome"] == "ATTEMPTED" for row in rows) == 1
                        and adapter.page.locator("#choices").is_visible()):
                    adapter.page.locator("#choice").evaluate("e=>e.removeAttribute('type')")
                    assert adapter.page.locator("#choice").evaluate("e=>[e.type,e.form.id]") == [
                        "submit", "app"]
                    injected.append(True)
            adapter.mutation_guard = guard
        dangerous = "#target" if fault.startswith("widget_") else "#choice"
        if fault != "reactive_option_submit":
            effective = adapter.page.locator(dangerous).evaluate("e=>[e.type,e.form.id]")
            assert effective == ["reset" if fault.endswith("reset") else "submit",
                                 "external" if fault == "option_external_form" else "app"]
        with pytest.raises(BrowserOwnershipError, match="field write outcome unknown") as caught:
            adapter.apply_resolutions([
                FieldResolution(field_id="first", selector="#first", label="First",
                    value="PRIVATE_NATIVE_EFFECT_CANARY", status=ResolutionStatus.RESOLVED),
                FieldResolution(field_id="target", selector="#target", label="Target",
                    value="Exact choice", status=ResolutionStatus.RESOLVED),
            ])
        assert adapter.page.locator("#first").input_value() == "PRIVATE_NATIVE_EFFECT_CANARY"
        assert adapter.page.locator("#target").inner_text() == "Choose"
        opened = not fault.startswith("widget_")
        assert adapter.page.locator("#choices").is_visible() == opened
        assert adapter.page.evaluate("[window.opens,window.targetWrites,window.submits,window.resets]") == [
            1 if opened else 0, 0, 0, 0]
        assert adapter.page.locator("#final").is_enabled() is True
        assert injected == ([True] if fault == "reactive_option_submit" else [])
        assert "PRIVATE_" not in str(caught.value)
    actions = queue.field_actions(tid)
    assert len(actions) == 2 and {row["outcome"] for row in actions} == {"UNKNOWN_OUTCOME"}
    assert "PRIVATE_" not in json.dumps(actions)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert rebuilt.field_actions(tid) == actions
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"refused-native-effect-replay").hexdigest())
    assert rebuilt.get(tid)["stage"] != "READY_TO_SUBMIT"


def test_explicit_button_aria_choices_inside_form_preserve_user_submit_boundary(tmp_path):
    queue, tid, owner, attempt, _clock = _owned(tmp_path)
    html = tmp_path / "aria-explicit-button-form.html"
    html.write_text("""<!doctype html><body><form id='app'>
      <label>First<input id='first'></label><label for='target'>Target</label>
      <button id='target' type='button' role='combobox' aria-label='Target' aria-controls='choices'
        onclick="window.opens++;document.querySelector('#choices').hidden=false">Choose</button>
      <div id='choices' role='listbox' hidden>
        <button id='choice' type='button' role='option'
          onclick="window.targetWrites++;document.querySelector('#target').innerText=this.innerText;document.querySelector('#target').value=this.innerText">
          Exact choice</button>
      </div>
      <button id='final' type='submit'>Submit application</button>
      </form><script>
        window.opens=0;window.targetWrites=0;window.submits=0;window.resets=0;
        document.addEventListener('submit',event=>{window.submits++;event.preventDefault();});
        document.addEventListener('reset',event=>{window.resets++;event.preventDefault();});
      </script>
    """, encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        adapter.field_readbacks_invalidate = audit.invalidate_field_readbacks
        actions = adapter.apply_resolutions([
            FieldResolution(field_id="first", selector="#first", label="First",
                value="PRIVATE_EXPLICIT_BUTTON_CANARY", status=ResolutionStatus.RESOLVED),
            FieldResolution(field_id="target", selector="#target", label="Target",
                value="Exact choice", status=ResolutionStatus.RESOLVED),
        ])
        assert len(actions) == 2 and all(item["ok"] and item["observed"] == "DOM_READBACK" for item in actions)
        assert adapter.page.locator("#first").input_value() == "PRIVATE_EXPLICIT_BUTTON_CANARY"
        assert adapter.page.locator("#target").inner_text() == "Exact choice"
        assert adapter.page.evaluate("[window.opens,window.targetWrites,window.submits,window.resets]") == [1,1,0,0]
        assert adapter.page.locator("#final").is_enabled() is True
    assert {row["outcome"] for row in queue.field_actions(tid)} == {"DOM_READBACK_UNVERIFIED"}
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"
