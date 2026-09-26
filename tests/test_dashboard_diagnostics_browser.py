"""Cloud browser check for explicit diagnostic preview and copy."""
from __future__ import annotations

import json

import pytest

from playwright.sync_api import expect, sync_playwright

from executor.autonomy.dashboard import DASHBOARD_HTML
from executor.autonomy import bootstrap


def test_diagnostics_need_explicit_copy_and_clear_after_close():
    report = {
        "format": "application-executor-diagnostics-v1",
        "captured_at_utc": "2026-09-24T21:00:00+00:00",
        "recovery": {"reason": "provider_unavailable", "action": "check_provider_settings"},
        "safety": {"final_click_actor": "user", "submit_capability": False},
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("cloud-ui.test", 1)[-1]
                if path.startswith("/ui/api/diagnostics"):
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(report))
                elif path in {"", "/"}:
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"tasks":[],"ready_for_live_e2e":false}')

            page.route("http://cloud-ui.test/**", route_request)
            page.goto("http://cloud-ui.test/")
            page.evaluate("""() => {
              window.__copied = [];
              Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {writeText: async text => window.__copied.push(text)}
              });
            }""")
            page.get_by_role("button", name="查看诊断").click()
            dialog = page.locator("#diagnostics-dialog")
            expect(dialog).to_be_visible()
            assert dialog.is_visible()
            assert json.loads(page.locator("#diagnostics-report").inner_text()) == report
            assert page.evaluate("window.__copied.length") == 0
            page.get_by_role("button", name="复制报告").click()
            page.wait_for_function("window.__copied.length === 1")
            assert json.loads(page.evaluate("window.__copied[0]")) == report
            page.get_by_role("button", name="关闭").click()
            assert not page.locator("#diagnostics-dialog").is_visible()
            assert page.locator("#diagnostics-report").inner_text() == ""
        finally:
            browser.close()


def test_bootstrap_diagnostics_are_explicit_and_copy_safe(monkeypatch):
    digest = "a" * 64
    token = "sensitive-bootstrap-token"
    monkeypatch.setattr(bootstrap, "_version", lambda: "verified-v1")
    monkeypatch.setattr(
        bootstrap, "read_release_identity",
        lambda _root: {"status": "verified", "source_sha256": digest},
    )
    page_html = bootstrap._page("service_start_failed", token)
    assert token in page_html  # The recovery form needs the private token.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.route(
                "http://bootstrap.test/**",
                lambda route: route.fulfill(
                    status=200, content_type="text/html", body=page_html
                ),
            )
            page.goto("http://bootstrap.test/")
            page.evaluate("""() => {
              window.__copied = [];
              Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {writeText: async text => window.__copied.push(text)}
              });
            }""")
            assert page.evaluate("window.__copied.length") == 0
            page.get_by_text("查看安全诊断").click()
            report = json.loads(page.locator("#safe-diagnostics").inner_text())
            assert report["reason"] == "service_start_failed"
            assert report["recovery_action"] == "retry_service"
            assert report["loaded_source_verified"] is True
            assert report["loaded_source_sha256"] == digest
            assert report["loaded_version"] == "verified-v1"
            assert report["applicant_values_in_report"] is False
            assert report["submit_capability"] is False
            assert token not in json.dumps(report)
            page.get_by_role("button", name="复制诊断").click()
            assert json.loads(page.evaluate("window.__copied[0]")) == report
        finally:
            browser.close()


def test_readiness_details_are_read_only_and_restore_focus():
    """Onboarding shows only named checks and does not start an applicant action."""
    readiness_payload = {
        "ready_for_live_e2e": False,
        "message": "资料文件尚未就绪。",
        "checks": {
            "live_browser_mode": True,
            "chrome_installed": True,
            "existing_cdp_session": False,
            "profile_configured": False,
            "profile_exists": False,
            "profile_loadable": False,
            "deepseek_available": False,
            "supervisor_running": True,
        },
        "remediation": ["configure_profile_path"],
        "profile_secret": "must-not-appear",
        "final_click_actor": "user",
        "submit_capability": False,
    }
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                requests.append((route.request.method, route.request.url))
                path = route.request.url.split("readiness-ui.test", 1)[-1]
                if path.startswith("/ui/api/readiness"):
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(readiness_payload))
                elif path in {"", "/"}:
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"tasks":[]}')

            page.route("http://readiness-ui.test/**", route_request)
            page.goto("http://readiness-ui.test/")
            page.get_by_role("button", name="运行条件").click()
            dialog = page.locator("#readiness-dialog")
            assert dialog.is_visible()
            expect(page.locator("#readiness-close")).to_be_focused()
            assert "资料文件尚未就绪" in page.locator("#readiness-summary").inner_text()
            assert page.locator("#readiness-checks li").count() == 8
            assert "待处理" in page.locator("#readiness-checks").inner_text()
            assert "must-not-appear" not in dialog.inner_text()
            assert all(method == "GET" for method, _ in requests)
            page.get_by_role("button", name="关闭").click()
            assert not dialog.is_visible()
            expect(page.get_by_role("button", name="运行条件")).to_be_focused()

            readiness_payload["ready_for_live_e2e"] = True
            readiness_payload["message"] = "已就绪"
            readiness_payload["checks"] = {
                key: True for key in readiness_payload["checks"]
            }
            page.evaluate("readiness()")
            page.get_by_role("button", name="运行条件").click()
            assert "运行条件已就绪" in page.locator("#readiness-summary").inner_text()
            assert "最终提交仍由你本人完成" in dialog.inner_text()
            assert "待处理" not in page.locator("#readiness-checks").inner_text()
            assert all(method == "GET" for method, _ in requests)
        finally:
            browser.close()


def test_app_shell_keeps_tasks_visible_at_narrow_zoom_and_announces_once():
    """Task access, keyboard focus and status announcements survive a narrow window."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        try:
            def route_request(route):
                path = route.request.url.split("shell-ui.test", 1)[-1]
                if path in {"", "/"}:
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path.startswith("/ui/api/diagnostics"):
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"reason":"service_healthy"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"tasks":[],"ready_for_live_e2e":false}')

            page.route("http://shell-ui.test/**", route_request)
            page.goto("http://shell-ui.test/")
            assert page.locator("aside").is_visible()
            assert page.locator("#newtask").is_visible()
            assert page.locator("#tasks").is_visible()
            assert page.locator("#toast").get_attribute("role") == "status"
            first, duplicate, changed = page.evaluate("""async () => {
              const toast = document.getElementById('toast');
              let count = 0;
              new MutationObserver(() => count++).observe(toast, {
                childList:true,subtree:true,characterData:true
              });
              notify('状态已更新'); await Promise.resolve();
              const first = count;
              notify('状态已更新'); await Promise.resolve();
              const duplicate = count;
              notify('需要你处理'); await Promise.resolve();
              return [first,duplicate,count];
            }""")
            assert first > 0
            assert duplicate == first
            assert changed > duplicate

            page.get_by_role("button", name="查看诊断").click()
            expect(page.locator("#diagnostics-dialog")).to_be_visible()
            page.locator("#diagnostics-close").click()
            expect(page.get_by_role("button", name="查看诊断")).to_be_focused()
        finally:
            browser.close()


@pytest.mark.parametrize("trigger", ["state", "readiness", "diagnostics", "command", "chat"])
def test_expired_session_preserves_unsent_input_and_refuses_replay(trigger):
    """The first real API 401 latches a truthful, read-only consumer page."""
    requests = []
    expired = False
    tasks = [
        {"task_id": "question", "company": "Synthetic", "role": "Engineer",
         "stage": "NEEDS_USER_INPUT", "revision": 2, "blocker": "unknown_facts",
         "question_context": {"status": "current", "items": [
             {"key": "motivation", "label": "申请原因", "required": True}]},
         "unresolved_keys": ["motivation"], "boolean_keys": [], "reusable_keys": []},
        {"task_id": "running", "company": "Synthetic", "role": "Designer",
         "stage": "DISCOVERED", "revision": 1},
        {"task_id": "review", "company": "Synthetic", "role": "Reviewer",
         "stage": "READY_TO_SUBMIT", "revision": 3, "review_values_available": True,
         "review_summary": {"status": "last_verified"}},
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("expired-ui.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif expired:
                    route.fulfill(status=401, content_type="application/json",
                                  body='{"error":"ui_session_required"}')
                elif path.startswith("/ui/api/review-values"):
                    route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"revision": 3, "review": {"fields": [
                            {"label": "合成复核", "expected": "PRIVATE_REVIEW_CANARY",
                             "observed": "PRIVATE_REVIEW_CANARY"}]}}))
                elif path == "/ui/api/diagnostics":
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"reason":"service_healthy"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"tasks": tasks, "ready_for_live_e2e": False}))

            page.route("http://expired-ui.test/**", route_request)
            page.goto("http://expired-ui.test/")
            expect(page.get_by_label("申请原因", exact=True)).to_be_visible()
            page.get_by_role("button", name="查看完整复核值", exact=True).click()
            expect(page.locator('[data-private-review-panel][data-private-review-open="review"]')).to_contain_text("PRIVATE_REVIEW_CANARY")
            page.get_by_label("申请原因", exact=True).fill("未发送的完整真实用词")
            page.locator('input[name="company"]').fill("未发送公司")
            page.locator("#message").fill("未发送消息\n保留第二行")
            expired = True
            if trigger in {"state", "readiness"}:
                page.evaluate(trigger + "()")
            elif trigger == "diagnostics":
                page.get_by_role("button", name="查看诊断", exact=True).click()
            elif trigger == "chat":
                page.locator("#send").click()
            else:
                page.get_by_role("button", name="暂停", exact=True).click()
            notice = page.locator("#session-expired")
            expect(notice).to_be_visible()
            expect(notice).to_be_focused()
            expect(notice).to_contain_text("未发送的输入仍保留")
            expect(notice).to_contain_text("最终提交仍由你本人完成")
            assert page.get_by_label("申请原因", exact=True).input_value() == "未发送的完整真实用词"
            assert page.locator('input[name="company"]').input_value() == "未发送公司"
            assert page.locator("#message").input_value() == "未发送消息\n保留第二行"
            assert page.locator("#message").get_attribute("readonly") is not None
            expect(page.locator("#message")).to_be_enabled()
            assert page.get_by_label("申请原因", exact=True).get_attribute("readonly") is not None
            assert all(panel.inner_text() == "" for panel in page.locator("[data-private-review-panel]").all())
            assert all(not panel.is_visible() for panel in page.locator("[data-private-review-panel]").all())
            assert "PRIVATE_REVIEW_CANARY" not in page.locator(".shell").inner_text()
            assert page.locator("#health").inner_text() == "面板会话已失效"
            for button in page.locator(".shell button").all():
                expect(button).to_be_disabled()
            checkpoint = list(requests)
            # Exercise the same polling/action entrypoints and synthetic DOM
            # dispatches after expiry; none may send a new request or replay.
            page.evaluate("""async () => {
              await state(); await readiness(); await pollUpdate(); await submit();
              document.getElementById('newtask').dispatchEvent(new Event('submit', {cancelable:true}));
              document.getElementById('diagnostics').dispatchEvent(new Event('click'));
              document.getElementById('update').dispatchEvent(new Event('click'));
              document.querySelector('[data-answer]').dispatchEvent(new MouseEvent('click', {bubbles:true}));
              await Promise.resolve();await Promise.resolve();
            }""")
            assert requests == checkpoint
            expect(page.locator("#session-expired")).to_be_visible()
            expect(page.locator("#send")).to_be_disabled()
            expect(page.locator("#update")).to_be_disabled()
            assert page.locator("#message").input_value() == "未发送消息\n保留第二行"
            posts = [path for method, path in requests if method == "POST"]
            assert posts == (["/ui/api/" + trigger] if trigger in {"command", "chat"} else [])
            assert all(path.startswith("/ui/api/") or path == "/" for _, path in requests)
        finally:
            browser.close()


def test_expired_session_discards_late_diagnostics_without_refreshing_private_ui():
    held = []
    expired = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("late-ui.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/diagnostics":
                    held.append(route)
                elif expired:
                    route.fulfill(status=401, content_type="application/json",
                                  body='{"error":"ui_session_required"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"tasks":[],"ready_for_live_e2e":false}')

            page.route("http://late-ui.test/**", route_request)
            page.goto("http://late-ui.test/")
            expect(page.locator("#tasks")).to_have_text("暂无任务")
            with page.expect_request("http://late-ui.test/ui/api/diagnostics"):
                page.get_by_role("button", name="查看诊断", exact=True).click()
            assert len(held) == 1
            expired = True
            page.evaluate("readiness()")
            expect(page.locator("#session-expired")).to_be_visible()
            with page.expect_response("http://late-ui.test/ui/api/diagnostics"):
                held[0].fulfill(status=200, content_type="application/json",
                               body='{"stale":"PRIVATE_LATE_CANARY"}')
            expect(page.locator("#diagnostics")).to_be_disabled()
            assert page.locator("#diagnostics-report").inner_text() == ""
            expect(page.locator("#diagnostics-dialog")).not_to_be_visible()
            assert "PRIVATE_LATE_CANARY" not in page.locator("body").inner_text()
            expect(page.locator("#session-expired")).to_be_focused()
        finally:
            browser.close()
