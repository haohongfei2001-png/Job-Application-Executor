"""A user-facing command must produce the expected server draft, not just READY."""
from __future__ import annotations

import http.cookiejar
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from executor.autonomy.manager import ManagerAction, ManagerController, ManagerDecision, ManagerTurn
from executor.autonomy.queue import TaskQueue
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker


class SyntheticATS(BaseHTTPRequestHandler):
    draft = None
    submit_count = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
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
            if data["field"] in {"full_name", "email"}:
                self.server.draft[data["field"]] = data["value"]
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


def test_ui_to_service_to_browser_draft_has_independent_server_oracle(tmp_path):
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.submit_count = {}, 0
    ats_thread = threading.Thread(target=ats.serve_forever, daemon=True)
    ats_thread.start()
    local = None
    try:
        target_url = f"http://127.0.0.1:{ats.server_port}/apply?postId=synthetic-01"
        profile = tmp_path / "profile.json"
        golden = {"full_name": "Synthetic Applicant", "email": "synthetic@example.test"}
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
        assert actual["submit_count"] == 0
        assert queue.get(tid)["stage"] == "READY_TO_SUBMIT"
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()
