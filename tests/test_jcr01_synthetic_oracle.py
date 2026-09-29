"""A user-facing command must produce the expected server draft, not just READY."""
from __future__ import annotations

import http.cookiejar
import hashlib
import json
import threading
import urllib.request
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from executor.autonomy.manager import ManagerAction, ManagerController, ManagerDecision, ManagerTurn
from executor.autonomy.queue import TaskQueue
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker, outcome
from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.models import ApplicationStage


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _review_snapshot(adapter, plan, ats, *, account):
    """Read actual values from the fixture service, never from the fill plan."""
    actual = json.load(urllib.request.urlopen(
        f"http://127.0.0.1:{ats.server_port}/oracle"))
    draft = actual["draft"]
    return {
        "source": "server_readback",
        "target_sha256": _digest(plan.target_url),
        "draft_id_digest": _digest("syntheticats-draft"),
        "revision": actual["revision"],
        "account_verified": True,
        "account_identity_digest": _digest("identity.email:" + account.casefold()),
        "complete_pages": set(draft) == {"full_name", "email"},
        "complete_required": set(draft) == {"full_name", "email"},
        "save_status": "VERIFIED",
        "validation_error_count": 0, "hidden_required_count": 0,
        "unverified_default_count": 0,
        "document_epoch": _digest(adapter.page.url),
        "driver_version": "syntheticats-v1",
        "fields": [
            {"index": index, "field_id": field.field_id,
             "selector": field.selector, "required": field.required,
             "value": draft.get(field.field_id)}
            for index, field in enumerate(plan.fields)],
        "attachments": {}, "rows": {},
    }


class SyntheticATS(BaseHTTPRequestHandler):
    draft = None
    submit_count = 0

    def log_message(self, *_):
        pass

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/apply-multipage" or route == "/apply-second":
            second = route == "/apply-second"
            if second:
                self.server.page_two_reads += 1
            field = "email" if second else "full_name"
            label = "Email" if second else "Full name"
            retained = escape(self.server.draft.get(field, ""), quote=True)
            control = ("<button type='button'>Submit application</button>" if second
                       else "<button type='button' onclick=\"location.href='/apply-second'\">Next</button>")
            body = f'''<!doctype html><meta charset="utf-8"><body>
              <label>{label}<input id="{field}" name="{field}" value="{retained}" required></label>
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
        if route.startswith("/apply"):
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

            def observe_review_draft(self, plan):
                return _review_snapshot(self, plan, ats, account=golden["email"])

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
            safe_advance_certified = True

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

            def observe_review_draft(self, plan):
                return _review_snapshot(self, plan, ats, account="synthetic@example.test")

        monkeypatch.setattr("executor.application.adapter_for_url", lambda url: MultiPageAdapter(url))
        monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
        plan = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=2)
        assert plan.stage == ApplicationStage.READY_TO_SUBMIT
        assert plan.metadata["page_draft_receipts"] == [{
            "page_index": 0, "level": "server_readback", "revision": 1}]
        assert ats.draft == {"full_name": "Synthetic Person", "email": "synthetic@example.test"}
        assert ats.submit_count == 0
        # Re-enter both pages in fresh isolated browser contexts. Browser DOM
        # values must be restored from the independent server draft.
        with GenericWebAdapter(target) as first_reentry:
            assert first_reentry.page.locator("#full_name").input_value() == "Synthetic Person"
        with GenericWebAdapter(f"http://127.0.0.1:{ats.server_port}/apply-second") as second_reentry:
            assert second_reentry.page.locator("#email").input_value() == "synthetic@example.test"

        ats.draft, ats.revision, ats.page_two_reads, ats.accept_draft = {}, 0, 0, False
        blocked = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=2)
        assert blocked.stage == ApplicationStage.BLOCKED
        assert blocked.metadata["block_reason"] == "draft persistence unverified"
        assert blocked.metadata["draft_persistence_phase"] == "before_navigation"
        assert outcome(blocked) == ("BLOCKED", "draft_persistence_unverified")
        assert ats.page_two_reads == 0
        assert ats.submit_count == 0
    finally:
        ats.shutdown()
        ats.server_close()


def test_one_hundred_golden_ui_to_browser_tasks_have_independent_server_drafts(
    tmp_path, monkeypatch
):
    """Run the real local UI, queue and browser path for 100 distinct jobs."""
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.submit_count, ats.revision = {}, 0, 0
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    local = None
    try:
        profile = tmp_path / "golden-profile.json"
        expected = {}
        target = f"http://127.0.0.1:{ats.server_port}/apply?postId=golden-000"

        class GoldenAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, plan):
                actual = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                if (plan.target_url != target or actual["draft"] != expected
                        or actual["revision"] < 2):
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": actual["revision"]}

            def observe_review_draft(self, plan):
                return _review_snapshot(self, plan, ats, account=expected["email"])

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: GoldenAdapter(url))
        proposal = Proposal(target)
        queue = TaskQueue(tmp_path / "golden-runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(
            queue, worker, provider=proposal,
            settings={"profile_path": str(profile)},
        )
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()
        results = {}
        for seed in range(100):
            target = (f"http://127.0.0.1:{ats.server_port}/apply"
                      f"?postId=golden-{seed:03d}")
            proposal.target_url = target
            profile = tmp_path / f"golden-profile-{seed:03d}.json"
            manager.settings["profile_path"] = str(profile)
            expected = {
                "full_name": f"Synthetic Applicant {seed:03d}",
                "email": f"applicant{seed:03d}@example.test",
            }
            profile.write_text(json.dumps({"fields": {
                "identity.full_name": {"value": expected["full_name"],
                                       "confidence": 1.0},
                "identity.email": {"value": expected["email"],
                                   "confidence": 1.0},
            }}), encoding="utf-8")
            ats.draft, ats.revision = {}, 0
            request = urllib.request.Request(
                base + "/ui/api/chat",
                data=json.dumps({"message": "请申请 " + target}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(browser.open(request).read())
            assert response["actions"][0]["status"] == "accepted", seed
            task_id = response["actions"][0]["task_id"]
            assert task_id not in results
            assert worker.run_once(), seed
            actual = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{ats.server_port}/oracle"))
            assert actual["draft"] == expected, seed
            assert actual["revision"] >= 2, seed
            assert actual["submit_count"] == 0, seed
            task = queue.get(task_id)
            assert task["stage"] == "READY_TO_SUBMIT", seed
            assert task["spec"]["target_url"] == target, seed
            results[task_id] = (target, dict(actual["draft"]))
        assert len(results) == 100
        assert len({item[0] for item in results.values()}) == 100
        assert len({item[1]["email"] for item in results.values()}) == 100
        assert ats.submit_count == 0
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()



def test_two_queued_ui_tasks_keep_their_original_profiles_after_manager_changes(
    tmp_path, monkeypatch
):
    """A later UI choice cannot retarget an already queued applicant."""
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.submit_count, ats.revision = {}, 0, 0
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    local = None
    try:
        first_target = f"http://127.0.0.1:{ats.server_port}/apply?postId=queued-000"
        expected_by_target = {}

        class QueuedAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, plan):
                actual = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                expected = expected_by_target[plan.target_url]
                if actual["draft"] != expected or actual["revision"] < 2:
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": actual["revision"]}

            def observe_review_draft(self, plan):
                account = expected_by_target[plan.target_url]["email"]
                return _review_snapshot(self, plan, ats, account=account)

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: QueuedAdapter(url))
        proposal = Proposal(first_target)
        queue = TaskQueue(tmp_path / "queued-runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(
            queue, worker, provider=proposal,
            settings={"profile_path": str(tmp_path / "not-yet-selected.json")},
        )
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()

        task_targets = {}
        for seed in range(2):
            target = (f"http://127.0.0.1:{ats.server_port}/apply"
                      f"?postId=queued-{seed:03d}")
            profile = tmp_path / f"queued-profile-{seed:03d}.json"
            expected_by_target[target] = {
                "full_name": f"Queued Applicant {seed:03d}",
                "email": f"queued{seed:03d}@example.test",
            }
            profile.write_text(json.dumps({"fields": {
                "identity.full_name": {
                    "value": expected_by_target[target]["full_name"],
                    "confidence": 1.0},
                "identity.email": {
                    "value": expected_by_target[target]["email"],
                    "confidence": 1.0},
            }}), encoding="utf-8")
            manager.settings["profile_path"] = str(profile)
            proposal.target_url = target
            request = urllib.request.Request(
                base + "/ui/api/chat",
                data=json.dumps({"message": "请申请 " + target}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(browser.open(request).read())
            assert response["actions"][0]["status"] == "accepted", seed
            task_id = response["actions"][0]["task_id"]
            assert task_id not in task_targets
            task = queue.get(task_id)
            assert task["spec"]["target_url"] == target
            assert task["spec"]["profile_ref"] == str(profile)
            task_targets[task_id] = target

        # The manager's mutable current choice is deliberately invalid by the
        # time either queued task runs; each worker must use its task snapshot.
        manager.settings["profile_path"] = str(tmp_path / "missing-profile.json")
        remaining = set(task_targets)
        for _ in range(2):
            ats.draft, ats.revision = {}, 0
            assert worker.run_once()
            newly_ready = [
                task_id for task_id in remaining
                if queue.get(task_id)["stage"] == "READY_TO_SUBMIT"
            ]
            assert len(newly_ready) == 1
            task_id = newly_ready[0]
            target = task_targets[task_id]
            actual = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{ats.server_port}/oracle"))
            assert actual["draft"] == expected_by_target[target]
            assert actual["revision"] >= 2
            assert actual["submit_count"] == 0
            remaining.remove(task_id)

        assert not remaining
        reopened = TaskQueue(tmp_path / "queued-runtime")
        for task_id, target in task_targets.items():
            task = reopened.get(task_id)
            assert task["stage"] == "READY_TO_SUBMIT"
            assert task["spec"]["target_url"] == target
        assert ats.submit_count == 0
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()


def test_repeated_server_draft_corruption_blocks_twenty_ui_tasks(
    tmp_path, monkeypatch
):
    """A real browser fill cannot become READY after server draft divergence."""
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.submit_count, ats.revision = {}, 0, 0
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    local = None
    try:
        profile = tmp_path / "fault-profile.json"
        expected = {}
        target = f"http://127.0.0.1:{ats.server_port}/apply?postId=fault-000"
        corrupted = set()

        class CorruptDraftAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, plan):
                actual = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                if plan.target_url != target or actual["draft"] != expected:
                    return None
                # Simulate a conflicting committed server write after browser
                # filling. The independent draft oracle must reject this task.
                ats.draft["email"] = "conflict@example.test"
                ats.revision += 1
                corrupted.add(target)
                return None

            def observe_review_draft(self, plan):
                raise AssertionError("corrupt server draft must not reach READY review")

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: CorruptDraftAdapter(url))
        proposal = Proposal(target)
        queue = TaskQueue(tmp_path / "fault-runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(
            queue, worker, provider=proposal,
            settings={"profile_path": str(profile)},
        )
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()
        blocked = set()
        for seed in range(20):
            target = (f"http://127.0.0.1:{ats.server_port}/apply"
                      f"?postId=fault-{seed:03d}")
            proposal.target_url = target
            profile = tmp_path / f"fault-profile-{seed:03d}.json"
            manager.settings["profile_path"] = str(profile)
            expected = {
                "full_name": f"Synthetic Fault Applicant {seed:03d}",
                "email": f"fault{seed:03d}@example.test",
            }
            profile.write_text(json.dumps({"fields": {
                "identity.full_name": {"value": expected["full_name"],
                                       "confidence": 1.0},
                "identity.email": {"value": expected["email"],
                                   "confidence": 1.0},
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
            assert target in corrupted, seed
            actual = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{ats.server_port}/oracle"))
            assert actual["draft"]["full_name"] == expected["full_name"], seed
            assert actual["draft"]["email"] == "conflict@example.test", seed
            assert actual["submit_count"] == 0, seed
            task = queue.get(task_id)
            assert task["stage"] == "BLOCKED", seed
            assert task["blocker"] == "draft_persistence_unverified", seed
            blocked.add(task_id)
        assert len(blocked) == 20
        assert len(corrupted) == 20
        assert ats.submit_count == 0
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()


def test_repeated_acknowledged_but_unsaved_drafts_block_twenty_ui_tasks(
    tmp_path, monkeypatch
):
    """An HTTP 204 acknowledgement without a committed draft is never READY."""
    class SilentLossATS(SyntheticATS):
        def do_POST(self):
            if self.path == "/draft":
                self.server.draft_attempts += 1
            return super().do_POST()

    ats = ThreadingHTTPServer(("127.0.0.1", 0), SilentLossATS)
    ats.draft, ats.submit_count, ats.revision = {}, 0, 0
    ats.accept_draft, ats.draft_attempts = False, 0
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    local = None
    try:
        profile = tmp_path / "silent-loss-profile.json"
        target = f"http://127.0.0.1:{ats.server_port}/apply?postId=loss-000"
        checked = set()

        class SilentLossAdapter(GenericWebAdapter):
            def verify_draft_persistence(self, plan):
                actual = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                if (plan.target_url == target and actual["draft"] == {}
                        and actual["revision"] == 0
                        and ats.draft_attempts >= 2):
                    checked.add(target)
                return None

            def observe_review_draft(self, plan):
                raise AssertionError("uncommitted server draft must not reach READY review")

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: SilentLossAdapter(url))
        proposal = Proposal(target)
        queue = TaskQueue(tmp_path / "silent-loss-runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(
            queue, worker, provider=proposal,
            settings={"profile_path": str(profile)},
        )
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()
        blocked = set()
        for seed in range(20):
            target = (f"http://127.0.0.1:{ats.server_port}/apply"
                      f"?postId=loss-{seed:03d}")
            proposal.target_url = target
            profile = tmp_path / f"silent-loss-profile-{seed:03d}.json"
            manager.settings["profile_path"] = str(profile)
            profile.write_text(json.dumps({"fields": {
                "identity.full_name": {
                    "value": f"Synthetic Unsaved Applicant {seed:03d}",
                    "confidence": 1.0},
                "identity.email": {
                    "value": f"unsaved{seed:03d}@example.test",
                    "confidence": 1.0},
            }}), encoding="utf-8")
            ats.draft, ats.revision, ats.draft_attempts = {}, 0, 0
            request = urllib.request.Request(
                base + "/ui/api/chat",
                data=json.dumps({"message": "请申请 " + target}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(browser.open(request).read())
            assert response["actions"][0]["status"] == "accepted", seed
            task_id = response["actions"][0]["task_id"]
            assert worker.run_once(), seed
            assert target in checked, seed
            assert ats.draft_attempts >= 2, seed
            actual = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{ats.server_port}/oracle"))
            assert actual["draft"] == {}, seed
            assert actual["revision"] == 0, seed
            assert actual["submit_count"] == 0, seed
            task = queue.get(task_id)
            assert task["stage"] == "BLOCKED", seed
            assert task["blocker"] == "draft_persistence_unverified", seed
            blocked.add(task_id)
        assert len(blocked) == 20
        assert len(checked) == 20
        assert ats.submit_count == 0
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()


def test_twenty_distinct_multipage_ui_tasks_require_each_server_draft_receipt(
    tmp_path, monkeypatch
):
    """Second synthetic form mechanism: page advance and final draft readback."""
    ats = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticATS)
    ats.draft, ats.submit_count, ats.revision, ats.page_two_reads = {}, 0, 0, 0
    ats.accept_draft = True
    threading.Thread(target=ats.serve_forever, daemon=True).start()
    local = None
    try:
        profile = tmp_path / "multipage-profile.json"
        expected = {}
        target = f"http://127.0.0.1:{ats.server_port}/apply-multipage?postId=multi-000"

        class MultiPageGoldenAdapter(GenericWebAdapter):
            safe_advance_certified = True

            def verify_draft_persistence(self, plan):
                actual = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{ats.server_port}/oracle"))
                page_draft = {"full_name": expected["full_name"]}
                if self.page.url.endswith("/apply-second"):
                    page_draft["email"] = expected["email"]
                if (actual["draft"] != page_draft
                        or actual["revision"] < len(page_draft)):
                    return None
                return {"verified": True, "level": "server_readback",
                        "revision": actual["revision"]}

            def observe_review_draft(self, plan):
                return _review_snapshot(self, plan, ats, account=expected["email"])

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: MultiPageGoldenAdapter(url))
        proposal = Proposal(target)
        queue = TaskQueue(tmp_path / "multipage-runtime")
        worker = Worker(queue, settings={"deepseek": {"enabled": False}})
        manager = ManagerController(
            queue, worker, provider=proposal,
            settings={"profile_path": str(profile)},
        )
        supervisor = Supervisor(queue, worker=worker, manager=manager)
        local = create_server(supervisor, port=0)
        threading.Thread(target=local.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{local.server_port}"
        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        browser.open(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket()).read()
        ready = {}
        for seed in range(20):
            target = (f"http://127.0.0.1:{ats.server_port}/apply-multipage"
                      f"?postId=multi-{seed:03d}")
            proposal.target_url = target
            profile = tmp_path / f"multipage-profile-{seed:03d}.json"
            manager.settings["profile_path"] = str(profile)
            expected = {
                "full_name": f"Synthetic Multipage Applicant {seed:03d}",
                "email": f"multi{seed:03d}@example.test",
            }
            profile.write_text(json.dumps({"fields": {
                "identity.full_name": {"value": expected["full_name"],
                                       "confidence": 1.0},
                "identity.email": {"value": expected["email"],
                                   "confidence": 1.0},
            }}), encoding="utf-8")
            ats.draft, ats.revision, ats.page_two_reads = {}, 0, 0
            request = urllib.request.Request(
                base + "/ui/api/chat",
                data=json.dumps({"message": "请申请 " + target}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            response = json.loads(browser.open(request).read())
            assert response["actions"][0]["status"] == "accepted", seed
            task_id = response["actions"][0]["task_id"]
            assert worker.run_once(), seed
            actual = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{ats.server_port}/oracle"))
            assert actual["draft"] == expected, seed
            assert actual["revision"] >= 2, seed
            assert ats.page_two_reads >= 1, seed
            assert actual["submit_count"] == 0, seed
            task = queue.get(task_id)
            assert task["stage"] == "READY_TO_SUBMIT", seed
            assert task["spec"]["target_url"] == target, seed
            ready[task_id] = target
        assert len(ready) == 20
        # An independent browser draft must not overwrite the durable task
        # identity or READY state of any earlier applicant in the batch.
        reopened = TaskQueue(tmp_path / "multipage-runtime")
        for task_id, original_target in ready.items():
            retained = reopened.get(task_id)
            assert retained["stage"] == "READY_TO_SUBMIT", task_id
            assert retained["spec"]["target_url"] == original_target, task_id
        assert ats.submit_count == 0
    finally:
        if local is not None:
            local.shutdown()
            local.server_close()
        ats.shutdown()
        ats.server_close()
