from executor import browser as browser_module
from executor.browser import cleanup_live_pages, owned_page_after_action
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import Worker
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest


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
        with page.expect_popup() as opened:
            page.locator("#open").click()
        popup = opened.value
        popup.wait_for_load_state()
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
