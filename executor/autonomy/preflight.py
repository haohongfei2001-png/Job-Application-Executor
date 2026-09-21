from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import browser
from ..profile import load_profile
from ..settings import load_settings
from .manager import DeepSeekManagerProvider


def collect_live_preflight(
    settings: dict[str, Any] | None = None,
    *,
    supervisor_running: bool,
    browser_mode_value: str | None = None,
    chrome_exists: bool | None = None,
    cdp_alive: bool | None = None,
    deepseek_available: bool | None = None,
    profile_exists: bool | None = None,
    profile_loadable: bool | None = None,
) -> dict[str, Any]:
    """Read-only readiness check for a real local application E2E run."""
    settings = settings or load_settings()
    mode = (browser_mode_value or browser.browser_mode()).strip().casefold()
    live_mode = mode not in {"test", "isolated", "headless"}

    profile_ref = settings.get("profile_path")
    profile_configured = isinstance(profile_ref, str) and bool(profile_ref.strip())
    if profile_exists is None:
        profile_exists = bool(
            profile_configured and Path(profile_ref).expanduser().is_file()
        )
    if profile_loadable is None:
        profile_loadable = False
        if profile_exists:
            try:
                load_profile(profile_ref)
                profile_loadable = True
            except Exception:
                profile_loadable = False

    if chrome_exists is None:
        chrome_exists = Path(browser.CHROME).is_file()
    if cdp_alive is None:
        cdp_alive = browser._alive()

    if deepseek_available is None:
        try:
            deepseek_available = DeepSeekManagerProvider(settings).available
        except Exception:
            deepseek_available = False

    checks = {
        "live_browser_mode": bool(live_mode),
        "chrome_installed": bool(chrome_exists),
        "existing_cdp_session": bool(cdp_alive),
        "profile_configured": bool(profile_configured),
        "profile_exists": bool(profile_exists),
        "profile_loadable": bool(profile_loadable),
        "deepseek_available": bool(deepseek_available),
        "supervisor_running": bool(supervisor_running),
    }
    remediation = [
        code
        for code, passed in (
            ("use_live_browser_mode", checks["live_browser_mode"]),
            ("install_google_chrome", checks["chrome_installed"]),
            ("start_dedicated_chrome_cdp", checks["existing_cdp_session"]),
            ("configure_profile_path", checks["profile_configured"]),
            ("restore_profile_file", checks["profile_exists"]),
            ("repair_profile_file", checks["profile_loadable"]),
            ("configure_deepseek_key", checks["deepseek_available"]),
            ("start_supervisor", checks["supervisor_running"]),
        )
        if not passed
    ]
    ready = all(checks.values())
    return {
        "ok": ready,
        "ready_for_live_e2e": ready,
        "checks": checks,
        "remediation": remediation,
        "final_click_actor": "user",
        "submit_capability": False,
    }
