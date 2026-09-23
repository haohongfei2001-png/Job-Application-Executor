from __future__ import annotations

import os
import hashlib
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP = "http://127.0.0.1:9333"
PROFILE = Path.home() / "Job-Application-Executor/chrome-profile"
BROWSER_MODE_ENV = "APPLICATION_EXECUTOR_BROWSER_MODE"
BLANK_URLS = {"about:blank", "chrome://newtab/", "chrome://new-tab-page/"}


class BrowserOwnershipError(RuntimeError):
    """The task's page or successor cannot be proven; never replay writes."""


def browser_mode() -> str:
    return os.getenv(BROWSER_MODE_ENV, "live").strip().casefold() or "live"


def _alive() -> bool:
    try:
        with urllib.request.urlopen(CDP + "/json/version", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def owned_cdp_session() -> bool:
    """Fail closed unless the listener is our dedicated Chrome process/profile."""
    if not _alive() or PROFILE.is_symlink() or not PROFILE.is_dir():
        return False
    try:
        if PROFILE.stat().st_mode & 0o077:
            return False
        listeners = subprocess.run(
            ["lsof", "-nP", "-t", "-iTCP:9333", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.splitlines()
        pids = {line.strip() for line in listeners if line.strip().isdigit()}
        if len(pids) != 1:
            return False
        pid = pids.pop()
        info = subprocess.run(
            ["ps", "-ww", "-p", pid, "-o", "uid=", "-o", "command="],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        uid, _, command = info.partition(" ")
        required = (
            CHROME in command
            and uid.strip() == str(os.getuid())
            and re.search(r"(?:^|\s)--user-data-dir=" + re.escape(str(PROFILE)) + r"(?:\s|$)", command)
            and re.search(r"(?:^|\s)--remote-debugging-port=9333(?:\s|$)", command)
            and re.search(r"(?:^|\s)--remote-debugging-address=127\.0\.0\.1(?:\s|$)", command)
        )
        return bool(required)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def owned_cdp_fingerprint() -> str | None:
    """Bind one worker lease to the specific owned browser process epoch."""
    if not owned_cdp_session():
        return None
    try:
        listeners = subprocess.run(
            ["lsof", "-nP", "-t", "-iTCP:9333", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.splitlines()
        pids = {line.strip() for line in listeners if line.strip().isdigit()}
        if len(pids) != 1:
            return None
        pid = pids.pop()
        started = subprocess.run(
            ["ps", "-ww", "-p", pid, "-o", "lstart="],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        if not started:
            return None
        material = f"{pid}:{started}:{PROFILE.stat().st_ino}"
        return hashlib.sha256(material.encode()).hexdigest()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def ensure_chrome(start_url: str = "about:blank") -> None:
    if PROFILE.is_symlink():
        raise RuntimeError("application Chrome profile is not owned")
    PROFILE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROFILE.chmod(0o700)
    if _alive():
        if owned_cdp_session():
            return
        raise RuntimeError("CDP listener does not belong to the dedicated application profile")
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
        if owned_cdp_session():
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
    try:
        launch_kwargs = {
            "headless": True,
            "args": [
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-component-update",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        # Test runs always use the bundled headless browser. The installed Chrome
        # belongs to the live CDP path and may carry user policy or permissions.
        browser = pw.chromium.launch(**launch_kwargs)
        ctx = browser.new_context()
        page = ctx.new_page()
        if target_url:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        return pw, browser, ctx, page
    except BaseException:
        pw.stop()
        raise



def page_target_id(ctx, page) -> str:
    session = ctx.new_cdp_session(page)
    try:
        target_id = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", target_id):
            raise BrowserOwnershipError("invalid browser target ID")
        return target_id
    finally:
        session.detach()


def page_document_epoch(page) -> str:
    try:
        value = page.evaluate("() => String(performance.timeOrigin)")
    except Exception:
        raise BrowserOwnershipError("task document observation unavailable") from None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise BrowserOwnershipError("task document identity unavailable")
    return value


def connect(
    target_url: str | None = None, *, existing_only: bool = False,
    task_binding: dict | None = None, bind_page=None, session_epoch: str | None = None,
):
    if browser_mode() in {"test", "isolated", "headless"}:
        return _connect_isolated(target_url)

    if existing_only:
        if not owned_cdp_session():
            raise RuntimeError("owned CDP session unavailable")
    else:
        ensure_chrome("about:blank")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP, no_defaults=True)
        ctx = browser.contexts[0]
        pages = [page for page in ctx.pages if not page.is_closed()]
        if task_binding:
            if not session_epoch or task_binding["process_epoch"] != session_epoch:
                raise BrowserOwnershipError("task browser process epoch changed")
            matching = [page for page in pages if page_target_id(ctx, page) == task_binding["target_id"]]
            if len(matching) != 1:
                raise BrowserOwnershipError("bound task tab is unavailable or ambiguous")
            page = matching[0]
            expected, observed = urlsplit(target_url or ""), urlsplit(page.url)
            if expected.scheme == "file":
                same_origin = observed.scheme == "file"
            else:
                same_origin = observed.scheme == expected.scheme and observed.netloc == expected.netloc
            if not same_origin:
                raise BrowserOwnershipError("bound task tab left verified origin")
            if task_binding.get("document_epoch") and page_document_epoch(page) != task_binding["document_epoch"]:
                raise BrowserOwnershipError("bound task document changed")
        elif target_url:
            if any(page.url == target_url for page in pages):
                raise BrowserOwnershipError("existing target tab is not bound to this task")
            # Bind before navigation. A crash after this intent remains UNKNOWN
            # in the run journal; another worker will not create a second tab.
            page = ctx.new_page()
            if bind_page is not None:
                if not session_epoch:
                    raise BrowserOwnershipError("task browser epoch unavailable")
                bind_page(page_target_id(ctx, page))
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        else:
            page = pages[-1] if pages else ctx.new_page()
        return pw, browser, ctx, page
    except BaseException:
        pw.stop()
        raise


def latest_page(ctx, fallback):
    pages = [page for page in ctx.pages if not page.is_closed()]
    return pages[-1] if pages else fallback


def owned_page_after_action(ctx, current, target_url: str):
    """Follow only one popup opened by the task page, never the global last tab.

    The JCR-03 target contract will add verified cross-origin navigation. Until
    then, a different host is an observation error and cannot receive writes.
    """
    expected = urlsplit(target_url)
    children = []
    for page in ctx.pages:
        if page is current or page.is_closed():
            continue
        try:
            if page.opener() is current:
                children.append(page)
        except Exception:
            continue
    if len(children) > 1:
        raise BrowserOwnershipError("multiple task popups; page ownership ambiguous")
    if children:
        selected = children[0]
    elif current.is_closed():
        raise BrowserOwnershipError("task page closed without an owned successor")
    else:
        selected = current
    observed = urlsplit(selected.url)
    if expected.scheme == "file":
        if observed.scheme != "file":
            raise BrowserOwnershipError("task page left its local fixture origin")
    elif observed.scheme != expected.scheme or observed.netloc != expected.netloc:
        raise BrowserOwnershipError("task page left its verified origin")
    return selected
