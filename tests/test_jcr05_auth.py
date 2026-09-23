"""Synthetic auth lifecycle evidence; no real SMS, account, or user profile."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from executor.auth.attempts import AuthAttemptStore
from executor.autonomy.otp import OtpBroker
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor
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
    queue = TaskQueue(tmp_path / "runtime")
    tid, spec = _task(tmp_path, queue)
    store, owner, first = _attempt(queue, tid, spec)
    store.record_send_intent(first["attempt_id"], tid, owner)
    store.close(tid, first["attempt_id"], outcome="EXPIRED")
    second = store.begin(tid, owner, "auth.example.test", spec.target_url)
    assert second["attempt_id"] != first["attempt_id"]
    store.record_send_intent(second["attempt_id"], tid, owner)
    queue.checkpoint(tid, owner, "NEEDS_USER_ACTION", blocker="otp_waiting", release=True)
    broker = OtpBroker(queue)
    assert not broker.push(task_id=tid, attempt_id=first["attempt_id"],
                           message="OTP 48291357")["accepted"]
    assert broker.push(task_id=tid, attempt_id=second["attempt_id"],
                       message="OTP 72941836")["accepted"]
    assert broker.consume(tid, attempt_id=second["attempt_id"],
                          origin="auth.example.test") == "72941836"


def test_isolated_worker_sms_send_once_then_returns_to_exact_task(tmp_path):
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
                body = ("<meta charset='utf-8'><form id='login'><h2>登录</h2>"
                        "<label>手机号<input id='phone' type='tel'></label>"
                        "<label>验证码<input id='otp' autocomplete='one-time-code' "
                        "oninput=\"if(this.value==='482913')fetch('/authenticated').then(()=>location.reload())\"></label>"
                        "<button id='send' type='button' "
                        "onclick=\"fetch('/sms',{method:'POST'}).then(()=>this.textContent='重新发送')\">"
                        "发送验证码</button></form>")
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
        assert supervisor.dispatch("POST", "/v1/otp", {
            "task_id": tid, "attempt_id": attempt["attempt_id"],
            "message": "验证码 482913",
        })["accepted"]
        assert worker.run_once()
        assert queue.get(tid)["stage"] == "NEEDS_USER_INPUT"
        assert state == {"sms_requests": 1, "authenticated": True, "submits": 0}
        assert worker.broker.attempts.current(tid) is None
        assert b"482913" not in queue.path.read_bytes()
    finally:
        server.shutdown()
