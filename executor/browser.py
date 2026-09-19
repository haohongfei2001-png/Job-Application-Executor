from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP = "http://127.0.0.1:9333"
PROFILE = Path.home() / "Job-Application-Executor/chrome-profile"
BROWSER_MODE_ENV = "APPLICATION_EXECUTOR_BROWSER_MODE"
BLANK_URLS = {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}


def browser_mode() -> str:
    return os.getenv(BROWSER_MODE_ENV, "live").strip().casefold() or "live"


def _alive() -> bool:
    try:
        with urllib.request.urlopen(CDP + "/json/version", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_chrome(start_url: str = "about:blank") -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    if _alive():
        return
    subprocess.Popen(
        [
            CHROME,
            f"--user-data-dir={PROFILE}",
            "--remote-debugging-port=9333",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            start_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(40):
        if _alive():
            return
        time.sleep(0.5)
    raise RuntimeError("application Chrome did not expose CDP on 9333")


def _is_pytest_artifact(url: str) -> bool:
    if not url.startswith("file://"):
        return False
    return "/pytest-of-" in url or "/private/tmp/pytest-" in url or "/tmp/pytest-" in url


def cleanup_live_pages(ctx, target_url: str | None = None) -> dict[str, int]:
    """Remove only automation-owned junk; never close unrelated real job/application pages."""
    closed_test = 0
    closed_blank = 0
    closed_duplicate = 0

    pages = [page for page in ctx.pages if not page.is_closed()]
    for page in list(pages):
        if _is_pytest_artifact(page.url):
            try:
                page.close()
                closed_test += 1
            except Exception:
                pass

    pages = [page for page in ctx.pages if not page.is_closed()]
    if target_url:
        exact = [page for page in pages if page.url == target_url]
        for page in exact[:-1]:
            try:
                page.close()
                closed_duplicate += 1
            except Exception:
                pass
        for page in [p for p in ctx.pages if not p.is_closed() and p.url in BLANK_URLS]:
            try:
                page.close()
                closed_blank += 1
            except Exception:
                pass
    else:
        blanks = [page for page in pages if page.url in BLANK_URLS]
        for page in blanks[:-1]:
            try:
                page.close()
                closed_blank += 1
            except Exception:
                pass

    return {
        "closed_test_pages": closed_test,
        "closed_blank_pages": closed_blank,
        "closed_duplicate_pages": closed_duplicate,
    }


def _connect_isolated(target_url: str | None = None):
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        executable_path=CHROME,
        headless=True,
        args=[
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-component-update",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    ctx = browser.new_context()
    page = ctx.new_page()
    if target_url:
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    return pw, browser, ctx, page


def connect(target_url: str | None = None):
    if browser_mode() in {"test", "isolated", "headless"}:
        return _connect_isolated(target_url)

    ensure_chrome("about:blank")
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(CDP, no_defaults=True)
    ctx = browser.contexts[0]
    cleanup_live_pages(ctx, target_url)

    pages = [page for page in ctx.pages if not page.is_closed()]
    if target_url:
        exact = [page for page in pages if page.url == target_url]
        if exact:
            page = exact[-1]
        else:
            blanks = [page for page in pages if page.url in BLANK_URLS]
            page = blanks[-1] if blanks else ctx.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
    else:
        page = pages[-1] if pages else ctx.new_page()
    return pw, browser, ctx, page


def latest_page(ctx, fallback):
    pages = [page for page in ctx.pages if not page.is_closed()]
    return pages[-1] if pages else fallback
