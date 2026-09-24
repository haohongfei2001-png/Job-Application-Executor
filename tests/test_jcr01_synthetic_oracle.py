"""A user-facing command must produce the expected server draft, not just READY."""
from __future__ import annotations

import http.cookiejar
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from executor.autonomy.manager import ManagerAction, ManagerController, ManagerDecision, ManagerTurn
from executor.autonomy.queue import TaskQueue
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker
from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.models import ApplicationStage


class SyntheticATS(BaseHTTPRequestHandler):
    draft = None
    submit_count = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/apply-multipage" or self.path == "/apply-second":
            second = self.path == "/apply-second"
            if second:
                self.server.page_two_reads += 1
            field = "email" if second else "full_name"
            label = "Email" if second else "Full name"
            control = ("<button type='button'>Submit application</button>" if second
                       else "<button type='button' onclick=\"location.href='/apply-second'\">Next</button>")
            body = f'''<!doctype html><meta charset="utf-8"><body>
              <label>{label}<input id="{field}" name="{field}" required></label>
              {control}
              <script>
              document.querySelector('input').addEventListener('input', event => {{
                const request = new XMLHttpRequest();
                request.open('POST', '/draft', false);
                request.setRequestHeader('Content-Type', 'application/json');
                request.send(JSON.stringify({{field:event.target.name, value:event.target.value}}));
              }});
              </script></body>'''.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/apply"):
            body = b'''<!doctype html><meta charset="utf-8"><body>
              <label>Full name <input id="name" name="full_name" required></label>
              <label>Email <input id="email" name="email" type="email" required></label>
              <button id="submit" onclick="fetch('/submit', {method:'POST'})">Submit application</button>
              <script>
              for (const field of document.querySelectorAll('input')) {
                field.addEventListener('input', () => {
                  const request = new XMLHttpRequest();
                  request.open('POST', '/draft', false);
                  request.setRequestHeader('Content-Type', 'application/json');
                  request.send(JSON.stringify({field:field.name, value:field.value}));
                });
              }
              </script></body>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/oracle":
            body = json.dumps({"draft": self.server.draft,
                               "revision": self.server.revision,
                               "submit_count": self.server.submit_count}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path == "/draft":
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if data["field"] in {"full_name", "email"} and getattr(self.server, "accept_draft", True):
                self.server.draft[data["field"]] = data["value"]
                self.server.revision += 1
        elif self.path == "/submit":
            self.server.submit_count += 1
        else:
            self.send_error(404)
            return
        self.send_response(204)
        self.end_headers()


class Proposal:
    available = True

    def __init__(self, target_url):
        self.target_url = target_url

    def decide(self, *_):
        return ManagerTurn(reply="准备合成岗位", decisions=[
            ManagerDecision(action=ManagerAction.CREATE_TASK, company="Synthetic ATS",
                            role="Engineer", target_url=self.target_url)
        ])


def test_ui_to_service_to_browser_draft_has_independent_server_oracle(tmp_path, monkeypatch):
    manifest = json.loads((Path(__file__).parent / "fixtures/syntheticats-v1.manifest.json").read_text())
    assert manifest["origin_class"] == "SYNTHETIC"
    assert manifest["network_allowlist"] == ["127.0.0.1"]
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.submit_count, ats.revision = {}, 0, 0
    ats_thread = threading.Thread(target=ats.serve_forever, daemon=True)
    ats_thread.start()
    local = None
    try:
        target_url = f"http://127.0.0.1:{ats.server_port}/apply?postId=synthetic-01"
        profile = tmp_path / "profile.json"
        golden = manifest["golden_expected_outcome"]["draft"]
        class SyntheticATSAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, _plan):
                actual = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                if actual["draft"] != golden or actual["revision"] < 2:
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": actual["revision"]}

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: SyntheticATSAdapter(url))
        profile.write_text(json.dumps({"fields": {
            "identity.full_name": {"value": golden["full_name"], "confidence": 1.0},
            "identity.email": {"value": golden["email"], "confidence": 1.0},
        }}))
        queue = TaskQueue(tmp_path / "runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(queue, worker, provider=Proposal(target_url),
                                    settings={"profile_path": str(profile)})
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()
        request = urllib.request.Request(base + "/ui/api/chat",
            data=json.dumps({"message": "请申请 " + target_url}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        response = json.loads(browser.open(request).read())
        assert response["actions"][0]["status"] == "accepted"
        tid = response["actions"][0]["task_id"]
        assert worker.run_once()
        actual = json.load(urllib.request.urlopen(f"http://127.0.0.1:{ats.server_port}/oracle"))
        assert actual["draft"] == golden
        assert actual["submit_count"] == manifest["golden_expected_outcome"]["submit_count"]
        assert queue.get(tid)["stage"] == "READY_TO_SUBMIT"
        # Fault canary: the same page state must not certify a wrong server draft.
        ats.draft["email"] = "wrong@example.test"
        assert SyntheticATSAdapter(target_url).verify_draft_persistence(None) is None
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()


def test_multipage_navigation_requires_independent_draft_readback(tmp_path, monkeypatch):
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.revision, ats.submit_count, ats.page_two_reads = {}, 0, 0, 0
    ats.accept_draft = True
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    try:
        target = f"http://127.0.0.1:{ats.server_port}/apply-multipage"
        profile = tmp_path / "profile.json"
        profile.write_text(json.dumps({"fields": {
            "identity.full_name": {"value": "Synthetic Person", "confidence": 1.0},
            "identity.email": {"value": "synthetic@example.test", "confidence": 1.0},
        }}))

        class MultiPageAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, _plan):
                oracle = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                expected = {"full_name": "Synthetic Person"}
                if self.page.url.endswith("/apply-second"):
                    expected["email"] = "synthetic@example.test"
                if oracle["draft"] != expected or oracle["revision"] < len(expected):
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": oracle["revision"]}

        monkeypatch.setattr("executor.application.adapter_for_url", lambda url: MultiPageAdapter(url))
        monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
        plan = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=2)
        assert plan.stage == ApplicationStage.READY_TO_SUBMIT
        assert plan.metadata["page_draft_receipts"] == [{
            "page_index": 0, "level": "server_readback", "revision": 1}]
        assert ats.draft == {"full_name": "Synthetic Person", "email": "synthetic@example.test"}
        assert ats.submit_count == 0

        ats.draft, ats.revision, ats.page_two_reads, ats.accept_draft = {}, 0, 0, False
        blocked = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=2)
        assert blocked.stage == ApplicationStage.BLOCKED
        assert blocked.metadata["block_reason"] == "draft persistence unverified before navigation"
        assert ats.page_two_reads == 0
        assert ats.submit_count == 0
    finally:
        ats.shutdown()
        ats.server_close()
