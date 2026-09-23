from executor import browser as browser_module
from executor.browser import cleanup_live_pages, owned_page_after_action
from executor.adapters import generic_web
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import Worker
from executor.models import ApplicationStage
import os
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright


class FakePage:
    def __init__(self, url, opener=None):
        self.url = url
        self.closed = False
        self._opener = opener

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def opener(self):
        return self._opener


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


def test_page_lineage_ignores_newer_unrelated_tab_and_follows_one_owned_popup():
    target = "https://jobs.example.test/apply"
    task_page = FakePage(target)
    unrelated = FakePage("https://jobs.example.test/other")
    ctx = FakeContext([task_page, unrelated])
    assert owned_page_after_action(ctx, task_page, target) is task_page

    popup = FakePage("https://jobs.example.test/apply/step-2", opener=task_page)
    ctx.pages.append(popup)
    assert owned_page_after_action(ctx, task_page, target) is popup
    task_page.close()
    assert owned_page_after_action(ctx, task_page, target) is popup


def test_page_lineage_fails_closed_on_ambiguous_or_cross_origin_successor():
    target = "https://jobs.example.test/apply"
    task_page = FakePage(target)
    ctx = FakeContext([task_page, FakePage(target + "/one", opener=task_page),
                       FakePage(target + "/two", opener=task_page)])
    with pytest.raises(RuntimeError, match="ambiguous"):
        owned_page_after_action(ctx, task_page, target)
    ctx.pages[-1].close()
    ctx.pages[1].url = "https://other.example.test/apply"
    with pytest.raises(RuntimeError, match="verified origin"):
        owned_page_after_action(ctx, task_page, target)
    ctx.pages[1].close()
    task_page.close()
    with pytest.raises(RuntimeError, match="closed"):
        owned_page_after_action(ctx, task_page, target)


def test_live_connect_never_adopts_or_closes_an_unbound_tab(monkeypatch):
    target = "https://jobs.example.test/apply"
    unrelated = FakePage("about:blank")

    class Browser:
        def __init__(self):
            self.contexts = [FakeContext([unrelated])]

        def connect_over_cdp(self, *_args, **_kwargs):
            return self

    class Playwright:
        def __init__(self, browser):
            self.chromium = browser
            self.stopped = False

        def start(self):
            return self

        def stop(self):
            self.stopped = True

    browser = Browser()
    pw = Playwright(browser)
    fresh = FakePage("about:blank")
    fresh.goto = lambda url, **_kwargs: setattr(fresh, "url", url)
    browser.contexts[0].new_page = lambda: browser.contexts[0].pages.append(fresh) or fresh
    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser_module, "owned_cdp_session", lambda: True)
    monkeypatch.setattr(browser_module, "sync_playwright", lambda: pw)
    _, _, _, page = browser_module.connect(target, existing_only=True)
    assert page is fresh
    assert unrelated.is_closed() is False
    assert unrelated.url == "about:blank"

    with pytest.raises(browser_module.BrowserOwnershipError, match="not bound"):
        browser_module.connect(target, existing_only=True)
    assert fresh.is_closed() is False
    assert pw.stopped is True


def test_bound_live_connect_selects_only_the_recorded_tab(monkeypatch):
    target = "https://jobs.example.test/apply"
    owned = FakePage(target + "/draft")
    unrelated = FakePage(target + "/other")
    owned.target_id = "ownedtarget123"
    unrelated.target_id = "othertarget123"
    ctx = FakeContext([owned, unrelated])
    ctx.new_page = lambda: (_ for _ in ()).throw(AssertionError("must reuse bound tab"))

    class Browser:
        contexts = [ctx]

        def connect_over_cdp(self, *_args, **_kwargs):
            return self

    class Playwright:
        chromium = Browser()

        def start(self):
            return self

        def stop(self):
            pass

    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser_module, "owned_cdp_session", lambda: True)
    monkeypatch.setattr(browser_module, "sync_playwright", lambda: Playwright())
    monkeypatch.setattr(browser_module, "page_target_id", lambda context, page: page.target_id)
    binding = {"process_epoch": "epoch12345", "target_id": owned.target_id}
    _, _, _, page = browser_module.connect(target, existing_only=True,
        task_binding=binding, session_epoch="epoch12345")
    assert page is owned
    assert unrelated.is_closed() is False
    with pytest.raises(browser_module.BrowserOwnershipError, match="epoch changed"):
        browser_module.connect(target, existing_only=True,
            task_binding=binding, session_epoch="epoch99999")
    monkeypatch.setattr(browser_module, "page_document_epoch", lambda page: "100.0")
    with pytest.raises(browser_module.BrowserOwnershipError, match="document changed"):
        browser_module.connect(target, existing_only=True,
            task_binding={**binding, "document_epoch": "200.0"}, session_epoch="epoch12345")
    owned.url = "https://other.example.test/draft"
    with pytest.raises(browser_module.BrowserOwnershipError, match="verified origin"):
        browser_module.connect(target, existing_only=True,
            task_binding=binding, session_epoch="epoch12345")


def test_browser_binding_persists_and_fences_popup_transition(tmp_path):
    runtime = tmp_path / "runtime"
    queue = TaskQueue(runtime)
    created = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=bind-1",
        profile_ref=str(tmp_path / "profile.json"), live_authorized=True,
    ))
    tid = created["task_id"]
    claimed = queue.claim("browser-worker")
    owner = claimed["owner"]
    queue.bind_browser_page(tid, owner, "epoch12345", "target12345")
    assert TaskQueue(runtime).browser_binding(tid)["target_id"] == "target12345"
    queue.record_browser_document(tid, owner, "epoch12345", "target12345", "123456.78")
    assert TaskQueue(runtime).browser_binding(tid)["document_epoch"] == "123456.78"
    queue.bind_browser_page(tid, owner, "epoch12345", "popup12345",
                            previous_target_id="target12345")
    assert queue.browser_binding(tid)["target_id"] == "popup12345"
    assert queue.browser_binding(tid)["document_epoch"] is None
    with pytest.raises(RuntimeError, match="binding changed"):
        queue.record_browser_document(tid, owner, "epoch12345", "target12345", "123456.78")
    with pytest.raises(RuntimeError, match="changed"):
        queue.bind_browser_page(tid, owner, "epoch12345", "other12345",
                                previous_target_id="target12345")
    with pytest.raises(RuntimeError, match="lease lost"):
        queue.bind_browser_page(tid, "old-worker", "epoch12345", "other12345",
                                previous_target_id="popup12345")


def test_adapter_records_owned_popup_transition(monkeypatch):
    target = "https://jobs.example.test/apply"
    parent = FakePage(target)
    popup = FakePage(target + "/draft", opener=parent)
    popup.target_id = "popup12345"
    adapter = generic_web.GenericWebAdapter(target)
    adapter.ctx = FakeContext([parent, popup])
    adapter.page = parent
    adapter.bound_target_id = "parent12345"
    adapter.document_epoch = "100.0"
    recorded = []
    adapter.browser_binding_set = lambda target_id, previous_target_id=None: recorded.append(
        (target_id, previous_target_id)
    )
    monkeypatch.setattr(generic_web, "page_target_id", lambda ctx, page: page.target_id)
    monkeypatch.setattr(generic_web, "page_document_epoch", lambda page: "200.0")
    adapter.browser_document_set = lambda target_id, epoch: recorded.append((target_id, epoch))
    adapter._adopt_owned_page()
    assert adapter.page is popup
    assert recorded == [("popup12345", "parent12345"), ("popup12345", "200.0")]


def test_adapter_fences_stale_document_before_write(monkeypatch):
    target = "https://jobs.example.test/apply"
    page = FakePage(target)
    page.target_id = "target12345"
    adapter = generic_web.GenericWebAdapter(target)
    adapter.ctx = FakeContext([page])
    adapter.page = page
    adapter.bound_target_id = "target12345"
    adapter.document_epoch = "100.0"
    observed = ["100.0"]
    monkeypatch.setattr(generic_web, "page_target_id", lambda ctx, page: page.target_id)
    monkeypatch.setattr(generic_web, "page_document_epoch", lambda page: observed[0])
    adapter.verify_document()
    observed[0] = "200.0"
    with pytest.raises(browser_module.BrowserOwnershipError, match="document changed"):
        adapter.verify_document()


def test_live_worker_persists_browser_binding_before_runner_return(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "runtime")
    created = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=bind-2",
        profile_ref=str(tmp_path / "profile.json"), live_authorized=True,
    ))
    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser_module, "owned_cdp_fingerprint", lambda: "epoch12345")

    class SyntheticRunner:
        def __init__(self, *_args, **_kwargs):
            self.plan = SimpleNamespace(
                metadata={}, stage=ApplicationStage.BLOCKED, unresolved_fields=[],
            )

        def run(self):
            assert self.browser_binding_get() is None
            self.browser_binding_set("target12345")
            self.browser_document_set("target12345", "123456.78")
            return self.plan

    assert Worker(queue, runner_factory=SyntheticRunner,
                  settings={"deepseek": {"enabled": False}}).run_once() is True
    binding = TaskQueue(queue.root).browser_binding(created["task_id"])
    assert binding["process_epoch"] == "epoch12345"
    assert binding["target_id"] == "target12345"
    assert binding["document_epoch"] == "123456.78"


def test_readonly_observation_never_starts_or_replays_browser_work(monkeypatch):
    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser_module, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("no tab may open without a proven owned process")
    ))
    assert browser_module.observe_bound_draft("https://jobs.example.test/apply", None)["status"] == "NO_TASK_BINDING"
    assert browser_module.observe_bound_draft("https://jobs.example.test/apply",
        {"process_epoch": "epoch12345", "target_id": "target12345"})["status"] == "DOCUMENT_IDENTITY_UNRECORDED"
    monkeypatch.setattr(browser_module, "owned_cdp_fingerprint", lambda: None)
    result = browser_module.observe_bound_draft("https://jobs.example.test/apply",
        {"process_epoch": "epoch12345", "target_id": "target12345", "document_epoch": "100.0"})
    assert result["status"] == "OWNED_SESSION_UNAVAILABLE"
    assert result["replay_allowed"] is False


def test_readonly_observation_reports_only_page_continuity(monkeypatch):
    target = "https://jobs.example.test/apply"
    page = FakePage(target)
    page.target_id = "target12345"
    page.locator = lambda selector: SimpleNamespace(count=lambda: 3)
    stopped = []
    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser_module, "owned_cdp_fingerprint", lambda: "epoch12345")
    monkeypatch.setattr(browser_module, "connect", lambda *args, **kwargs: (
        SimpleNamespace(stop=lambda: stopped.append(True)), None, FakeContext([page]), page,
    ))
    monkeypatch.setattr(browser_module, "page_target_id", lambda ctx, page: page.target_id)
    monkeypatch.setattr(browser_module, "page_document_epoch", lambda page: "100.0")
    result = browser_module.observe_bound_draft(target,
        {"process_epoch": "epoch12345", "target_id": "target12345", "document_epoch": "100.0"})
    assert result == {
        "status": "BOUND_DOCUMENT_OBSERVED", "visible_field_count": 3,
        "draft_identity_verified": False, "write_outcome_verified": False,
        "replay_allowed": False,
    }
    assert stopped == [True]


def test_real_headless_cdp_binding_observation_and_browser_loss(tmp_path, monkeypatch):
    draft = {"value": "", "writes": 0, "submits": 0}

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = (f'<label>Name<input id="name" value="{draft["value"]}"></label>'
                    '<script>document.querySelector("#name").oninput=e=>'
                    'fetch("/save",{method:"POST",body:e.target.value})</script>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/save":
                draft["submits"] += 1
                self.send_error(404)
                return
            draft["value"] = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 100)).decode()
            draft["writes"] += 1
            self.send_response(204)
            self.end_headers()

    fixture = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        cdp_port = reservation.getsockname()[1]
    target = f"http://127.0.0.1:{fixture.server_port}/apply"
    clock = [100.0]
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: clock[0])
    task = queue.enqueue(TaskSpec(
        company="SyntheticATS", role="Engineer", target_url=target,
        profile_ref=str(tmp_path / "synthetic-profile.json"), live_authorized=True,
    ))
    claimed = queue.claim("first-worker", lease_seconds=1)
    attempt = queue.begin_run_attempt(task["task_id"], claimed["owner"])
    with sync_playwright() as pw:
        executable = pw.chromium.executable_path
    args = [executable, "--headless=new", "--no-sandbox", "--disable-gpu",
            f"--user-data-dir={tmp_path / 'isolated-cdp-profile'}",
            f"--remote-debugging-port={cdp_port}",
            "--remote-debugging-address=127.0.0.1", "--no-first-run", "about:blank"]
    def start_headless():
        return subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    headless = start_headless()
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
        monkeypatch.setattr(browser_module, "CDP", f"http://127.0.0.1:{cdp_port}")
        monkeypatch.setattr(browser_module, "owned_cdp_session", lambda: True)
        epoch = ["fixtureepoch123"]
        monkeypatch.setattr(browser_module, "owned_cdp_fingerprint", lambda: epoch[0])
        bound = []
        client, _, ctx, page = browser_module.connect(
            target, existing_only=True, session_epoch="fixtureepoch123",
            bind_page=lambda target_id: (
                queue.bind_browser_page(task["task_id"], claimed["owner"], "fixtureepoch123", target_id),
                bound.append(target_id),
            ),
        )
        try:
            page.locator("#name").fill("Synthetic")
            for _ in range(50):
                if draft["writes"]:
                    break
                time.sleep(.05)
            assert draft == {"value": "Synthetic", "writes": 1, "submits": 0}
            binding = {
                "process_epoch": "fixtureepoch123", "target_id": bound[0],
                "document_epoch": browser_module.page_document_epoch(page),
            }
            queue.record_browser_document(task["task_id"], claimed["owner"],
                                          "fixtureepoch123", bound[0], binding["document_epoch"])
        finally:
            client.stop()
        observed = browser_module.observe_bound_draft(target, binding)
        assert observed["status"] == "BOUND_DOCUMENT_OBSERVED"
        assert observed["replay_allowed"] is False
        assert draft == {"value": "Synthetic", "writes": 1, "submits": 0}
        client, _, ctx, page = browser_module.connect(
            target, existing_only=True, task_binding=binding,
            session_epoch="fixtureepoch123",
        )
        try:
            assert page.locator("#name").input_value() == "Synthetic"
            assert browser_module.page_target_id(ctx, page) == bound[0]
        finally:
            client.stop()
        headless.terminate()
        headless.wait(timeout=5)
        clock[0] += 2
        replacement = TaskQueue(queue.root, clock=lambda: clock[0])
        assert replacement.claim("replacement-worker") is None
        blocked = replacement.get(task["task_id"])
        assert blocked["task_id"] == task["task_id"]
        assert blocked["stage"] == "BLOCKED"
        assert blocked["blocker"] == "unknown_outcome"
        assert replacement.run_attempts(task["task_id"])[0]["attempt_id"] == attempt
        assert replacement.run_attempts(task["task_id"])[0]["outcome"] == "UNKNOWN_OUTCOME"
        lost = browser_module.observe_bound_draft(target, binding)
        assert lost["replay_allowed"] is False
        assert lost["status"] != "BOUND_DOCUMENT_OBSERVED"
        headless = start_headless()
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        epoch[0] = "fixtureepoch456"
        restarted = browser_module.observe_bound_draft(target, binding)
        assert restarted["status"] == "PROCESS_EPOCH_CHANGED"
        assert draft == {"value": "Synthetic", "writes": 1, "submits": 0}
    finally:
        if headless.poll() is None:
            headless.terminate()
            headless.wait(timeout=5)
        fixture.shutdown()
        fixture.server_close()
        thread.join()


def test_second_live_run_waits_for_tab_reconciliation(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "runtime")
    task = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=live-2",
        profile_ref=str(tmp_path / "profile.json"), live_authorized=True,
    ))
    claimed = queue.claim("first")
    attempt = queue.begin_run_attempt(task["task_id"], claimed["owner"])
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    queue.checkpoint(task["task_id"], claimed["owner"], "NEEDS_USER_ACTION",
                     blocker="security_challenge", release=True)
    queue.resume(task["task_id"])
    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser_module, "owned_cdp_fingerprint", lambda: "synthetic-epoch")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unbound live task must not create a second tab")
    assert Worker(queue, runner_factory=forbidden).run_once() is True
    blocked = queue.get(task["task_id"])
    assert blocked["stage"] == "BLOCKED"
    assert blocked["blocker"] == "browser_ownership_unknown"


def test_real_headless_popup_lineage_does_not_adopt_unrelated_tab(monkeypatch):
    class Site(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = (
                b'<button id="open" onclick="window.open(\'/child\', \'_blank\')">Open</button>'
                if self.path == "/apply" else b"<p>Owned child</p>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")
    target = f"http://127.0.0.1:{server.server_port}/apply"
    pw = browser = None
    try:
        pw, browser, ctx, page = browser_module.connect(target)
        initial_id = browser_module.page_target_id(ctx, page)
        assert initial_id == browser_module.page_target_id(ctx, page)
        assert browser_module.page_document_epoch(page) == browser_module.page_document_epoch(page)
        with page.expect_popup() as opened:
            page.locator("#open").click()
        popup = opened.value
        popup.wait_for_load_state()
        assert browser_module.page_target_id(ctx, popup) != initial_id
        unrelated = ctx.new_page()
        unrelated.goto(target)
        assert owned_page_after_action(ctx, page, target) is popup
    finally:
        if browser is not None:
            browser.close()
        if pw is not None:
            pw.stop()
        server.shutdown()
        server.server_close()
        thread.join()


def test_unknown_browser_owner_blocks_replay_even_after_pause(tmp_path):
    class UnknownOutcomeRunner:
        def __init__(self, *_args, **_kwargs):
            self.plan = SimpleNamespace(metadata={})

        def run(self):
            raise browser_module.BrowserOwnershipError("synthetic lost popup")

    queue = TaskQueue(tmp_path / "runtime")
    created = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=owner-1",
        profile_ref=str(tmp_path / "synthetic-profile.json"),
    ))
    worker = Worker(queue, runner_factory=UnknownOutcomeRunner,
                    settings={"deepseek": {"enabled": False}})
    assert worker.run_once()
    blocked = queue.get(created["task_id"])
    assert blocked["stage"] == "BLOCKED"
    assert blocked["blocker"] == "browser_ownership_unknown"
    assert queue.run_attempts(created["task_id"])[0]["outcome"] == "UNKNOWN_OUTCOME"
    assert worker.run_once() is False
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.resume(created["task_id"])
    queue.pause(created["task_id"])
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.resume(created["task_id"])


def test_worker_fences_changed_owned_browser_process_epoch(tmp_path, monkeypatch):
    class Runner:
        def __init__(self, *_args, **kwargs):
            self.guard = kwargs["guard"]
            self.plan = SimpleNamespace(metadata={})

        def run(self):
            self.guard()
            raise AssertionError("write would have followed changed browser")

    monkeypatch.setattr(browser_module, "browser_mode", lambda: "live")
    epochs = iter(["first-process", "new-process"])
    monkeypatch.setattr(browser_module, "owned_cdp_fingerprint", lambda: next(epochs))
    queue = TaskQueue(tmp_path / "runtime")
    created = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=epoch-1",
        profile_ref=str(tmp_path / "synthetic-profile.json"),
        live_authorized=True,
    ))
    worker = Worker(queue, runner_factory=Runner, settings={"deepseek": {"enabled": False}})
    assert worker.run_once()
    assert queue.get(created["task_id"])["blocker"] == "browser_ownership_unknown"


def test_expired_lease_with_unanswered_external_attempt_does_not_replay(tmp_path):
    now = [100.0]
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: now[0])
    created = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=crash-1",
        profile_ref=str(tmp_path / "synthetic-profile.json"),
    ))
    claimed = queue.claim("crashing-worker", lease_seconds=10)
    attempt_id = queue.begin_run_attempt(created["task_id"], claimed["owner"])
    assert queue.run_attempts(created["task_id"])[0]["outcome"] == "ATTEMPTED"
    now[0] += 11
    recovered = TaskQueue(queue.root, clock=lambda: now[0])
    assert recovered.claim("replacement-worker") is None
    blocked = recovered.get(created["task_id"])
    assert blocked["stage"] == "BLOCKED"
    assert blocked["blocker"] == "unknown_outcome"
    assert recovered.run_attempts(created["task_id"])[0]["attempt_id"] == attempt_id
    assert recovered.run_attempts(created["task_id"])[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(ValueError, match="read-only reconciliation"):
        recovered.resume(created["task_id"])


def test_cleanup_live_pages_only_closes_owned_junk():
    target = "https://jobs.example.test/apply"
    duplicate_old = FakePage(target)
    duplicate_new = FakePage(target)
    other_real = FakePage("https://jobs.example.test/other")
    pytest_page = FakePage("file:///private/tmp/pytest-of-user/test_form0/form.html")
    blank = FakePage("about:blank")
    ctx = FakeContext([duplicate_old, other_real, pytest_page, blank, duplicate_new])

    result = cleanup_live_pages(ctx, target)

    assert duplicate_old.closed is True
    assert duplicate_new.closed is False
    assert pytest_page.closed is True
    assert blank.closed is True
    assert other_real.closed is False
    assert result == {
        "closed_test_pages": 1,
        "closed_blank_pages": 1,
        "closed_duplicate_pages": 1,
    }


def test_isolated_connect_never_starts_live_profile(tmp_path, monkeypatch):
    html = tmp_path / "isolated.html"
    html.write_text("<!doctype html><title>isolated-test</title><p>ok</p>", encoding="utf-8")
    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("isolated mode must not call ensure_chrome")

    monkeypatch.setattr(browser_module, "ensure_chrome", forbidden)
    pw, browser, _ctx, page = browser_module.connect(html.as_uri())
    try:
        assert page.title() == "isolated-test"
        assert page.url == html.as_uri()
    finally:
        browser.close()
        pw.stop()


def test_cdp_listener_requires_own_process_and_dedicated_private_profile(tmp_path, monkeypatch):
    profile = tmp_path / "dedicated-profile"
    profile.mkdir(mode=0o700)
    monkeypatch.setattr(browser_module, "PROFILE", profile)
    monkeypatch.setattr(browser_module, "CHROME", "/synthetic/Chrome")
    monkeypatch.setattr(browser_module, "_alive", lambda: True)
    command = (f"{os.getuid()} /synthetic/Chrome --user-data-dir={profile} "
               "--remote-debugging-port=9333 --remote-debugging-address=127.0.0.1")

    def fake_run(args, **_):
        if args[0] == "lsof":
            return SimpleNamespace(stdout="12345\n")
        return SimpleNamespace(stdout=command)

    monkeypatch.setattr(browser_module.subprocess, "run", fake_run)
    assert browser_module.owned_cdp_session()
    command = command.replace(str(profile), str(tmp_path / "other-profile"))
    assert not browser_module.owned_cdp_session()
    with pytest.raises(RuntimeError, match="does not belong"):
        browser_module.ensure_chrome()
    command = command.replace(str(tmp_path / "other-profile"), str(profile))
    profile.chmod(0o755)
    assert not browser_module.owned_cdp_session()


def test_owned_browser_fingerprint_changes_with_process_epoch(tmp_path, monkeypatch):
    profile = tmp_path / "dedicated-profile"
    profile.mkdir(mode=0o700)
    monkeypatch.setattr(browser_module, "PROFILE", profile)
    monkeypatch.setattr(browser_module, "owned_cdp_session", lambda: True)
    started = ["Wed Sep 23 17:00:00 2026"]

    def fake_run(args, **_):
        if args[0] == "lsof":
            return SimpleNamespace(stdout="12345\n")
        return SimpleNamespace(stdout=started[0] + "\n")

    monkeypatch.setattr(browser_module.subprocess, "run", fake_run)
    first = browser_module.owned_cdp_fingerprint()
    assert first and len(first) == 64
    started[0] = "Wed Sep 23 17:01:00 2026"
    assert browser_module.owned_cdp_fingerprint() != first
