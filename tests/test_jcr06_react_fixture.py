"""Real React controlled input plus independent local draft oracle, headless only."""
from __future__ import annotations

import json
import hashlib
import http.cookiejar
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from executor.adapters.generic_web import GenericWebAdapter
from executor.autonomy.manager import ManagerAction, ManagerController, ManagerDecision, ManagerTurn
from executor.autonomy.queue import TaskQueue
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker
from executor.application import ApplicationExecutor
from executor.models import ApplicationStage, FieldResolution, ResolutionStatus


BUNDLE = Path(__file__).parent / "fixtures" / "react-controlled.bundle.js"


class ReactFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/apply":
            body = (b'<!doctype html><meta charset="utf-8"><body><div id="root"></div>'
                    b'<script src="/fixture.js"></script>')
            content_type = "text/html; charset=utf-8"
        elif self.path == "/fixture.js":
            body = BUNDLE.read_bytes()
            content_type = "application/javascript"
        elif self.path == "/oracle":
            body = json.dumps({"draft": self.server.draft,
                               "revision": self.server.revision,
                               "submit_count": self.server.submit_count}).encode()
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/draft":
            self.send_error(404)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if (data.get("field") == "full_name" and isinstance(data.get("value"), str)
                and not getattr(self.server, "drop_draft", False)):
            self.server.draft["full_name"] = data["value"]
            self.server.revision += 1
        self.send_response(204)
        self.end_headers()


def test_react_controlled_rerender_and_server_draft(tmp_path, monkeypatch):
    ats = ThreadingHTTPServer(("127.0.0.1", 0), ReactFixture)
    ats.draft, ats.revision, ats.submit_count = {}, 0, 0
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{ats.server_port}/apply"
    try:
        with GenericWebAdapter(target) as adapter:
            adapter.page.locator("#name").wait_for()
            actions = adapter.apply_resolutions([FieldResolution(
                field_id="full_name", selector="#name", label="Full name",
                status=ResolutionStatus.RESOLVED, value="Synthetic Person")])
            assert actions[0]["ok"] is True
            adapter.page.locator("#rerender").click()
            adapter.page.locator("#framework-state[data-generation='1']").wait_for()
            assert adapter.page.locator("#framework-state").get_attribute(
                "data-framework-value") == "Synthetic Person"
            assert adapter.page.locator("#name").input_value() == "Synthetic Person"
        oracle = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{ats.server_port}/oracle"))
        assert oracle["draft"] == {"full_name": "Synthetic Person"}
        assert oracle["revision"] >= 1
        assert oracle["submit_count"] == 0

        class ReactFixtureAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, _plan):
                observed = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                if observed["draft"] != {"full_name": "Synthetic Person"}:
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": observed["revision"]}

            def observe_review_draft(self, plan):
                observed = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                draft = observed["draft"]
                digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
                return {
                    "source": "server_readback",
                    "target_sha256": digest(plan.target_url),
                    "draft_id_digest": digest("react-fixture-draft"),
                    "revision": observed["revision"],
                    "account_verified": True,
                    "account_identity_digest": digest(
                        "identity.full_name:synthetic person"),
                    "complete_pages": set(draft) == {"full_name"},
                    "complete_required": set(draft) == {"full_name"},
                    "save_status": "VERIFIED", "validation_error_count": 0,
                    "hidden_required_count": 0, "unverified_default_count": 0,
                    "document_epoch": digest(self.page.url),
                    "driver_version": "react-fixture-v1",
                    "fields": [
                        {"index": index, "field_id": field.field_id,
                         "selector": field.selector, "required": field.required,
                         "value": draft.get(field.field_id)}
                        for index, field in enumerate(plan.fields)],
                    "attachments": {}, "rows": {},
                }

        profile = tmp_path / "profile.json"
        profile.write_text(json.dumps({"fields": {"identity.full_name": {
            "value": "Synthetic Person", "confidence": 1.0}}}))
        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: ReactFixtureAdapter(url))
        monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
        plan = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
        assert plan.stage == ApplicationStage.READY_TO_SUBMIT
        assert ats.submit_count == 0
        ats.draft["full_name"] = "wrong value"
        assert ReactFixtureAdapter(target).verify_draft_persistence(None) is None

        ats.draft, ats.revision, ats.drop_draft = {}, 0, True
        unpersisted = ApplicationExecutor(target, profile, {
            "deepseek": {"enabled": False}}).run(max_pages=1)
        assert unpersisted.stage == ApplicationStage.BLOCKED
        assert unpersisted.metadata["block_reason"] == "draft persistence unverified"
        assert ats.submit_count == 0
    finally:
        ats.shutdown()
        ats.server_close()


def test_twenty_react_controlled_ui_tasks_keep_independent_server_drafts(
    tmp_path, monkeypatch
):
    """Third synthetic mechanism through local UI, durable queue and real browser."""
    ats = ThreadingHTTPServer(("127.0.0.1", 0), ReactFixture)
    ats.draft, ats.revision, ats.submit_count, ats.drop_draft = {}, 0, 0, False
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    local = None
    try:
        expected = {}
        target = f"http://127.0.0.1:{ats.server_port}/apply?postId=react-000"

        class ReactUIAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, plan):
                observed = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                if (plan.target_url != target or observed["draft"] != expected
                        or observed["revision"] < 1):
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": observed["revision"]}

            def observe_review_draft(self, plan):
                observed = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                draft = observed["draft"]
                digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
                return {
                    "source": "server_readback",
                    "target_sha256": digest(plan.target_url),
                    "draft_id_digest": digest("react-ui-draft"),
                    "revision": observed["revision"],
                    "account_verified": True,
                    "account_identity_digest": digest(
                        "identity.full_name:" + expected["full_name"].casefold()),
                    "complete_pages": set(draft) == {"full_name"},
                    "complete_required": set(draft) == {"full_name"},
                    "save_status": "VERIFIED", "validation_error_count": 0,
                    "hidden_required_count": 0, "unverified_default_count": 0,
                    "document_epoch": digest(self.page.url),
                    "driver_version": "react-fixture-v1",
                    "fields": [
                        {"index": index, "field_id": field.field_id,
                         "selector": field.selector, "required": field.required,
                         "value": draft.get(field.field_id)}
                        for index, field in enumerate(plan.fields)],
                    "attachments": {}, "rows": {},
                }

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: ReactUIAdapter(url))

        class Proposal:
            available = True

            def __init__(self, target_url):
                self.target_url = target_url

            def decide(self, *_):
                return ManagerTurn(reply="准备合成岗位", decisions=[
                    ManagerDecision(action=ManagerAction.CREATE_TASK,
                                    company="Synthetic React ATS", role="Engineer",
                                    target_url=self.target_url)
                ])

        proposal = Proposal(target)
        queue = TaskQueue(tmp_path / "react-ui-runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(
            queue, worker, provider=proposal,
            settings={"profile_path": str(tmp_path / "react-profile.json")},
        )
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()

        tasks = {}
        for seed in range(20):
            target = (f"http://127.0.0.1:{ats.server_port}/apply"
                      f"?postId=react-{seed:03d}")
            proposal.target_url = target
            profile = tmp_path / f"react-profile-{seed:03d}.json"
            manager.settings["profile_path"] = str(profile)
            expected = {"full_name": f"Synthetic React Applicant {seed:03d}"}
            profile.write_text(json.dumps({"fields": {
                "identity.full_name": {
                    "value": expected["full_name"], "confidence": 1.0}
            }}), encoding="utf-8")
            ats.draft, ats.revision = {}, 0
            request = urllib.request.Request(
                base + "/ui/api/chat",
                data=json.dumps({"message": "请申请 " + target}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(browser.open(request).read())
            assert response["actions"][0]["status"] == "accepted", seed
            task_id = response["actions"][0]["task_id"]
            assert worker.run_once(), seed
            observed = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{ats.server_port}/oracle"))
            assert observed["draft"] == expected, seed
            assert observed["revision"] >= 1, seed
            assert observed["submit_count"] == 0, seed
            task = queue.get(task_id)
            assert task["stage"] == "READY_TO_SUBMIT", seed
            assert task["spec"]["target_url"] == target, seed
            tasks[task_id] = target

        assert len(tasks) == 20
        reopened = TaskQueue(tmp_path / "react-ui-runtime")
        for task_id, original_target in tasks.items():
            task = reopened.get(task_id)
            assert task["stage"] == "READY_TO_SUBMIT", task_id
            assert task["spec"]["target_url"] == original_target, task_id
        assert ats.submit_count == 0
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()
