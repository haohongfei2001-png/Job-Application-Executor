"""Synthetic auth lifecycle evidence; no real SMS, account, or user profile."""

import json
import http.cookiejar
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from executor.auth.attempts import AuthAttemptStore
from executor.autonomy.otp import OtpBroker
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker
from executor.evidence import write_profile
from executor.models import ApplicantProfile, ProfileField


def _task(tmp_path, queue, *, suffix="one"):
    profile = tmp_path / "synthetic-profile.json"
    if not profile.exists():
        write_profile(ApplicantProfile(), profile)
    spec = TaskSpec(company="Synthetic", role="Tester",
                    target_url=f"https://auth.example.test/jobs/{suffix}",
                    profile_ref=str(profile))
    return queue.enqueue(spec)["task_id"], spec


def _attempt(queue, tid, spec, *, owner="synthetic-worker"):
    claimed = queue.claim(owner)
    assert claimed["task_id"] == tid
    store = AuthAttemptStore(queue)
    attempt = store.begin(tid, claimed["owner"], "auth.example.test", spec.target_url)
    return store, claimed["owner"], attempt


def test_unknown_send_survives_restart_and_cannot_click_again(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, attempt = _attempt(queue, tid, spec)
    store.record_send_intent(attempt["attempt_id"], tid, owner)
    restarted = AuthAttemptStore(TaskQueue(queue.root))
    observed = restarted.begin(tid, owner, "auth.example.test", spec.target_url)
    assert observed["attempt_id"] == attempt["attempt_id"]
    assert observed["send_outcome"] == "SEND_UNKNOWN"
    with pytest.raises(RuntimeError, match="already attempted"):
        restarted.record_send_intent(attempt["attempt_id"], tid, owner)
    assert restarted.valid_wait(tid, attempt_id=attempt["attempt_id"],
                                origin="auth.example.test") is not None
    raw = queue.path.read_bytes()
    assert b"13800138000" not in raw
    assert b"48291357" not in raw


def test_auth_attempt_metadata_migration_keeps_old_wait_and_task(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    with queue.tx() as db:
        db.execute("""CREATE TABLE auth_attempts (
            attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, origin TEXT NOT NULL,
            return_target_digest TEXT NOT NULL, mode TEXT NOT NULL,
            phone_fact_ref TEXT NOT NULL, send_outcome TEXT NOT NULL,
            requested_at REAL, deadline REAL, cooldown_until REAL,
            current INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)""")
        db.execute("""INSERT INTO auth_attempts VALUES(
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',?,?,?,'sms','identity.phone',
            'SEND_UNKNOWN',?,?,?,1,?,?)""",
            (tid, "auth.example.test", "old-target-digest", queue.clock(),
             queue.clock()+300, queue.clock()+60, queue.clock(), queue.clock()))
    migrated = AuthAttemptStore(queue)
    current = migrated.current(tid)
    assert current["attempt_id"] == "a" * 32
    assert current["send_outcome"] == "SEND_UNKNOWN"
    assert current["resend_parent_id"] is None
    assert TaskQueue(queue.root).get(tid)["spec"]["target_url"] == spec.target_url
    assert AuthAttemptStore(TaskQueue(queue.root)).current(tid)["attempt_id"] == "a" * 32


def test_code_is_bound_to_task_attempt_origin_and_memory(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, attempt = _attempt(queue, tid, spec)
    store.record_send_intent(attempt["attempt_id"], tid, owner)
    store.record_send_result(attempt["attempt_id"], outcome="CLICK_OBSERVED")
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    broker = OtpBroker(queue)
    assert broker.push(task_id=tid, message="OTP 48291357")["reason"] == "auth_attempt_required"
    assert not broker.push(task_id=tid, attempt_id="0" * 32,
                           message="OTP 48291357")["accepted"]
    accepted = broker.push(task_id=tid, attempt_id=attempt["attempt_id"],
                           message="OTP 48291357")
    assert accepted["accepted"]
    assert broker.consume(tid, attempt_id=attempt["attempt_id"],
                          origin="other.example.test") is None
    assert broker.consume(tid, attempt_id=attempt["attempt_id"],
                          origin="auth.example.test") == "48291357"
    assert broker.consume(tid, attempt_id=attempt["attempt_id"]) is None
    assert OtpBroker(TaskQueue(queue.root)).consume(tid) is None
    assert b"48291357" not in queue.path.read_bytes()


def test_old_attempt_cannot_feed_new_attempt_after_switch(tmp_path):
    now = [1000.0]
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: now[0])
    tid, spec = _task(tmp_path, queue)
    store, owner, first = _attempt(queue, tid, spec)
    store.record_send_intent(first["attempt_id"], tid, owner)
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    now[0] += 61
    second = store.authorize_resend(
        tid, command_id="switch-attempt", expected_revision=queue.get(tid)["revision"])
    assert second["attempt_id"] != first["attempt_id"]
    claimed = queue.claim("synthetic-resend-worker")
    assert claimed["task_id"] == tid
    store.record_send_intent(second["attempt_id"], tid, claimed["owner"])
    queue.checkpoint(tid, claimed["owner"], "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    broker = OtpBroker(queue)
    assert not broker.push(task_id=tid, attempt_id=first["attempt_id"],
                           message="OTP 48291357")["accepted"]
    assert broker.push(task_id=tid, attempt_id=second["attempt_id"],
                       message="OTP 72941836")["accepted"]
    assert broker.consume(tid, attempt_id=second["attempt_id"],
                          origin="auth.example.test") == "72941836"


def test_closed_unknown_send_cannot_silently_start_fresh_sms(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, first = _attempt(queue, tid, spec)
    store.record_send_intent(first["attempt_id"], tid, owner)
    store.close(tid, first["attempt_id"], outcome="EXPIRED")
    with pytest.raises(RuntimeError, match="explicit reconciliation"):
        store.begin(tid, owner, "auth.example.test", spec.target_url)


def test_resend_requires_explicit_command_cooldown_and_one_new_attempt(tmp_path):
    now = [1000.0]
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: now[0])
    tid, spec = _task(tmp_path, queue)
    store, owner, first = _attempt(queue, tid, spec)
    store.record_send_intent(first["attempt_id"], tid, owner)
    store.record_send_result(first["attempt_id"], outcome="CLICK_OBSERVED")
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    revision = queue.get(tid)["revision"]
    with pytest.raises(RuntimeError, match="cooldown"):
        store.authorize_resend(tid, command_id="resend-one", expected_revision=revision)
    assert store.current(tid)["attempt_id"] == first["attempt_id"]
    now[0] += 61
    second = store.authorize_resend(tid, command_id="resend-one", expected_revision=revision)
    assert second["attempt_id"] != first["attempt_id"]
    assert second["resend_parent_id"] == first["attempt_id"]
    assert second["send_outcome"] == "PREPARED"
    assert queue.get(tid)["stage"] != "NEEDS_USER_ACTION"
    assert store.authorize_resend(tid, command_id="resend-one",
                                  expected_revision=revision)["attempt_id"] == second["attempt_id"]
    assert not store.valid_wait(tid, attempt_id=first["attempt_id"])
    with pytest.raises(RuntimeError, match="stale"):
        store.authorize_resend(tid, command_id="resend-two", expected_revision=revision)
    assert store.current(tid)["attempt_id"] == second["attempt_id"]


def test_resend_invalidates_buffered_old_code_and_is_task_scoped(tmp_path):
    now = [1000.0]
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: now[0])
    tid, spec = _task(tmp_path, queue)
    store, owner, first = _attempt(queue, tid, spec)
    store.record_send_intent(first["attempt_id"], tid, owner)
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    supervisor = Supervisor(queue, worker=worker, token="x" * 40)
    assert worker.broker.push(task_id=tid, attempt_id=first["attempt_id"],
                              message="验证码 482913")["accepted"]
    revision = queue.get(tid)["revision"]
    now[0] += 61
    assert queue.get(tid)["stage"] == "NEEDS_USER_ACTION"
    assert queue.get(tid)["blocker"] == "otp_waiting"
    assert queue.get(tid)["revision"] == revision
    second = supervisor.dispatch("POST", f"/v1/tasks/{tid}/authorize-otp-resend", {
        "command_id": "resend-one", "expected_revision": revision,
    })
    assert second["status"] == "RESEND_AUTHORIZED_ONCE"
    assert worker.broker.consume(tid, attempt_id=first["attempt_id"]) is None
    assert not worker.broker.push(task_id=tid, attempt_id=first["attempt_id"],
                                  message="验证码 482913")["accepted"]
    other_tid, _ = _task(tmp_path, queue, suffix="other")
    with pytest.raises(ValueError, match="another task"):
        store.authorize_resend(other_tid, command_id="resend-one", expected_revision=0)
    assert b"482913" not in queue.path.read_bytes()


def test_late_source_resumes_exact_wait_without_worker_lease(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, attempt = _attempt(queue, tid, spec)
    store.record_send_intent(attempt["attempt_id"], tid, owner)
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    release = threading.Event()
    observed = threading.Event()

    class FakeSource:
        enabled = True

        def wait_for_code(self, site, **kwargs):
            observed.set()
            assert site == "auth.example.test"
            assert kwargs["attempt_id"] == attempt["attempt_id"]
            release.wait(2)
            return {"code": "482913", "attempt_id": kwargs["attempt_id"],
                    "origin": site, "source": "fake_sms"}

    worker = Worker(queue, relay=FakeSource(), settings={"deepseek": {"enabled": False}})
    assert worker.run_once() is False
    assert observed.wait(1)
    assert queue.get(tid)["stage"] == "NEEDS_USER_ACTION"
    assert queue.get(tid)["owner"] is None
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and queue.get(tid)["stage"] == "NEEDS_USER_ACTION":
        time.sleep(0.01)
    assert queue.get(tid)["stage"] != "NEEDS_USER_ACTION"
    assert worker.broker.consume(tid, attempt_id=attempt["attempt_id"],
                                 origin="auth.example.test") == "482913"
    assert worker.broker.consume(tid, attempt_id=attempt["attempt_id"]) is None


def test_authenticated_local_ui_otp_handoff_never_echoes_code(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, attempt = _attempt(queue, tid, spec)
    store.record_send_intent(attempt["attempt_id"], tid, owner)
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    supervisor = Supervisor(queue, worker=worker, token="x" * 40)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        payload = json.dumps({"task_id": tid, "attempt_id": attempt["attempt_id"],
                              "message": "482913"}).encode()
        request = urllib.request.Request(base + "/ui/api/otp", data=payload,
                                         headers={"Content-Type": "application/json",
                                                  "Origin": base})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request)
        assert denied.value.code == 401
        ticket_request = urllib.request.Request(
            base + "/v1/ui-ticket", data=b"{}", method="POST",
            headers={"Authorization": "Bearer " + "x" * 40,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(ticket_request) as response:
            ticket = json.load(response)["ticket"]
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(base + "/ui-login?ticket=" + ticket):
            pass
        with opener.open(base + "/ui/api/state") as response:
            state = json.load(response)
        view = next(t for t in state["tasks"] if t["task_id"] == tid)
        assert view["auth_attempt_id"] == attempt["attempt_id"]
        assert view["otp_source"] == "local_input_only"
        with opener.open(request) as response:
            body = response.read().decode()
        assert '"accepted": true' in body
        assert "482913" not in body
        assert b"482913" not in queue.path.read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_headless_local_task_card_delivers_otp_without_chat(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, attempt = _attempt(queue, tid, spec)
    store.record_send_intent(attempt["attempt_id"], tid, owner)
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    supervisor = Supervisor(queue, worker=worker, token="x" * 40)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        ticket_request = urllib.request.Request(
            base + "/v1/ui-ticket", data=b"{}", method="POST",
            headers={"Authorization": "Bearer " + "x" * 40,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(ticket_request) as response:
            ticket = json.load(response)["ticket"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base + "/ui-login?ticket=" + ticket)
            field = page.locator(f'[data-otp-task="{tid}"]')
            field.wait_for()
            assert page.locator("#chat").inner_text().find("482913") == -1
            field.fill("482913")
            page.locator(f'[data-otp-send][data-task="{tid}"]').click()
            page.get_by_text("验证码已通过本地专用通道交给当前任务。").wait_for()
            assert page.locator("#chat").inner_text().find("482913") == -1
            assert queue.get(tid)["stage"] != "NEEDS_USER_ACTION"
            browser.close()
        assert b"482913" not in queue.path.read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_already_authenticated_task_never_requests_sms(tmp_path):
    state = {"sms_requests": 0, "submits": 0}

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = ("<meta charset='utf-8'><label>Unknown objective fact "
                    "<input id='unknown_fact' required></label>"
                    "<button type='button' onclick=\"fetch('/submit',{method:'POST'})\">确认提交</button>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            state["sms_requests" if self.path == "/sms" else "submits"] += 1
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        profile = tmp_path / "synthetic-profile.json"
        write_profile(ApplicantProfile(), profile)
        queue = TaskQueue(tmp_path / "runtime")
        target = f"http://127.0.0.1:{server.server_port}/apply"
        tid = queue.enqueue(TaskSpec(company="Synthetic", role="Tester",
                                     target_url=target, profile_ref=str(profile)))["task_id"]
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        assert worker.run_once()
        assert queue.get(tid)["stage"] == "NEEDS_USER_INPUT"
        assert worker.broker.attempts.current(tid) is None
        assert state == {"sms_requests": 0, "submits": 0}
    finally:
        server.shutdown()


@pytest.mark.parametrize(("authorized_resend", "wrong_return"),
                         [(False, False), (True, False), (False, True)])
def test_isolated_worker_sms_send_once_then_returns_to_exact_task(
        tmp_path, authorized_resend, wrong_return):
    state = {"sms_requests": 0, "authenticated": False, "submits": 0}

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/authenticated":
                state["authenticated"] = True
            if state["authenticated"]:
                body = ("<meta charset='utf-8'><label>Unknown objective fact "
                        "<input id='unknown_fact' required></label>"
                        "<button type='button' onclick=\"fetch('/submit',{method:'POST'})\">确认提交</button>")
            else:
                send_label = "重新发送" if state["sms_requests"] else "发送验证码"
                after_auth = "location.href='/wrong'" if wrong_return else "location.reload()"
                body = ("<meta charset='utf-8'><form id='login'><h2>登录</h2>"
                        "<label>手机号<input id='phone' type='tel'></label>"
                        "<label>验证码<input id='otp' autocomplete='one-time-code' "
                        f"oninput=\"if(this.value==='482913')fetch('/authenticated').then(()=>{after_auth})\"></label>"
                        "<button id='send' type='button' "
                        "onclick=\"fetch('/sms',{method:'POST'}).then(()=>this.textContent='重新发送')\">"
                        f"{send_label}</button></form>")
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            state["sms_requests" if self.path == "/sms" else "submits"] += 1
            self.send_response(200)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        profile = tmp_path / "synthetic-profile.json"
        write_profile(ApplicantProfile(fields={"identity.phone": ProfileField(
            value="13800138000", user_confirmed=True)}), profile)
        queue = TaskQueue(tmp_path / "runtime")
        target = f"http://127.0.0.1:{server.server_port}/apply"
        tid = queue.enqueue(TaskSpec(company="Synthetic", role="Tester",
                                     target_url=target, profile_ref=str(profile)))["task_id"]
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        supervisor = Supervisor(queue, worker=worker, token="x" * 40)
        assert worker.run_once()
        waiting = queue.get(tid)
        assert waiting["stage"] == "NEEDS_USER_ACTION"
        assert waiting["blocker"] == "otp_waiting"
        assert state["sms_requests"] == 1
        attempt = worker.broker.attempts.current(tid)
        assert attempt["send_outcome"] == "CLICK_OBSERVED"
        if authorized_resend:
            queue.clock = lambda: time.time() + 61
            result = supervisor.dispatch("POST", f"/v1/tasks/{tid}/authorize-otp-resend", {
                "command_id": "resend-once", "expected_revision": waiting["revision"],
            })
            assert result["status"] == "RESEND_AUTHORIZED_ONCE"
            assert worker.run_once()
            attempt = worker.broker.attempts.current(tid)
            assert attempt["attempt_id"] == result["attempt_id"]
            assert attempt["send_outcome"] == "CLICK_OBSERVED"
            assert state["sms_requests"] == 2
        assert supervisor.dispatch("POST", "/v1/otp", {
            "task_id": tid, "attempt_id": attempt["attempt_id"],
            "message": "验证码 482913",
        })["accepted"]
        assert worker.run_once()
        if wrong_return:
            assert queue.get(tid)["stage"] == "BLOCKED"
            assert queue.get(tid)["blocker"] == "auth_return_unverified"
            assert worker.broker.attempts.current(tid) is not None
            with pytest.raises(ValueError, match="read-only reconciliation"):
                queue.resume(tid)
        else:
            assert queue.get(tid)["stage"] == "NEEDS_USER_INPUT"
        assert state == {"sms_requests": 2 if authorized_resend else 1,
                         "authenticated": True, "submits": 0}
        if not wrong_return:
            assert worker.broker.attempts.current(tid) is None
        assert b"482913" not in queue.path.read_bytes()
        assert "482913" not in json.dumps(supervisor.diagnostics(), ensure_ascii=False)
        assert "482913" not in json.dumps(supervisor.ui_state(), ensure_ascii=False)
        assert "482913" not in json.dumps(queue.events(0), ensure_ascii=False)
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert b"482913" not in path.read_bytes(), path.name
    finally:
        server.shutdown()
