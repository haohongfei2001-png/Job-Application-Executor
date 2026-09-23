from executor import browser as browser_module
from executor.browser import cleanup_live_pages
import os
from types import SimpleNamespace

import pytest


class FakePage:
    def __init__(self, url):
        self.url = url
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


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
