"""Private projection UI: explicit reveal, cleared values and interrupted reads."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from executor.preparation.qiyunfang import map_routine_fields
from test_dashboard_diagnostics_browser import preparation_ui

CANARY = "PRIVATE_REVIEW_CANARY"


def payload():
    return {"mode": "PRIVATE_MAPPING_REVIEW", "state": "AWAITING_LIVE_PREFLIGHT", "task_id": "prep-a", "task_revision": 7,
        "profile_version": "a" * 64, "resume_version": None,
        "contract_version": "qiyunfang-public-form-2026-10-01-v1", "observed_at": "2026-10-01",
        "proposals": map_routine_fields({"fields": {"identity.full_name": {"value": CANARY}}}),
        "capabilities": {"live_write": False, "submit": False, "account_verified": False, "server_draft_verified": False}}


def open_review(page, observed, *, hold=False, result=None):
    pending = []
    def route(route):
        observed["requests"].append((route.request.method, route.request.url, route.request.post_data))
        if hold:
            pending.append(route)
            page.evaluate("() => {window.__privateReviewAdmitted=true}")
        else: route.fulfill(status=200, content_type="application/json", body=json.dumps(result or payload()))
    page.route("**/ui/api/preparation-review?*", route)
    page.locator('[data-task-preparation][data-task="prep-a"]').click()
    expect(page.locator('#preparation-review-open')).to_be_visible()
    assert CANARY not in page.content()
    assert not any('/preparation-review?' in item[1] for item in observed["requests"])
    page.locator('#preparation-review-open').click()
    if hold: page.wait_for_function("window.__privateReviewAdmitted===true")
    return pending


@pytest.mark.parametrize("interrupt", ["hide", "close", "escape", "reload", "pagehide", "expired"])
def test_revealed_values_are_cleared_without_clipboard_storage_or_website_calls(preparation_ui, interrupt):
    page, observed = preparation_ui
    open_review(page, observed)
    expect(page.locator('#preparation-review-fields')).to_contain_text(CANARY)
    if interrupt == "hide" and os.environ.get("JAE_UI_SCREENSHOT_DIR"):
        directory = Path(os.environ["JAE_UI_SCREENSHOT_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        page.locator('#preparation-review').scroll_into_view_if_needed()
        page.screenshot(path=str(directory / "preparation-private-review.png"))
    if interrupt == "hide": page.locator('#preparation-review-hide').click()
    elif interrupt == "close": page.locator('#preparation-close').click()
    elif interrupt == "escape": page.locator('#preparation-dialog').press('Escape')
    elif interrupt == "reload": page.reload()
    elif interrupt == "pagehide": page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    else:
        observed["expired"] = True; page.evaluate("state()")
        expect(page.locator('#session-expired')).to_be_visible()
    assert CANARY not in page.content()
    assert page.evaluate("localStorage.length+sessionStorage.length") == 0
    assert page.evaluate("window.__copied.length") == 0
    assert observed["external"] == [] and observed["errors"] == []
    assert all(item[0] == 'GET' for item in observed["requests"])


@pytest.mark.parametrize("interrupt", ["close", "escape", "task_change", "expired"])
def test_delayed_private_reply_cannot_revive_closed_or_rebound_surface(preparation_ui, interrupt):
    page, observed = preparation_ui
    pending = open_review(page, observed, hold=True)
    page.evaluate("document.getElementById('preparation-review-open').dispatchEvent(new MouseEvent('click'))")
    assert len(pending) == 1
    if interrupt == "close": page.locator('#preparation-close').click()
    elif interrupt == "escape": page.locator('#preparation-dialog').press('Escape')
    elif interrupt == "task_change": page.evaluate("openTaskPreparation('prep-b',9)")
    else:
        observed["expired"] = True; page.evaluate("state()")
        expect(page.locator('#session-expired')).to_be_visible()
    pending[0].fulfill(status=200, content_type="application/json", body=json.dumps(payload()))
    page.wait_for_timeout(100)
    assert CANARY not in page.content()
    assert observed["external"] == [] and observed["errors"] == []


@pytest.mark.parametrize("change", ["task", "contract", "unknown_field", "capability", "value_type", "protected"])
def test_malformed_or_stale_private_response_is_not_rendered(preparation_ui, change):
    page, observed = preparation_ui
    data = payload()
    if change == "task": data["task_id"] = "prep-b"
    elif change == "contract": data["contract_version"] = "unknown"
    elif change == "unknown_field": data["proposals"][0]["field_id"] = "unknown"
    elif change == "capability": data["capabilities"]["live_write"] = True
    elif change == "value_type": data["proposals"][0]["value"] = {"secret": CANARY}
    else: data["proposals"][0]["field_id"] = "12"
    open_review(page, observed, result=data)
    expect(page.locator('#preparation-review-status')).to_contain_text('无法读取')
    assert CANARY not in page.content()
    assert observed["external"] == [] and observed["errors"] == []
