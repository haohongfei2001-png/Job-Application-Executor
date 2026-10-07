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
        bootstrap, "current_packaged_source",
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


def test_task_workspace_readiness_details_are_read_only_and_restore_focus():
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
            assert "DeepSeek 配置已加载" in page.locator("#readiness-checks").inner_text()
            assert "DeepSeek 已连接" not in dialog.inner_text()
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
            assert "DeepSeek 配置已加载" in page.locator("#readiness-checks").inner_text()
            assert "DeepSeek 已连接" not in dialog.inner_text()
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
    errors = []
    expired = False
    first_401 = None
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
                nonlocal first_401
                path = route.request.url.split("expired-ui.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                # Keep real background polling active. The first injected 401
                # belongs to this parameter's endpoint; afterward every API is
                # expired. Otherwise a state poll can preempt the button case.
                elif expired and (first_401 is not None or path == "/ui/api/" + trigger):
                    if first_401 is None:
                        first_401 = (route.request.method, path)
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

            # Production loopback is a trustworthy origin. HTTPS gives the
            # routed fixture the same secure-context randomUUID contract,
            # without changing runtime UUID creation or making network calls.
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://expired-ui.test/**", route_request)
            page.goto("https://expired-ui.test/")
            assert page.evaluate("isSecureContext && typeof crypto.randomUUID === 'function'")
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
            assert first_401 == ("POST" if trigger in {"command", "chat"} else "GET",
                                 "/ui/api/" + trigger)
            assert errors == []
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


def test_task_workspace_keyboard_context_survives_reorder_without_implicit_actions():
    """The actual task surface preserves orientation without granting a command."""
    tasks = [
        {"task_id": "a", "company": "Alpha", "role": "Engineer", "stage": "DISCOVERED", "revision": 1},
        {"task_id": "b", "company": "Beta", "role": "Designer", "stage": "DISCOVERED", "revision": 2},
        {"task_id": "c", "company": "Gamma", "role": "Reviewer", "stage": "READY_TO_SUBMIT", "revision": 3},
    ]
    requests, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        try:
            def route_request(route):
                path = route.request.url.split("workspace.test", 1)[-1]
                requests.append((route.request.method, path, route.request.post_data))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                else:
                    route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"tasks": tasks, "ready_for_live_e2e": False}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://workspace.test/**", route_request)
            page.goto("https://workspace.test/")
            alpha = page.get_by_role("button", name="查看任务 · Alpha · Engineer", exact=True)
            beta = page.get_by_role("button", name="查看任务 · Beta · Designer", exact=True)
            gamma = page.get_by_role("button", name="查看任务 · Gamma · Reviewer", exact=True)
            expect(alpha).to_be_visible()
            expect(page.locator("#task-context")).to_contain_text("尚未选中")
            alpha.focus()
            alpha.press("ArrowDown")
            expect(beta).to_be_focused()
            assert beta.get_attribute("aria-pressed") == "false"
            beta.press("Enter")
            expect(beta).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#task-context")).to_contain_text("Beta · Designer")
            beta.press("End")
            expect(gamma).to_be_focused()
            gamma.press("Home")
            expect(alpha).to_be_focused()
            alpha.press("ArrowUp")
            expect(alpha).to_be_focused()
            gamma.focus()
            gamma.press("ArrowDown")
            expect(gamma).to_be_focused()

            beta.focus()
            tasks[:] = [tasks[2], tasks[1], tasks[0]]
            with page.expect_response("https://workspace.test/ui/api/state"):
                page.evaluate("state()")
            expect(beta).to_be_focused()
            expect(beta).to_have_attribute("aria-pressed", "true")
            assert page.locator('[data-current-task="true"]').count() == 1
            page.locator("#message").fill("未发送的完整消息\n保留第二行")
            company_input = page.locator('input[name="company"]')
            company_input.fill("未发送公司")
            expect(company_input).to_be_focused()
            with page.expect_response("https://workspace.test/ui/api/state"):
                page.evaluate("state()")
            # Filling the company field moved focus there. Readback must
            # preserve that actual editing focus, never steal it for a card.
            expect(company_input).to_be_focused()
            expect(beta).to_have_attribute("aria-pressed", "true")
            assert page.locator("#message").input_value() == "未发送的完整消息\n保留第二行"
            assert page.locator('input[name="company"]').input_value() == "未发送公司"

            beta.focus()
            expect(beta).to_be_focused()
            tasks[:] = [task for task in tasks if task["task_id"] != "b"]
            with page.expect_response("https://workspace.test/ui/api/state"):
                page.evaluate("state()")
            expect(page.locator("#task-context")).to_be_focused()
            expect(page.locator("#task-context")).to_contain_text("尚未选中")
            assert page.locator('[data-current-task="true"]').count() == 0
            assert page.locator("[data-task-select]").count() == 2
            assert all(method == "GET" for method, _, _ in requests)
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


def test_task_workspace_selection_never_retargets_an_explicit_card_command():
    """Orientation is not command authority: another card retains its exact ID/revision."""
    tasks = [
        {"task_id": "first", "company": "<img src=x onerror=alert(1)>", "role": "Engineer",
         "stage": "DISCOVERED", "revision": 7},
        {"task_id": "second", "company": "Second", "role": "Designer",
         "stage": "DISCOVERED", "revision": 9},
    ]
    commands, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("commands-workspace.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/command":
                    commands.append(json.loads(route.request.post_data))
                    route.fulfill(status=200, content_type="application/json", body='{"accepted":true}')
                else:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({"tasks": tasks}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://commands-workspace.test/**", route_request)
            page.goto("https://commands-workspace.test/")
            page.get_by_role("button", name="查看任务 · Second · Designer", exact=True).click()
            expect(page.locator("#task-context")).to_contain_text("Second · Designer")
            assert commands == []
            assert page.locator("#tasks img").count() == 0
            first_card = page.locator('[data-task-card="first"]')
            expect(first_card).to_contain_text("<img src=x onerror=alert(1)>")
            first_card.get_by_role("button", name="暂停", exact=True).click()
            expect(page.locator("#toast")).to_contain_text("任务已暂停")
            assert len(commands) == 1
            assert commands[0]["task_id"] == "first"
            assert commands[0]["expected_revision"] == 7
            assert commands[0]["action"] == "PAUSE"
            assert commands[0]["command_id"].startswith("ui-")
            expect(page.locator('[data-task-select="second"]')).to_have_attribute("aria-pressed", "true")
            assert errors == []
        finally:
            browser.close()


def test_task_workspace_expiry_clears_orientation_and_never_replays_selection():
    """A stale page preserves unsent facts but cannot keep a live task context."""
    expired = False
    requests, errors = [], []
    task = {"task_id": "answer", "company": "Synthetic", "role": "Engineer",
            "stage": "NEEDS_USER_INPUT", "revision": 4, "blocker": "unknown_facts",
            "question_context": {"status": "current", "items": [
                {"key": "motivation", "label": "申请原因", "required": True}]},
            "unresolved_keys": ["motivation"], "boolean_keys": [], "reusable_keys": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("expired-workspace.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif expired:
                    route.fulfill(status=401, content_type="application/json", body='{"error":"ui_session_required"}')
                else:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({"tasks": [task]}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://expired-workspace.test/**", route_request)
            page.goto("https://expired-workspace.test/")
            selection = page.get_by_role("button", name="查看任务 · Synthetic · Engineer", exact=True)
            selection.click()
            expect(selection).to_have_attribute("aria-pressed", "true")
            page.get_by_label("申请原因", exact=True).fill("未发送的本人回答")
            with page.expect_response("https://expired-workspace.test/ui/api/state"):
                page.evaluate("state()")
            assert page.get_by_label("申请原因", exact=True).input_value() == "未发送的本人回答"
            expect(selection).to_have_attribute("aria-pressed", "true")
            expired = True
            page.evaluate("readiness()")
            expect(page.locator("#session-expired")).to_be_visible()
            expect(page.locator("#task-context")).to_contain_text("面板会话已失效")
            expect(selection).to_have_attribute("aria-pressed", "false")
            expect(selection).to_be_disabled()
            checkpoint = list(requests)
            page.evaluate("""() => {
              const trigger=document.querySelector('[data-task-select]');
              trigger.dispatchEvent(new MouseEvent('click',{bubbles:true}));
              trigger.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
            }""")
            expect(page.locator("#session-expired")).to_be_focused()
            assert requests == checkpoint
            assert all(method == "GET" for method, _ in requests)
            assert page.get_by_label("申请原因", exact=True).input_value() == "未发送的本人回答"
            expect(selection).to_have_attribute("aria-pressed", "false")
            assert errors == []
        finally:
            browser.close()


def test_task_workspace_explicit_remember_restores_only_view_across_page_reopen():
    tasks = [
        {"task_id": "alpha", "company": "Alpha", "role": "Engineer", "stage": "DISCOVERED", "revision": 1},
        {"task_id": "beta", "company": "Beta", "role": "Designer", "stage": "DISCOVERED", "revision": 2},
    ]
    saved = {"task_id": None, "revision": 0}
    posts, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("remember-workspace.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/task-view-context":
                    data = json.loads(route.request.post_data)
                    posts.append(data)
                    assert set(data) == {"task_id", "expected_revision"}
                    assert data["expected_revision"] == saved["revision"]
                    saved.update(task_id=data["task_id"], revision=saved["revision"] + 1)
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(saved))
                else:
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"tasks": tasks, "ui_context": saved}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://remember-workspace.test/**", route_request)
            page.goto("https://remember-workspace.test/")
            expect(page.get_by_role("button", name="记住当前任务", exact=True)).to_be_disabled()
            page.get_by_role("button", name="查看任务 · Beta · Designer", exact=True).click()
            expect(page.locator('[data-task-select="beta"]')).to_have_attribute("aria-pressed", "true")
            assert posts == [], "view navigation itself never writes"
            page.locator("#message").fill("UNSENT_PRIVATE_BODY")
            page.get_by_role("button", name="记住当前任务", exact=True).click()
            expect(page.locator("#toast")).to_contain_text("下次打开只恢复查看位置")
            assert posts == [{"task_id": "beta", "expected_revision": 0}]
            assert page.locator("#message").input_value() == "UNSENT_PRIVATE_BODY"
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            page.reload()
            expect(page.locator('[data-task-select="beta"]')).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#task-context")).to_contain_text("Beta · Designer")
            assert posts == [{"task_id": "beta", "expected_revision": 0}]
            assert "UNSENT_PRIVATE_BODY" not in json.dumps(saved)
            assert page.locator("#message").input_value() == ""
            page.get_by_role("button", name="忘记查看位置", exact=True).click()
            expect(page.locator("#toast")).to_contain_text("现有任务和输入保持不变")
            assert posts[-1] == {"task_id": None, "expected_revision": 1}
            page.reload()
            expect(page.locator("#task-context")).to_contain_text("尚未选中")
            assert page.locator('[data-current-task="true"]').count() == 0
            assert len(posts) == 2
            assert tasks[0]["revision"] == 1 and tasks[1]["revision"] == 2
            assert errors == []
        finally:
            browser.close()


def test_task_workspace_uncertain_save_requires_explicit_readback_without_replay():
    task = {"task_id": "beta", "company": "Beta", "role": "Designer", "stage": "DISCOVERED", "revision": 2}
    saved = {"task_id": None, "revision": 0}
    posts, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("uncertain-view.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/task-view-context":
                    posts.append(json.loads(route.request.post_data))
                    saved.update(task_id="beta", revision=1)
                    route.fulfill(status=200, content_type="application/json",
                        body='{"task_id":"wrong","revision":1}')
                else:
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"tasks": [task], "ui_context": saved}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://uncertain-view.test/**", route_request)
            page.goto("https://uncertain-view.test/")
            page.get_by_role("button", name="查看任务 · Beta · Designer", exact=True).click()
            page.locator("#message").fill("UNSENT_STAYS_LOCAL")
            remember = page.get_by_role("button", name="记住当前任务", exact=True)
            remember.click()
            expect(page.locator("#saved-task-view")).to_contain_text("保存结果未确认")
            expect(remember).to_be_disabled()
            assert len(posts) == 1
            page.evaluate("state()")
            expect(page.locator("#saved-task-view")).to_contain_text("保存结果未确认")
            expect(remember).to_be_disabled()
            page.evaluate("document.getElementById('remember-task-view').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
            assert len(posts) == 1
            page.get_by_role("button", name="重新读取查看位置", exact=True).click()
            expect(page.locator("#saved-task-view")).to_contain_text("已记住一个任务")
            expect(remember).to_be_enabled()
            assert len(posts) == 1
            assert page.locator("#message").input_value() == "UNSENT_STAYS_LOCAL"
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


def test_task_workspace_late_saved_view_reply_cannot_revive_expired_session():
    task = {"task_id": "beta", "company": "Beta", "role": "Designer", "stage": "DISCOVERED", "revision": 2}
    pending, requests, errors = [], [], []
    expired = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("late-view.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/task-view-context":
                    pending.append(route)
                elif expired:
                    route.fulfill(status=401, content_type="application/json", body='{"error":"ui_session_required"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                        body=json.dumps({"tasks": [task], "ui_context": {"task_id": None, "revision": 0}}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://late-view.test/**", route_request)
            page.goto("https://late-view.test/")
            page.get_by_role("button", name="查看任务 · Beta · Designer", exact=True).click()
            page.locator("#message").fill("UNSENT_LATE_VIEW")
            with page.expect_request("https://late-view.test/ui/api/task-view-context"):
                page.get_by_role("button", name="记住当前任务", exact=True).click()
            assert len(pending) == 1
            expired = True
            page.evaluate("readiness()")
            expect(page.locator("#session-expired")).to_be_visible()
            checkpoint = list(requests)
            with page.expect_response("https://late-view.test/ui/api/task-view-context"):
                pending[0].fulfill(status=200, content_type="application/json",
                    body='{"task_id":"beta","revision":1}')
            expect(page.locator("#saved-task-view")).to_contain_text("面板会话已失效")
            expect(page.locator('[data-task-select="beta"]')).to_have_attribute("aria-pressed", "false")
            expect(page.get_by_role("button", name="记住当前任务", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="重新读取查看位置", exact=True)).to_be_disabled()
            expect(page.locator("#session-expired")).to_be_focused()
            assert requests == checkpoint
            assert page.locator("#message").input_value() == "UNSENT_LATE_VIEW"
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


@pytest.fixture
def profile_setup_service(tmp_path, monkeypatch):
    """Actual loopback endpoint and private authority, synthetic applicant only."""
    import threading
    from types import SimpleNamespace
    from executor import settings
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.worker import Worker
    from executor.autonomy.manager import ManagerController
    from executor.autonomy.supervisor import Supervisor, create_server
    parent = tmp_path / "profile-settings"
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(settings, "PATH", parent / "settings.json")
    monkeypatch.setattr(settings, "PACKAGED", True)
    old = parent / "previous-profile.json"
    old.write_text('{"fields":{"identity.full_name":{"value":"PRIVATE_OLD"}}}')
    old.chmod(0o600)
    original = {"profile_path": str(old), "deepseek": {"enabled": False},
                "unknown_preference": {"full": "完整\n" * 1000}}
    settings.save_settings(original)
    queue = TaskQueue(tmp_path / "task-runtime")
    worker = Worker(queue, settings=original)
    calls = []
    provider = SimpleNamespace(available=False, decide=lambda *_: calls.append("provider"))
    manager = ManagerController(queue, worker, provider=provider, settings=original)
    supervisor = Supervisor(queue, worker=worker, manager=manager, token="x" * 40)
    task = queue.enqueue(TaskSpec(company="Synthetic Co", role="Engineer", job_id="profile-test",
                                 target_url="https://synthetic.example.test/job/profile-test",
                                 profile_ref=str(old), live_authorized=False))
    monkeypatch.setattr(supervisor, "readiness", lambda: {
        "ready_for_live_e2e": False, "message": "合成资料设置检查", "submit_capability": False})
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield settings, queue, supervisor, base, task["task_id"], calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_task_workspace_profile_setup_actual_file_save_and_complete_authority(profile_setup_service):
    from pathlib import Path
    settings, queue, supervisor, base, tid, calls = profile_setup_service
    before = queue.get(tid)
    old = Path(before["spec"]["profile_ref"])
    old_bytes = old.read_bytes()
    profile = {"fields": {"identity.full_name": {"value": "PRIVATE_NEW", "user_confirmed": False}},
               "research": [{"description": "完整长研究 👩🏽‍💻\n" * 1000}],
               "unknown": {"source": "<img src=x onerror=alert(1)>"}}
    errors, writes = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        try:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            page.get_by_role("button", name="资料设置", exact=True).click()
            expect(page.locator("#profile-file")).to_be_enabled()
            expect(page.locator("#profile-save")).to_be_disabled()
            page.locator("#profile-file").set_input_files({
                "name": "synthetic-profile.json", "mimeType": "application/json",
                "buffer": json.dumps(profile, ensure_ascii=False).encode("utf-8")})
            expect(page.locator("#profile-save")).to_be_enabled()
            assert writes == [] and settings.load_settings()["profile_path"] == str(old)
            page.locator("#profile-import summary").click()
            page.locator("#profile-save").click()
            expect(page.locator("#profile-status")).to_contain_text("资料已保存在本机")
            selected = settings.load_settings()
            new = Path(selected["profile_path"])
            assert new != old and json.loads(new.read_text()) == profile
            assert new.stat().st_mode & 0o777 == 0o600
            assert selected["unknown_preference"]["full"] == "完整\n" * 1000
            assert supervisor.manager._profile_ref() == str(new)
            assert supervisor.worker.settings == selected
            assert queue.get(tid) == before and old.read_bytes() == old_bytes
            assert writes == [base + "/ui/api/profile-setup"] and calls == []
            assert page.locator("#profile-file").input_value() == ""
            assert page.locator("#chat").inner_text().find("PRIVATE_NEW") == -1
            assert page.locator("#profile-dialog img").count() == 0
            page.locator("#profile-close").click()
            expect(page.locator("#profile-setup")).to_be_focused()
            assert page.locator("#profile-status").inner_text() == ""
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            page.get_by_role("button", name="资料设置", exact=True).click()
            expect(page.locator("#profile-status")).to_contain_text("已选择本机资料")
            expect(page.locator("#profile-save")).to_be_disabled()
            assert page.locator("#profile-file").input_value() == ""
            assert errors == []
        finally:
            browser.close()


def _latch_profile_recovery(settings, supervisor):
    from pathlib import Path
    supervisor.configure_profile('{"fields":{"identity.full_name":{"value":"SYNTHETIC_BEFORE_RECOVERY"}}}',
                                 settings.settings_version(settings.load_settings()))
    damaged = Path(supervisor.manager.settings["profile_path"])
    damaged.write_bytes(b"SYNTHETIC_INVALID_PROFILE")
    with pytest.raises(RuntimeError, match="profile_selection_reconciliation_required"):
        supervisor.manager._profile_ref()
    assert supervisor.manager._profile_selection_uncertain is True
    return damaged


def test_task_workspace_profile_import_pending_guidance_uses_explicit_existing_reconcile(profile_setup_service, monkeypatch):
    import os
    from pathlib import Path
    from executor.discovery.core import DiscoveryRequest, DiscoveryResult
    settings, queue, supervisor, base, tid, calls = profile_setup_service
    before = queue.get(tid)
    damaged = _latch_profile_recovery(settings, supervisor)
    prepared = DiscoveryRequest("Synthetic", "Future", source_url="https://future.test/reconciliation")
    discovery = DiscoveryResult("UNSUPPORTED", (), None, "synthetic", "no_official_site_contract")
    monkeypatch.setattr(supervisor.manager, "prepare_local_form", lambda *a, **k: (prepared, discovery))
    writes, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 850})
        try:
            page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            page.locator("#profile-setup").click()
            expect(page.locator("#profile-status")).to_contain_text("仍需明确核对")
            page.locator("#profile-import summary").click()
            page.locator("#profile-file").set_input_files({"name": "recovery.json", "mimeType": "application/json",
                "buffer": b'{"fields":{"identity.full_name":{"value":"SYNTHETIC_RECOVERED"}}}'})
            page.locator("#profile-save").click()
            expect(page.locator("#profile-status")).to_contain_text("资料已保存在本机，但仍需明确核对")
            expect(page.locator("#profile-editor-open")).to_have_text("打开资料并核对")
            assert writes == [base + "/ui/api/profile-setup"]
            assert supervisor.manager._profile_selection_uncertain is True and queue.get(tid) == before
            screenshot = os.environ.get("JAE_UI_SCREENSHOT_DIR")
            if screenshot:
                destination = Path(screenshot);destination.mkdir(parents=True, exist_ok=True)
                page.locator("#profile-dialog").screenshot(path=str(destination / "profile-reconciliation.png"), animations="disabled")
            page.locator("#profile-close").click()
            page.locator('#newtask [name="company"]').fill("Synthetic")
            page.locator('#newtask [name="role"]').fill("Future")
            page.locator('#newtask [name="target_url"]').fill(prepared.source_url)
            page.locator("#newtask button").click()
            expect(page.locator("#profile-reconciliation-note")).to_contain_text("本次没有添加任务")
            expect(page.locator("#toast")).to_contain_text("资料仍待明确核对")
            expect(page.locator('#newtask [name="role"]')).to_have_value("Future")
            assert queue.tasks() == [before] and supervisor.manager._profile_selection_uncertain is True
            page.locator("button[data-profile-reconciliation]").click()
            expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
            expect(page.locator("#profile-editor-save")).to_be_disabled()
            assert writes == [base + "/ui/api/profile-setup", base + "/ui/api/tasks"]
            # Opening the editor is read-only. Only this existing explicit
            # action can reconcile; it must never replay the blocked task.
            page.locator("#profile-editor-reconcile").click()
            expect(page.locator("#profile-editor-status")).to_contain_text("已重新读取本机当前记录")
            assert supervisor.manager._profile_selection_uncertain is False and queue.tasks() == [before]
            assert writes[-1] == base + "/ui/api/profile-editor/reconcile"
            page.locator("#profile-editor-close").click()
            page.locator("#profile-setup").click()
            expect(page.locator("#profile-status")).to_contain_text("已选择本机资料")
            expect(page.locator("#profile-status")).not_to_contain_text("仍需明确核对")
            expect(page.locator("#profile-editor-open")).to_have_text("填写或编辑基本资料与简历")
            page.locator("#profile-close").click()
            assert writes.count(base + "/ui/api/tasks") == 1
            page.locator("#newtask button").click()
            expect(page.locator("[data-task-card]")).to_have_count(2)
            assert len(queue.tasks()) == 2 and queue.get(tid) == before
            selected = settings.load_settings()["profile_path"]
            assert next(task for task in queue.tasks() if task["task_id"] != tid)["spec"]["profile_ref"] == selected
            assert damaged.read_bytes() == b"SYNTHETIC_INVALID_PROFILE" and calls == [] and errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("failure", ["http", "missing_status"])
def test_task_workspace_profile_import_readback_failure_is_unconfirmed_without_replay(profile_setup_service, failure):
    settings, queue, supervisor, base, tid, calls = profile_setup_service
    _latch_profile_recovery(settings, supervisor)
    before = queue.get(tid);writes = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            page.locator("#profile-setup").click()
            expect(page.locator("#profile-file")).to_be_enabled()
            page.locator("#profile-import summary").click()
            def intercept(route):
                if route.request.method == "POST":
                    writes.append(route.request.url);route.continue_()
                elif writes:
                    payload = {"error": "SYNTHETIC_PRIVATE_TRACE"} if failure == "http" else {
                        "settings_version": settings.settings_version(settings.load_settings()),
                        "profile_selected": True, "submit_capability": False}
                    route.fulfill(status=500 if failure == "http" else 200,
                                  content_type="application/json", body=json.dumps(payload))
                else: route.continue_()
            page.route("**/ui/api/profile-setup", intercept)
            page.locator("#profile-file").set_input_files({"name": "new.json", "mimeType": "application/json",
                "buffer": b'{"fields":{"identity.full_name":{"value":"SYNTHETIC_IMPORTED"}}}'})
            page.locator("#profile-save").click()
            expect(page.locator("#profile-status")).to_contain_text("资料保存未确认")
            expect(page.locator("#profile-save")).to_be_disabled()
            expect(page.locator("#profile-editor-open")).to_be_disabled()
            assert "SYNTHETIC_PRIVATE_TRACE" not in page.locator("#profile-dialog").inner_text()
            assert len(writes) == 1 and supervisor.manager._profile_selection_uncertain is True
            assert queue.get(tid) == before and calls == []
        finally:
            browser.close()


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_profile_setup_stale_file_read_never_sends_or_revives_private_selection(
    profile_setup_service, interruption
):
    settings, queue, supervisor, base, tid, calls = profile_setup_service
    before = settings.PATH.read_bytes(), queue.get(tid)
    writes, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            page.get_by_role("button", name="资料设置", exact=True).click()
            expect(page.locator("#profile-file")).to_be_enabled()
            page.locator("#profile-file").set_input_files({
                "name": "synthetic.json", "mimeType": "application/json",
                "buffer": b'{"name":"PRIVATE_DELAYED"}'})
            page.evaluate("""() => {
                File.prototype.text = () => new Promise(resolve => {
                    window.__finishProfileRead = () => resolve('{"name":"PRIVATE_DELAYED"}');
                });
            }""")
            page.locator("#profile-import summary").click()
            page.locator("#profile-save").click()
            page.wait_for_function("typeof window.__finishProfileRead === 'function'")
            if interruption in {"close", "escape"}:
                if interruption == "close":
                    page.locator("#profile-close").click()
                else:
                    page.locator("#profile-dialog").press("Escape")
                assert page.locator("#profile-file").input_value() == ""
                assert page.locator("#profile-status").inner_text() == ""
                page.get_by_role("button", name="资料设置", exact=True).click()
                expect(page.locator("#profile-file")).to_be_enabled()
            else:
                supervisor._ui_sessions.clear()
                page.evaluate("state()")
                expect(page.locator("#session-expired")).to_be_visible()
                expect(page.locator("#profile-dialog")).not_to_be_visible()
            page.evaluate("window.__finishProfileRead()")
            page.wait_for_function("document.querySelector('#profile-file').value === ''")
            assert settings.PATH.read_bytes() == before[0]
            assert queue.get(tid) == before[1]
            assert not list(settings.PATH.parent.glob("profile-*.json"))
            assert writes == [] and calls == [] and errors == []
            assert page.locator("#profile-status").inner_text().find("PRIVATE_DELAYED") == -1
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        finally:
            browser.close()

@pytest.mark.parametrize("availability", [True, False, "constructor_failure"])
@pytest.mark.parametrize("interruption", ["close", "escape"])
def test_task_workspace_explicit_provider_load_actual_authenticated_service_preserves_authority(
    profile_setup_service, monkeypatch, availability, interruption
):
    from pathlib import Path
    from types import SimpleNamespace
    from executor.autonomy import manager as manager_module

    settings, queue, supervisor, base, tid, external_calls = profile_setup_service
    supervisor.manager._provider = None
    loads = []
    before = (settings.PATH.read_bytes(), queue.tasks(), queue.recent_events(1000),
              {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()})
    def construct(configuration):
        assert configuration is supervisor.manager.settings
        loads.append("load")
        if availability == "constructor_failure":
            raise OSError("PRIVATE_KEYCHAIN_FAILURE")
        return SimpleNamespace(available=availability,
                               decide=lambda *_a, **_k: pytest.fail("presence is not a model request"))
    monkeypatch.setattr(manager_module, "DeepSeekManagerProvider", construct)
    def readiness():
        state = supervisor.manager.loaded_provider_state()
        return {"ready_for_live_e2e": False, "message": "真实站点条件尚未验证。",
                "provider_state": state["state"], "provider_state_basis": "loaded_configuration",
                "checks": {"deepseek_available": state["available"]},
                "final_click_actor": "user", "submit_capability": False}
    monkeypatch.setattr(supervisor, "readiness", readiness)
    errors, writes = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        try:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: writes.append((request.url, request.post_data))
                    if request.method == "POST" else None)
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            page.locator("#message").fill("UNSENT_PRIVATE_INPUT")
            assert page.locator("#message").input_value() == "UNSENT_PRIVATE_INPUT"
            page.get_by_role("button", name="运行条件", exact=True).click()
            expect(page.locator("#provider-load")).to_be_enabled()
            page.evaluate("readiness()")
            assert loads == [] and writes == [], "opening/polling must not acquire credentials"
            page.locator("#provider-load").click()
            expect(page.locator("#provider-load-status")).to_contain_text(
                "已加载本机配置" if availability is True else "已配置凭证暂不可用")
            assert loads == ["load"] and writes == [(base + "/ui/api/provider-load", "{}")]
            assert "PRIVATE_" not in page.locator("#readiness-dialog").inner_text()
            assert "真实站点条件尚未验证" in page.locator("#readiness-summary").inner_text()
            assert "已就绪" not in page.locator("#readiness-summary").inner_text()
            assert page.locator("#message").input_value() == "UNSENT_PRIVATE_INPUT"
            for _ in range(3):
                page.evaluate("readiness()")
            assert loads == ["load"] and external_calls == []
            assert before == (settings.PATH.read_bytes(), queue.tasks(), queue.recent_events(1000),
                              {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()})
            if interruption == "close":
                page.locator("#readiness-close").click()
            else:
                page.locator("#readiness-dialog").press("Escape")
            expect(page.locator("#readiness-dialog")).not_to_be_visible()
            # Native close() restores focus before its queued close event runs.
            # Observe the real completion condition; keep the exact empty oracle.
            expect(page.locator("#provider-load-status")).to_have_text("")
            expect(page.get_by_role("button", name="运行条件", exact=True)).to_be_focused()
            assert page.locator("#provider-load-status").inner_text() == ""
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_provider_load_late_reply_never_revives_closed_or_expired_dialog(interruption):
    pending, writes, errors = [], [], []
    expired = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("provider-lifetime.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/provider-load":
                    assert route.request.method == "POST" and route.request.post_data == "{}"
                    writes.append(path)
                    pending.append(route)
                elif expired:
                    route.fulfill(status=401, content_type="application/json",
                                  body='{"error":"ui_session_required"}')
                else:
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "tasks": [], "ready_for_live_e2e": False, "provider_state": "not_loaded",
                        "checks": {}, "message": "未调用模型", "submit_capability": False}))

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://provider-lifetime.test/**", route_request)
            page.goto("https://provider-lifetime.test/")
            page.locator("#message").fill("UNSENT_PROVIDER_INPUT")
            assert page.locator("#message").input_value() == "UNSENT_PROVIDER_INPUT"
            page.get_by_role("button", name="运行条件", exact=True).click()
            expect(page.locator("#provider-load")).to_be_enabled()
            with page.expect_request("https://provider-lifetime.test/ui/api/provider-load"):
                page.locator("#provider-load").click()
            assert len(pending) == 1
            expect(page.locator("#provider-load")).to_be_disabled()
            page.evaluate("readiness()")
            expect(page.locator("#provider-load")).to_be_disabled()
            page.evaluate("document.querySelector('#provider-load').dispatchEvent(new MouseEvent('click'))")
            assert writes == ["/ui/api/provider-load"]
            if interruption == "expired_session":
                expired = True
                page.evaluate("state()")
                expect(page.locator("#session-expired")).to_be_visible()
                expect(page.locator("#readiness-dialog")).not_to_be_visible()
            else:
                if interruption == "close":
                    page.locator("#readiness-close").click()
                else:
                    page.locator("#readiness-dialog").press("Escape")
                page.get_by_role("button", name="运行条件", exact=True).click()
                expect(page.locator("#provider-load")).to_be_disabled()
            with page.expect_response("https://provider-lifetime.test/ui/api/provider-load"):
                pending[0].fulfill(status=200, content_type="application/json", body=json.dumps({
                    "provider_state": "available", "provider_state_basis": "loaded_configuration",
                    "loaded": True, "final_click_actor": "user", "submit_capability": False}))
            page.wait_for_function("providerLoadBusy === false")
            assert page.locator("#provider-load-status").inner_text() == ""
            assert "已加载本机配置" not in page.locator("#readiness-dialog").inner_text()
            assert writes == ["/ui/api/provider-load"] and errors == []
            assert page.locator("#message").input_value() == "UNSENT_PROVIDER_INPUT"
            if interruption == "expired_session":
                expect(page.locator("#session-expired")).to_be_focused()
                expect(page.locator("#provider-load")).to_be_disabled()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        finally:
            browser.close()


@pytest.mark.parametrize("failure", ["transport", "malformed"])
def test_task_workspace_provider_load_uncertain_result_never_retries_or_claims_connection(failure):
    writes, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("provider-unknown.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/provider-load":
                    writes.append(route.request.post_data)
                    if failure == "transport":
                        route.abort("failed")
                    else:
                        route.fulfill(status=200, content_type="application/json",
                                      body='{"loaded":1,"provider_state":"available","api_key":"PRIVATE"}')
                else:
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "tasks": [], "ready_for_live_e2e": False, "provider_state": "not_loaded",
                        "checks": {}, "message": "连接尚未验证", "submit_capability": False}))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://provider-unknown.test/**", route_request)
            page.goto("https://provider-unknown.test/")
            page.get_by_role("button", name="运行条件", exact=True).click()
            expect(page.locator("#provider-load")).to_be_enabled()
            page.locator("#provider-load").click()
            expect(page.locator("#provider-load-status")).to_contain_text("检查结果未确认")
            expect(page.locator("#provider-load")).to_be_disabled()
            page.evaluate("readiness()")
            page.evaluate("document.querySelector('#provider-load').dispatchEvent(new MouseEvent('click'))")
            expect(page.locator("#provider-load")).to_be_disabled()
            assert writes == ["{}"] and errors == []
            assert "PRIVATE" not in page.locator("#readiness-dialog").inner_text()
            assert "已加载本机配置" not in page.locator("#readiness-dialog").inner_text()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        finally:
            browser.close()


@pytest.mark.parametrize("availability", [True, False, "constructor_failure"])
def test_task_workspace_explicit_provider_refresh_actual_service_replaces_only_cached_client(
    profile_setup_service, monkeypatch, availability
):
    from executor.autonomy import manager as manager_module
    from types import SimpleNamespace

    settings, queue, supervisor, base, tid, external_calls = profile_setup_service
    old = SimpleNamespace(available=False)
    supervisor.manager._provider = old
    loads, writes, errors = [], [], []
    before = (settings.PATH.read_bytes(), queue.tasks(), queue.recent_events(1000),
              {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()})
    def construct(configuration):
        assert configuration is supervisor.manager.settings
        loads.append("load")
        if availability == "constructor_failure":
            raise OSError("PRIVATE_REFRESH_KEYCHAIN")
        return SimpleNamespace(available=availability,
                               decide=lambda *_a, **_k: pytest.fail("refresh is not a model request"))
    monkeypatch.setattr(manager_module, "DeepSeekManagerProvider", construct)
    def readiness():
        current = supervisor.manager.loaded_provider_state()
        return {"ready_for_live_e2e": False, "provider_state": current["state"],
                "message": "真实站点条件尚未验证。", "checks": {"deepseek_available": current["available"]},
                "final_click_actor": "user", "submit_capability": False}
    monkeypatch.setattr(supervisor, "readiness", readiness)
    observed = supervisor.provider_refresh_state()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        try:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: writes.append((request.url, request.post_data))
                    if request.method == "POST" else None)
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            page.locator("#message").fill("UNSENT_REFRESH_INPUT")
            page.get_by_role("button", name="运行条件", exact=True).click()
            expect(page.locator("#provider-refresh")).to_be_enabled()
            expect(page.locator("#provider-load")).to_be_disabled()
            page.evaluate("readiness()")
            assert loads == [] and writes == []
            page.locator("#provider-refresh").click()
            expected_text = (
                "已重新读取本机配置" if availability is True else
                "已重新读取配置，但凭证暂不可用" if availability is False else
                "重新读取失败，旧客户端已停止使用")
            expect(page.locator("#provider-load-status")).to_contain_text(expected_text)
            assert len(writes) == 1 and writes[0][0] == base + "/ui/api/provider-refresh"
            assert json.loads(writes[0][1]) == {
                "expected_settings_version": observed["configuration_version"],
                "expected_refresh_revision": observed["refresh_revision"],
            }
            assert loads == ["load"] and supervisor.manager._provider is not old
            current = supervisor.provider_refresh_state()
            assert current["refresh_revision"] == observed["refresh_revision"] + 1
            assert current["provider_state"] == (
                "not_loaded" if availability == "constructor_failure" else
                "available" if availability is True else "unavailable")
            for _ in range(3):
                page.evaluate("readiness()")
            assert loads == ["load"] and len(writes) == 1 and external_calls == []
            assert "PRIVATE_" not in page.locator("#readiness-dialog").inner_text()
            assert "真实站点条件尚未验证" in page.locator("#readiness-summary").inner_text()
            assert page.locator("#message").input_value() == "UNSENT_REFRESH_INPUT"
            assert before == (settings.PATH.read_bytes(), queue.tasks(), queue.recent_events(1000),
                              {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()})
            page.locator("#readiness-close").click()
            expect(page.locator("#provider-load-status")).to_have_text("")
            expect(page.get_by_role("button", name="运行条件", exact=True)).to_be_focused()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("failure", ["transport", "malformed", "conflict", "readback_drift"])
def test_task_workspace_provider_refresh_uncertain_ack_never_retries_or_exposes_credentials(failure):
    writes, errors = [], []
    completed = False
    version = "f" * 64
    def observation(revision=0):
        return {"configuration_version": version, "refresh_revision": revision,
                "provider_state": "unavailable", "provider_state_basis": "loaded_configuration",
                "final_click_actor": "user", "submit_capability": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                nonlocal completed
                path = route.request.url.split("provider-refresh-unknown.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/provider-refresh" and route.request.method == "POST":
                    writes.append(json.loads(route.request.post_data))
                    completed = True
                    if failure == "transport":
                        route.abort("failed")
                    else:
                        result = {**observation(1), "refresh_status": "refreshed"}
                        if failure == "malformed":
                            result.update({"refresh_revision": True, "api_key": "PRIVATE_REFRESH_SECRET"})
                        route.fulfill(status=409 if failure == "conflict" else 200,
                                      content_type="application/json", body=json.dumps(result))
                elif path == "/ui/api/provider-refresh":
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(
                        observation(2 if completed and failure == "readback_drift" else 0)))
                else:
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "tasks": [], "provider_state": "unavailable", "ready_for_live_e2e": False,
                        "message": "连接尚未验证", "checks": {}, "submit_capability": False}))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://provider-refresh-unknown.test/**", route_request)
            page.goto("https://provider-refresh-unknown.test/")
            page.get_by_role("button", name="运行条件", exact=True).click()
            expect(page.locator("#provider-refresh")).to_be_enabled()
            page.locator("#provider-refresh").click()
            expect(page.locator("#provider-load-status")).to_contain_text("刷新结果未确认")
            expect(page.locator("#provider-refresh")).to_be_disabled()
            page.evaluate("readiness()")
            page.evaluate("document.querySelector('#provider-refresh').dispatchEvent(new MouseEvent('click'))")
            assert writes == [{"expected_settings_version": version, "expected_refresh_revision": 0}]
            assert "PRIVATE_REFRESH_SECRET" not in page.locator("#readiness-dialog").inner_text()
            assert "已重新读取本机配置" not in page.locator("#readiness-dialog").inner_text()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_provider_refresh_duplicate_click_and_late_reply_do_not_revive_dialog(interruption):
    pending, writes, errors = [], [], []
    expired = False
    version = "e" * 64
    observation = {"configuration_version": version, "refresh_revision": 0,
                   "provider_state": "unavailable", "provider_state_basis": "loaded_configuration",
                   "final_click_actor": "user", "submit_capability": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("provider-refresh-lifetime.test", 1)[-1]
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/provider-refresh" and route.request.method == "POST":
                    writes.append(json.loads(route.request.post_data))
                    pending.append(route)
                elif expired:
                    route.fulfill(status=401, content_type="application/json",
                                  body='{"error":"ui_session_required"}')
                elif path == "/ui/api/provider-refresh":
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(observation))
                else:
                    assert route.request.method == "GET"
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "tasks": [], "provider_state": "unavailable", "ready_for_live_e2e": False,
                        "message": "连接尚未验证", "checks": {}, "submit_capability": False}))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://provider-refresh-lifetime.test/**", route_request)
            page.goto("https://provider-refresh-lifetime.test/")
            page.locator("#message").fill("UNSENT_REFRESH_LIFETIME")
            page.get_by_role("button", name="运行条件", exact=True).click()
            expect(page.locator("#provider-refresh")).to_be_enabled()
            with page.expect_request(lambda request:
                    request.url == "https://provider-refresh-lifetime.test/ui/api/provider-refresh"
                    and request.method == "POST"):
                page.locator("#provider-refresh").click()
            expect(page.locator("#provider-refresh")).to_be_disabled()
            page.evaluate("document.querySelector('#provider-refresh').dispatchEvent(new MouseEvent('click'))")
            assert len(writes) == 1 and len(pending) == 1
            if interruption == "expired_session":
                expired = True
                page.evaluate("state()")
                expect(page.locator("#session-expired")).to_be_visible()
            elif interruption == "close":
                page.locator("#readiness-close").click()
            else:
                page.locator("#readiness-dialog").press("Escape")
            expect(page.locator("#readiness-dialog")).not_to_be_visible()
            if interruption != "expired_session":
                page.get_by_role("button", name="运行条件", exact=True).click()
                expect(page.locator("#provider-refresh")).to_be_disabled()
            with page.expect_response(lambda response:
                    response.url == "https://provider-refresh-lifetime.test/ui/api/provider-refresh"
                    and response.request.method == "POST"):
                pending[0].fulfill(status=200, content_type="application/json", body=json.dumps({
                    **observation, "refresh_revision": 1, "provider_state": "available",
                    "refresh_status": "refreshed"}))
            page.wait_for_function("providerRefreshBusy === false")
            assert page.locator("#provider-load-status").inner_text() == ""
            assert "已重新读取本机配置" not in page.locator("#readiness-dialog").inner_text()
            assert writes == [{"expected_settings_version": version, "expected_refresh_revision": 0}]
            assert page.locator("#message").input_value() == "UNSENT_REFRESH_LIFETIME"
            if interruption == "expired_session":
                expect(page.locator("#provider-refresh")).to_be_disabled()
                expect(page.locator("#session-expired")).to_be_focused()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()

@pytest.mark.parametrize("status,fenced", [
    ("idle", False), ("checking", True), ("updating", True),
    ("restarting", True), ("restart_required", True), ("success", False),
    ("up_to_date", False), ("failed", False),
])
def test_task_workspace_retired_update_is_readonly_and_preserves_legacy_fences(status, fenced):
    """Actual browser entry reads once and never re-enables the checkout writer."""
    requests, errors = [], []
    current = "idle"
    observation = {"status": status, "old_version": "PRIVATE_OLD_VERSION",
                   "new_version": "PRIVATE_NEW_VERSION", "reason": "PRIVATE_REASON"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("retired-update.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/update-status":
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(observation))
                else:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "tasks": [], "update": {"status": current}, "ready_for_live_e2e": False}))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://retired-update.test/**", route_request)
            page.goto("https://retired-update.test/")
            page.locator("#message").fill("UNSENT_UPDATE_INPUT")
            current = status
            page.get_by_role("button", name="更新状态", exact=True).click()
            expect(page.locator("#update-dialog")).to_be_visible()
            expect(page.locator("#update-close")).to_be_focused()
            page.wait_for_function("updateReadBusy === false")
            assert requests.count(("GET", "/ui/api/update-status")) == 1
            text = page.locator("#update-dialog").inner_text()
            assert "旧版 Git 更新入口已停用" in text
            assert "旧版状态记录不能证明当前安装包" in text
            assert "PRIVATE_" not in text
            assert "UNSENT_UPDATE_INPUT" not in text
            assert page.locator("#message").input_value() == "UNSENT_UPDATE_INPUT"
            assert page.locator("#message").is_disabled() is fenced
            assert page.locator("#send").is_disabled() is fenced
            if fenced:
                assert "任务写入保持暂停" in page.locator("#update-observation").inner_text()
            elif status == "success":
                assert "不能据此确认当前安装包版本" in page.locator("#update-observation").inner_text()
            elif status == "up_to_date":
                assert "不能据此确认当前安装包已是最新版本" in page.locator("#update-observation").inner_text()
            # No timer-based update polling exists. Existing entry names are
            # read-only compatibility, including synthetic duplicate dispatch.
            page.evaluate("document.getElementById('update').dispatchEvent(new MouseEvent('click'))")
            assert requests.count(("GET", "/ui/api/update-status")) == 1
            page.locator("#update-refresh").click()
            page.wait_for_function("updateReadBusy === false")
            assert requests.count(("GET", "/ui/api/update-status")) == 2
            page.locator("#update-close").click()
            expect(page.locator("#update-dialog")).not_to_be_visible()
            expect(page.locator("#update")).to_be_focused()
            assert page.locator("#update-observation").inner_text() == ""
            page.evaluate("pollUpdate()")
            assert requests.count(("GET", "/ui/api/update-status")) == 2
            assert all(method == "GET" for method, _ in requests)
            assert all(path != "/ui/api/update" for _, path in requests)
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("failure", ["transport", "http", "malformed", "private_status"])
def test_task_workspace_retired_update_uncertain_read_never_retries_or_clears_fence(failure):
    requests, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("retired-update-unknown.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/update-status":
                    if failure == "transport":
                        route.abort("failed")
                    else:
                        route.fulfill(status=503 if failure == "http" else 200,
                            content_type="application/json", body=json.dumps(
                                [] if failure == "malformed" else {"status": "PRIVATE_STATE"}))
                else:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "tasks": [], "update": {"status": "restart_required"},
                        "ready_for_live_e2e": False}))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://retired-update-unknown.test/**", route_request)
            page.goto("https://retired-update-unknown.test/")
            expect(page.locator("#send")).to_be_disabled()
            page.get_by_role("button", name="更新状态", exact=True).click()
            page.wait_for_function("updateReadBusy === false")
            expect(page.locator("#update-observation")).to_contain_text("更新状态未确认")
            assert "PRIVATE_STATE" not in page.locator("#update-dialog").inner_text()
            expect(page.locator("#message")).to_be_disabled()
            expect(page.locator("#send")).to_be_disabled()
            assert requests.count(("GET", "/ui/api/update-status")) == 1
            page.locator("#update-close").click()
            page.evaluate("pollUpdate()")
            assert requests.count(("GET", "/ui/api/update-status")) == 1
            assert all(method == "GET" for method, _ in requests)
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_retired_update_late_reply_never_revives_dialog_or_replays(interruption):
    pending, requests, errors = [], [], []
    expired = False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("retired-update-lifetime.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/update-status":
                    pending.append(route)
                elif expired:
                    route.fulfill(status=401, content_type="application/json",
                                  body='{"error":"ui_session_required"}')
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"tasks":[],"update":{"status":"idle"},"ready_for_live_e2e":false}')
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://retired-update-lifetime.test/**", route_request)
            page.goto("https://retired-update-lifetime.test/")
            page.locator("#message").fill("UNSENT_UPDATE_LIFETIME")
            with page.expect_request("https://retired-update-lifetime.test/ui/api/update-status"):
                page.get_by_role("button", name="更新状态", exact=True).click()
            expect(page.locator("#update-refresh")).to_be_disabled()
            page.evaluate("document.getElementById('update-refresh').dispatchEvent(new MouseEvent('click'))")
            assert len(pending) == 1
            if interruption == "expired_session":
                expired = True
                page.evaluate("state()")
                expect(page.locator("#session-expired")).to_be_visible()
            elif interruption == "close":
                page.locator("#update-close").click()
            else:
                page.locator("#update-dialog").press("Escape")
            expect(page.locator("#update-dialog")).not_to_be_visible()
            with page.expect_response("https://retired-update-lifetime.test/ui/api/update-status"):
                pending[0].fulfill(status=200, content_type="application/json",
                                  body='{"status":"success","reason":"PRIVATE_LATE_REPLY"}')
            page.evaluate("async () => { await Promise.resolve(); await pollUpdate(); }")
            assert page.locator("#update-observation").inner_text() == ""
            expect(page.locator("#update-dialog")).not_to_be_visible()
            assert page.evaluate("legacyUpdateObservation.status") == "idle"
            assert page.locator("#message").input_value() == "UNSENT_UPDATE_LIFETIME"
            assert requests.count(("GET", "/ui/api/update-status")) == 1
            assert all(method == "GET" for method, _ in requests)
            if interruption == "expired_session":
                expect(page.locator("#session-expired")).to_be_focused()
                expect(page.locator("#update")).to_be_disabled()
                assert page.locator("#message").get_attribute("readonly") is not None
                checkpoint = list(requests)
                page.evaluate("async () => { await startUpdate(); await pollUpdate(); }")
                assert requests == checkpoint
            else:
                expect(page.locator("#update")).to_be_focused()
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            assert errors == []
        finally:
            browser.close()

def test_task_workspace_retired_update_same_turn_close_reopen_preserves_new_epoch():
    """Queued close/late reads cannot cancel or populate a new foreground read."""
    pending, requests, errors = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            def route_request(route):
                path = route.request.url.split("retired-update-reopen.test", 1)[-1]
                requests.append((route.request.method, path))
                if path == "/":
                    route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
                elif path == "/ui/api/update-status":
                    pending.append(route)
                    # Request emission precedes Python route admission. Publish
                    # this causal barrier only after the held Route exists.
                    page.evaluate(
                        "(count) => { window.__testUpdateRouteAdmissions = count; }",
                        len(pending),
                    )
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body='{"tasks":[],"update":{"status":"idle"},"ready_for_live_e2e":false}')
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("https://retired-update-reopen.test/**", route_request)
            page.goto("https://retired-update-reopen.test/")
            page.locator("#message").fill("UNSENT_NEW_UPDATE_EPOCH")
            with page.expect_request("https://retired-update-reopen.test/ui/api/update-status"):
                page.locator("#update").click()
            page.wait_for_function("window.__testUpdateRouteAdmissions === 1")
            assert len(pending) == 1
            # One actual event-loop task: the old close event is still queued
            # when the new dialog begins, without any test timing delays.
            with page.expect_request("https://retired-update-reopen.test/ui/api/update-status"):
                page.evaluate("() => { closeUpdateDialog(); void startUpdate(); }")
            page.wait_for_function("window.__testUpdateRouteAdmissions === 2")
            assert len(pending) == 2
            expect(page.locator("#update-dialog")).to_be_visible()
            expect(page.locator("#update-refresh")).to_be_disabled()
            with page.expect_response("https://retired-update-reopen.test/ui/api/update-status"):
                pending[0].fulfill(status=200, content_type="application/json",
                                  body='{"status":"restart_required","reason":"PRIVATE_OLD_EPOCH"}')
            page.evaluate("async () => { await Promise.resolve(); }")
            expect(page.locator("#update-refresh")).to_be_disabled()
            expect(page.locator("#send")).to_be_enabled()
            assert page.evaluate("legacyUpdateObservation.status") == "idle"
            assert "PRIVATE_" not in page.locator("#update-dialog").inner_text()
            with page.expect_response("https://retired-update-reopen.test/ui/api/update-status"):
                pending[1].fulfill(status=200, content_type="application/json", body='{"status":"idle"}')
            page.wait_for_function("updateReadBusy === false")
            expect(page.locator("#update-observation")).to_contain_text("这不代表已检查新版本")
            expect(page.locator("#update-refresh")).to_be_enabled()
            page.locator("#update-close").click()
            assert page.locator("#update-observation").inner_text() == ""
            expect(page.locator("#update")).to_be_focused()
            assert page.locator("#message").input_value() == "UNSENT_NEW_UPDATE_EPOCH"
            assert requests.count(("GET", "/ui/api/update-status")) == 2
            assert all(method == "GET" for method, _ in requests)
            assert errors == []
        finally:
            browser.close()


_PREPARATION_FIELDS = {
    "identity.full_name": "姓名", "identity.phone": "电话号码", "identity.email": "邮箱号码",
    "identity.gender": "性别", "education.highest.degree": "最高学历",
    "education.highest.school": "毕业院校", "education.highest.college": "学院",
    "education.highest.major": "专业", "education.highest.graduation_date": "毕业时间",
    "language.cet6.level": "英语证书情况（本地六级记录）", "language.cet6.score": "英语考级分数（本地六级记录）",
    "preferences.preferred_cities": "期望工作城市",
}
_PREPARATION_SOURCE = "https://www.qiyunfang.com/h-col-124.html"


def _preparation_payload(task_id="prep-a", revision=7):
    return {
        "task_id": task_id, "task_revision": revision, "mode": "LOCAL_PREPARATION_ONLY",
        "profile": {"status": "available", "version": "a" * 64},
        "resume": {"status": "local_version_matches", "version": "b" * 64},
        "contract": {"matched": True, "id": "qiyunfang-wuhan-implementation-v1",
                     "source_url": _PREPARATION_SOURCE, "observed_at": "2026-09-30",
                     "coverage": "observed_fields_only", "requiredness": "UNVERIFIED",
                     "freshness": "cached_observation"},
        "items": [{"key": key, "label": "PRIVATE_UNTRUSTED_LABEL",
                   "status": ["recorded_locally", "missing", "needs_review"][index % 3],
                   "note": "PRIVATE_UNTRUSTED_NOTE"}
                  for index, key in enumerate(_PREPARATION_FIELDS)],
        "manual_steps": ["PRIVATE_UNTRUSTED_INSTRUCTION"],
        "capabilities": {"live_write": False, "submit": False,
                         "account_verified": False, "server_draft_verified": False},
        "identity": "PRIVATE_ID_123456789", "path": "/private/PRIVATE_RESUME.pdf",
        "target_url": "https://unreviewed.test/PRIVATE_TARGET",
    }


@pytest.fixture
def preparation_ui():
    """Synthetic routes cover lifecycle races without visiting a recruiting site."""
    from copy import deepcopy
    from urllib.parse import urlsplit
    tasks = [
        {"task_id": "prep-a", "company": "武汉启云方科技有限公司", "role": "应用实施工程师（武汉）",
         "stage": "BLOCKED", "blocker": "account_identity_unverified", "revision": 7},
        {"task_id": "prep-b", "company": "Synthetic B", "role": "Engineer",
         "stage": "BLOCKED", "blocker": "draft_persistence_unverified", "revision": 9},
    ]
    observed = {"tasks": tasks, "payload": _preparation_payload(), "hold": False,
                "expired": False, "status": 200, "requests": [], "external": [],
                "pending": [], "errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 600, "height": 800})
        page = context.new_page()
        page.add_init_script("""
            window.__copied = [];
            Object.defineProperty(navigator, 'clipboard', {configurable:true,
                value:{writeText:async text=>window.__copied.push(text)}});
        """)

        def route_request(route):
            request = route.request
            parsed = urlsplit(request.url)
            observed["requests"].append((request.method, request.url, request.post_data))
            if parsed.netloc != "preparation.test":
                observed["external"].append((request.url, request.headers))
                route.fulfill(status=200, content_type="text/html; charset=utf-8",
                              body='<meta charset="utf-8"><link rel="icon" href="data:,"><p>合成官方页面</p>')
            elif parsed.path == "/":
                route.fulfill(status=200, content_type="text/html", body=DASHBOARD_HTML)
            elif observed["expired"]:
                route.fulfill(status=401, content_type="application/json",
                              body='{"error":"ui_session_required"}')
            elif parsed.path == "/ui/api/task-preparation":
                payload = deepcopy(observed["payload"])
                if observed["hold"]:
                    observed["pending"].append((route, payload))
                    page.evaluate("count => {window.__preparationAdmissions=count}", len(observed["pending"]))
                else:
                    route.fulfill(status=observed["status"], content_type="application/json",
                                  body=json.dumps(payload))
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "tasks": observed["tasks"], "ready_for_live_e2e": False,
                    "ui_context": {"task_id": "prep-a", "revision": 0}}))

        page.on("pageerror", lambda error: observed["errors"].append(str(error)))
        context.route("**/*", route_request)
        page.goto("https://preparation.test/")
        expect(page.locator('[data-task-preparation]')).to_have_count(2)
        try:
            yield page, observed
        finally:
            browser.close()


def _preparation_requests(observed):
    return [request for request in observed["requests"] if "/ui/api/task-preparation?" in request[1]]


def _assert_preparation_read_only(page, observed):
    assert all(method == "GET" and body is None for method, _, body in observed["requests"])
    assert page.evaluate("window.__copied") == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert "PRIVATE_" not in page.locator("body").inner_text()
    assert observed["errors"] == []


def test_task_workspace_preparation_card_precheck_and_explicit_manual_handoff(preparation_ui):
    page, observed = preparation_ui
    original_tasks = json.dumps(observed["tasks"], sort_keys=True)
    page.locator("#message").fill("UNSENT_PREPARATION")
    trigger = page.locator('[data-task-card="prep-a"] [data-task-preparation]')
    assert _preparation_requests(observed) == []
    trigger.click()
    dialog = page.locator("#preparation-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#preparation-close")).to_be_focused()
    expect(page.locator("#preparation-status")).to_contain_text("最近一次本地观察")
    expect(page.locator("#preparation-checks li")).to_have_count(12)
    for label in _PREPARATION_FIELDS.values():
        expect(page.locator("#preparation-checks")).to_contain_text(label)
    for label in ("本机已记录（仍需核对）", "缺少记录", "需本人核对"):
        expect(page.locator("#preparation-checks")).to_contain_text(label)
    expect(page.locator("#preparation-profile")).to_contain_text("aaaaaaaaaaaa")
    expect(page.locator("#preparation-resume")).to_contain_text("bbbbbbbbbbbb")
    expect(dialog).to_contain_text("必填规则尚未核实")
    expect(dialog).to_contain_text("投递岗位2仅在本人确认需要时填写")
    expect(dialog).to_contain_text("本应用尚未核验账号或服务端草稿")
    expect(dialog).to_contain_text("最终提交始终只能由本人完成")
    expect(dialog).to_contain_text("缺少记录不代表没有英语证书")
    expect(dialog).to_contain_text("如本机窗口无法打开链接，请在自己的浏览器打开这个官网地址")
    expect(page.locator("#preparation-source-address")).to_have_text(_PREPARATION_SOURCE)
    assert page.locator("#preparation-dialog").evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    assert page.locator("#preparation-dialog").bounding_box()["width"] <= 568
    assert page.locator("#preparation-dialog input, #preparation-dialog textarea, #preparation-dialog img").count() == 0
    assert len(_preparation_requests(observed)) == 1
    assert _preparation_requests(observed)[0][1].endswith("task_id=prep-a&expected_revision=7")
    assert observed["external"] == []
    source = page.get_by_role("link", name="尝试打开已核对的官方页面", exact=True)
    expect(source).to_have_attribute("href", _PREPARATION_SOURCE)
    expect(source).to_have_attribute("rel", "noopener noreferrer")
    expect(source).to_have_attribute("referrerpolicy", "no-referrer")
    # Opt-in hosted visual review: synthetic dialog pixels only. No tracing,
    # HTML, cookies or storage artifacts are collected by this hook.
    _assert_preparation_read_only(page, observed)
    assert "/private/" not in dialog.inner_text()
    assert "unreviewed.test" not in dialog.inner_text()
    import os
    from pathlib import Path
    screenshot_dir = os.environ.get("JAE_UI_SCREENSHOT_DIR")
    if screenshot_dir:
        destination = Path(screenshot_dir)
        destination.mkdir(parents=True, exist_ok=True)
        dialog.screenshot(path=str(destination / "preparation-dialog.png"), animations="disabled")
    with page.context.expect_page() as opened:
        source.click()
    external_page = opened.value
    expect(external_page.locator("body")).to_contain_text("合成官方页面")
    assert external_page.evaluate("window.opener === null")
    assert [url for url, _ in observed["external"]] == [_PREPARATION_SOURCE]
    assert "referer" not in observed["external"][0][1]
    external_page.close()
    page.locator("#preparation-close").click()
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    assert page.locator("#preparation-checks").inner_text() == ""
    assert page.locator("#preparation-source").get_attribute("href") is None
    assert page.locator("#message").input_value() == "UNSENT_PREPARATION"
    assert json.dumps(observed["tasks"], sort_keys=True) == original_tasks
    _assert_preparation_read_only(page, observed)


@pytest.mark.parametrize("interruption", ["close", "escape", "switch", "revision", "expired_session"])
def test_task_workspace_preparation_delayed_reply_clears_on_interruption(preparation_ui, interruption):
    page, observed = preparation_ui
    observed["hold"] = True
    page.locator("#message").fill("UNSENT_PREPARATION_LATE")
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    page.wait_for_function("window.__preparationAdmissions === 1")
    expect(page.locator("#preparation-refresh")).to_be_disabled()
    page.evaluate("""() => {
        document.querySelector('[data-task-card="prep-a"] [data-task-preparation]').click();
        document.querySelector('#preparation-refresh').dispatchEvent(new MouseEvent('click'));
        void readTaskPreparation();
    }""")
    assert len(observed["pending"]) == 1
    if interruption == "close":
        page.locator("#preparation-close").click()
    elif interruption == "escape":
        page.locator("#preparation-dialog").press("Escape")
    elif interruption == "switch":
        page.evaluate("document.querySelector('[data-task-select=\"prep-b\"]').click()")
        expect(page.locator('[data-task-select="prep-b"]')).to_have_attribute("aria-pressed", "true")
    else:
        if interruption == "revision":
            observed["tasks"][0]["revision"] += 1
        else:
            observed["expired"] = True
        page.evaluate("state()")
    expect(page.locator("#preparation-dialog")).not_to_be_visible()
    route, payload = observed["pending"][0]
    with page.expect_response("**/ui/api/task-preparation?*"):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
    page.evaluate("async () => {await Promise.resolve(); await readTaskPreparation()}")
    expect(page.locator("#preparation-dialog")).not_to_be_visible()
    assert page.locator("#preparation-profile").inner_text() == ""
    assert page.locator("#preparation-resume").inner_text() == ""
    assert page.locator("#preparation-checks").inner_text() == ""
    assert page.locator("#preparation-source").get_attribute("href") is None
    assert len(_preparation_requests(observed)) == 1
    assert observed["external"] == []
    assert page.locator("#message").input_value() == "UNSENT_PREPARATION_LATE"
    if interruption == "expired_session":
        expect(page.locator("#session-expired")).to_be_focused()
        expect(page.locator('[data-task-preparation]').first).to_be_disabled()
        page.evaluate("openTaskPreparation('prep-a',7)")
        assert len(_preparation_requests(observed)) == 1
    _assert_preparation_read_only(page, observed)


def test_task_workspace_preparation_old_reply_cannot_populate_new_task_or_reopened_dialog(preparation_ui):
    page, observed = preparation_ui
    observed["hold"] = True
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    page.wait_for_function("window.__preparationAdmissions === 1")
    observed["payload"] = _preparation_payload("prep-b", 9)
    observed["payload"]["profile"]["version"] = "c" * 64
    page.evaluate("() => {closePreparationDialog(); openTaskPreparation('prep-b',9)}")
    page.wait_for_function("window.__preparationAdmissions === 2")
    old_route, old_payload = observed["pending"][0]
    with page.expect_response("**/ui/api/task-preparation?task_id=prep-a&*"):
        old_route.fulfill(status=200, content_type="application/json", body=json.dumps(old_payload))
    page.evaluate("async () => {await Promise.resolve()}")
    expect(page.locator("#preparation-dialog")).to_be_visible()
    expect(page.locator("#preparation-refresh")).to_be_disabled()
    assert page.locator("#preparation-profile").inner_text() == ""
    new_route, new_payload = observed["pending"][1]
    new_route.fulfill(status=200, content_type="application/json", body=json.dumps(new_payload))
    expect(page.locator("#preparation-profile")).to_contain_text("cccccccccccc")
    assert "aaaaaaaaaaaa" not in page.locator("#preparation-dialog").inner_text()
    expect(page.locator('[data-task-select="prep-b"]')).to_have_attribute("aria-pressed", "true")
    page.locator("#preparation-close").click()
    expect(page.locator('[data-task-card="prep-b"] [data-task-preparation]')).to_be_focused()
    assert len(_preparation_requests(observed)) == 2 and observed["external"] == []
    _assert_preparation_read_only(page, observed)


@pytest.mark.parametrize("failure", ["stale_revision", "wrong_task", "wrong_revision", "unchecked_source", "capability", "malformed", "http"])
def test_task_workspace_preparation_refresh_discards_old_observation_and_fails_closed(preparation_ui, failure):
    page, observed = preparation_ui
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    expect(page.locator("#preparation-profile")).to_contain_text("aaaaaaaaaaaa")
    if failure == "stale_revision":
        observed["status"] = 409
    elif failure == "wrong_task":
        observed["payload"]["task_id"] = "prep-b"
    elif failure == "wrong_revision":
        observed["payload"]["task_revision"] = 8
    elif failure == "unchecked_source":
        observed["payload"]["contract"]["source_url"] = "https://unreviewed.test/PRIVATE_TARGET"
    elif failure == "capability":
        observed["payload"]["capabilities"]["submit"] = True
    elif failure == "malformed":
        observed["payload"] = {"message": "PRIVATE_ERROR"}
    else:
        observed["status"] = 503
    page.locator("#preparation-refresh").click()
    expect(page.locator("#preparation-status")).to_contain_text(
        "版本已变化" if failure == "stale_revision" else "暂不可用")
    assert page.locator("#preparation-profile").inner_text() == ""
    assert page.locator("#preparation-checks").inner_text() == ""
    assert page.locator("#preparation-source").get_attribute("href") is None
    expect(page.locator("#preparation-observation")).not_to_be_visible()
    assert len(_preparation_requests(observed)) == 2 and observed["external"] == []
    if failure == "stale_revision":
        expect(page.locator("#preparation-refresh")).to_be_disabled()
        page.evaluate("readTaskPreparation()")
        assert len(_preparation_requests(observed)) == 2
    _assert_preparation_read_only(page, observed)


def test_task_workspace_preparation_nonmatching_task_has_no_form_contract_or_source(preparation_ui):
    page, observed = preparation_ui
    payload = observed["payload"]
    payload["contract"] = {"matched": False, "id": None, "source_url": None, "observed_at": None,
                           "coverage": "unavailable", "requiredness": "UNVERIFIED", "freshness": "unavailable"}
    payload["items"] = []
    payload["resume"] = {"status": "missing", "version": None}
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    expect(page.locator("#preparation-unmatched")).to_be_visible()
    expect(page.locator("#preparation-profile")).to_contain_text("aaaaaaaaaaaa")
    expect(page.locator("#preparation-resume")).to_contain_text("缺少本机简历记录")
    expect(page.locator("#preparation-contract")).not_to_be_visible()
    assert page.locator("#preparation-source").get_attribute("href") is None
    assert page.locator("#preparation-dialog a[href]").count() == 0
    assert page.locator("#preparation-checks li").count() == 0
    assert observed["external"] == []
    _assert_preparation_read_only(page, observed)


def test_task_workspace_preparation_reload_does_not_restore_observation_or_start_request(preparation_ui):
    page, observed = preparation_ui
    original = json.dumps(observed["tasks"], sort_keys=True)
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    expect(page.locator("#preparation-profile")).to_contain_text("aaaaaaaaaaaa")
    page.reload()
    expect(page.locator('[data-task-card="prep-a"] [data-task-preparation]')).to_be_visible()
    expect(page.locator("#preparation-dialog")).not_to_be_visible()
    assert page.locator("#preparation-profile").inner_text() == ""
    assert page.locator("#preparation-source").get_attribute("href") is None
    assert len(_preparation_requests(observed)) == 1
    observed["payload"]["profile"]["version"] = "c" * 64
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    expect(page.locator("#preparation-profile")).to_contain_text("cccccccccccc")
    assert "aaaaaaaaaaaa" not in page.locator("#preparation-dialog").inner_text()
    assert len(_preparation_requests(observed)) == 2
    assert json.dumps(observed["tasks"], sort_keys=True) == original
    assert observed["external"] == []
    _assert_preparation_read_only(page, observed)


@pytest.mark.parametrize("preserve_surface", ["unsent_answer", "private_review"])
def test_task_workspace_preparation_revision_clears_even_when_card_render_is_deferred(preparation_ui, preserve_surface):
    page, observed = preparation_ui
    task = observed["tasks"][0]
    if preserve_surface == "unsent_answer":
        task.update(stage="NEEDS_USER_INPUT", blocker="unknown_facts", unresolved_keys=["motivation"],
                    question_context={"status": "current", "items": [
                        {"key": "motivation", "label": "申请原因", "required": True}]})
    else:
        task.update(stage="READY_TO_SUBMIT", review_values_available=True,
                    review_summary={"status": "last_verified"})
    page.evaluate("state()")
    if preserve_surface == "unsent_answer":
        page.get_by_role("textbox", name="申请原因", exact=True).fill("UNSENT_PREPARATION_ANSWER")
    else:
        page.evaluate("""() => {
            const panel=document.querySelector('[data-private-review-panel]');
            panel.hidden=false;panel.dataset.privateReviewOpen='prep-a';panel.dataset.revision='7';
            panel.textContent='SYNTHETIC_PREVIOUS_REVIEW';
        }""")
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    expect(page.locator("#preparation-profile")).to_contain_text("aaaaaaaaaaaa")
    task["revision"] += 1
    page.evaluate("state()")
    expect(page.locator("#preparation-dialog")).not_to_be_visible()
    assert page.locator("#preparation-profile").inner_text() == ""
    assert page.locator("#preparation-source").get_attribute("href") is None
    if preserve_surface == "unsent_answer":
        assert page.get_by_role("textbox", name="申请原因", exact=True).input_value() == "UNSENT_PREPARATION_ANSWER"
    assert len(_preparation_requests(observed)) == 1 and observed["external"] == []
    observed["payload"]["task_revision"] = 8
    page.locator('[data-task-card="prep-a"] [data-task-preparation]').click()
    expect(page.locator("#preparation-profile")).to_contain_text("aaaaaaaaaaaa")
    assert _preparation_requests(observed)[-1][1].endswith("task_id=prep-a&expected_revision=8")
    assert len(_preparation_requests(observed)) == 2
    _assert_preparation_read_only(page, observed)


def test_task_workspace_preparation_actual_authenticated_service_preserves_task_and_profile_binding(
    profile_setup_service, tmp_path, monkeypatch
):
    """Real GET admission, original profile authority, reload and service restart."""
    import hashlib
    import threading
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlsplit
    from executor.autonomy.queue import TaskSpec, TaskQueue
    from executor.autonomy.worker import Worker
    from executor.autonomy.manager import ManagerController
    from executor.autonomy.supervisor import Supervisor, create_server
    settings, queue, supervisor, base, old_tid, calls = profile_setup_service
    bound_profile = tmp_path.resolve() / "PRIVATE_BOUND_PROFILE.json"
    resume = tmp_path.resolve() / "PRIVATE_RESUME_NAME.pdf"
    resume.write_bytes(b"SYNTHETIC_PRIVATE_RESUME_BYTES")
    resume.chmod(0o600)
    resume_digest = hashlib.sha256(resume.read_bytes()).hexdigest()
    profile = {"fields": {"identity.full_name": {"value": "PRIVATE_APPLICANT"},
                          "identity.id_number": {"value": "PRIVATE_ID_123456789"},
                          "identity.gender": {"value": {"uncertain": True}}},
               "assets": {"resume": {"path": str(resume), "kind": "resume_pdf", "sha256": resume_digest}}}
    bound_profile.write_text(json.dumps(profile), encoding="utf-8")
    bound_profile.chmod(0o600)
    profile_digest = hashlib.sha256(bound_profile.read_bytes()).hexdigest()
    task = queue.enqueue(TaskSpec(company="武汉启云方科技有限公司", role="应用实施工程师（武汉）",
                                  target_url=_PREPARATION_SOURCE, profile_ref=str(bound_profile),
                                  live_authorized=False))
    task_id = task["task_id"]
    # Selecting a new default in the local settings cannot rebind this task.
    newer_profile = tmp_path.resolve() / "PRIVATE_FUTURE_ONLY_PROFILE.json"
    newer_profile.write_text('{"fields":{"identity.full_name":{"value":"PRIVATE_FUTURE_ONLY"}}}')
    newer_profile.chmod(0o600)
    new_settings = {**settings.load_settings(), "profile_path": str(newer_profile)}
    settings.save_settings(new_settings)
    supervisor.manager.settings = new_settings
    supervisor.worker.settings = new_settings
    baseline = queue.get(task_id), bound_profile.read_bytes(), resume.read_bytes(), settings.PATH.read_bytes()
    requests, errors, external = [], [], []
    restarted_server = None
    restarted_thread = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        page.add_init_script("""
            window.__copied=[];
            Object.defineProperty(navigator,'clipboard',{configurable:true,
                value:{writeText:async text=>window.__copied.push(text)}});
        """)

        def local_only(route):
            url = route.request.url
            if urlsplit(url).hostname != "127.0.0.1":
                external.append(url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", local_only)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append((request.method, request.url)))
        try:
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            trigger = page.locator(f'[data-task-card="{task_id}"] [data-task-preparation]')
            trigger.click()
            expect(page.locator("#preparation-status")).to_contain_text("最近一次本地观察")
            expect(page.locator("#preparation-profile")).to_contain_text(profile_digest[:12])
            expect(page.locator("#preparation-resume")).to_contain_text(resume_digest[:12])
            expect(page.locator("#preparation-checks li")).to_have_count(12)
            assert "PRIVATE_" not in page.locator("body").inner_text()
            assert str(tmp_path) not in page.locator("#preparation-dialog").inner_text()
            assert page.locator("#preparation-source").get_attribute("href") == _PREPARATION_SOURCE
            assert queue.get(task_id) == baseline[0]
            assert (bound_profile.read_bytes(), resume.read_bytes(), settings.PATH.read_bytes()) == baseline[1:]
            assert calls == [] and external == []
            prep_requests = lambda: [url for _, url in requests if "/ui/api/task-preparation?" in url]
            assert len(prep_requests()) == 1
            page.reload()
            expect(trigger).to_be_visible()
            expect(page.locator("#preparation-dialog")).not_to_be_visible()
            assert page.locator("#preparation-profile").inner_text() == ""
            assert len(prep_requests()) == 1
            trigger.click()
            expect(page.locator("#preparation-profile")).to_contain_text(profile_digest[:12])
            assert len(prep_requests()) == 2
            # A newly constructed service reads the persisted task. The old
            # dialog is not a persisted observation or a source of authority.
            fresh_queue = TaskQueue(queue.root)
            fresh_worker = Worker(fresh_queue, settings=new_settings)
            provider = SimpleNamespace(available=False, decide=lambda *_: calls.append("provider"))
            fresh_manager = ManagerController(fresh_queue, fresh_worker, provider=provider, settings=new_settings)
            fresh_supervisor = Supervisor(fresh_queue, worker=fresh_worker, manager=fresh_manager, token="y" * 40)
            monkeypatch.setattr(fresh_supervisor, "readiness", lambda: {
                "ready_for_live_e2e": False, "message": "合成重启检查", "submit_capability": False})
            restarted_server = create_server(fresh_supervisor, port=0)
            restarted_thread = threading.Thread(target=restarted_server.serve_forever, daemon=True)
            restarted_thread.start()
            restarted_base = f"http://127.0.0.1:{restarted_server.server_address[1]}"
            page.goto(restarted_base + "/ui-login?ticket=" + fresh_supervisor.issue_ui_ticket())
            expect(trigger).to_be_visible()
            expect(page.locator("#preparation-dialog")).not_to_be_visible()
            assert page.locator("#preparation-checks").inner_text() == ""
            assert len(prep_requests()) == 2
            trigger.click()
            expect(page.locator("#preparation-profile")).to_contain_text(profile_digest[:12])
            expect(page.locator("#preparation-resume")).to_contain_text(resume_digest[:12])
            assert len(prep_requests()) == 3
            assert fresh_queue.get(task_id) == baseline[0]
            assert (bound_profile.read_bytes(), resume.read_bytes(), settings.PATH.read_bytes()) == baseline[1:]
            assert calls == [] and errors == [] and external == []
            assert all(method == "GET" for method, _ in requests)
            assert "PRIVATE_" not in page.locator("body").inner_text()
            assert page.evaluate("window.__copied") == []
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
        finally:
            browser.close()
            if restarted_server:
                restarted_server.shutdown()
                restarted_server.server_close()
                restarted_thread.join()


@pytest.mark.parametrize("discovery_status", ["VERIFIED", "UNSUPPORTED"])
def test_task_workspace_duplicate_binding_requires_explicit_review_and_preserves_new_form(preparation_ui, discovery_status):
    page, observed = preparation_ui
    original = json.dumps(observed["tasks"], sort_keys=True)
    submissions = []
    def submit(route):
        submissions.append((route.request.method, route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "task_id":"prep-a", "revision":7, "task_binding":"existing_different",
            "discovery":{"status":discovery_status},
            "profile_ref":"/private/PRIVATE_DO_NOT_RENDER.json"}))
    page.route("https://preparation.test/ui/api/tasks", submit)
    page.locator('[data-task-select="prep-b"]').click()
    expect(page.locator('[data-task-card="prep-b"]')).to_have_attribute("data-current-task", "true")
    page.locator('#newtask input[name="company"]').fill("Requested Co")
    page.locator('#newtask input[name="role"]').fill("Requested Other Role")
    page.locator('#newtask input[name="target_url"]').fill(_PREPARATION_SOURCE)
    page.locator('#newtask button').click()
    review = page.get_by_role("button", name="查看已有任务", exact=True)
    expect(review).to_be_visible()
    expect(page.locator('#candidates')).to_contain_text("仍保留原岗位、资料和授权")
    expect(page.locator('#candidates')).to_contain_text("本次没有替换或启动任务")
    expect(page.locator('#newtask input[name="role"]')).to_have_value("Requested Other Role")
    expect(page.locator('#newtask input[name="company"]')).to_have_value("Requested Co")
    expect(page.locator('[data-task-card="prep-b"]')).to_have_attribute("data-current-task", "true")
    assert "PRIVATE_DO_NOT_RENDER" not in page.locator('body').inner_text()
    assert "/private/" not in page.locator('#candidates').inner_text()
    assert len(submissions) == 1 and submissions[0][0] == "POST"
    review.click()
    expect(page.locator('[data-task-card="prep-a"]')).to_have_attribute("data-current-task", "true")
    expect(page.locator('[data-task-select="prep-a"]')).to_be_focused()
    assert len(submissions) == 1
    assert all(method == "GET" for method, _, _ in observed["requests"])
    assert json.dumps(observed["tasks"], sort_keys=True) == original
    assert observed["external"] == [] and observed["errors"] == []
    assert page.evaluate("window.__copied") == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_task_workspace_exact_binding_success_does_not_claim_a_new_duplicate(preparation_ui):
    page, observed = preparation_ui
    submissions = []
    def submit(route):
        submissions.append(route.request.post_data)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "task_id":"prep-a", "revision":7, "task_binding":"requested",
            "discovery":{"status":"VERIFIED"}}))
    page.route("https://preparation.test/ui/api/tasks", submit)
    page.locator('#newtask input[name="company"]').fill("武汉启云方科技有限公司")
    page.locator('#newtask input[name="role"]').fill("应用实施工程师（武汉）")
    page.locator('#newtask input[name="target_url"]').fill(_PREPARATION_SOURCE)
    page.locator('#newtask button').click()
    expect(page.locator('#toast')).to_contain_text("任务已保留在列表中")
    expect(page.locator('#newtask input[name="role"]')).to_have_value("")
    assert page.get_by_role("button", name="查看已有任务", exact=True).count() == 0
    assert "已添加" not in page.locator('#toast').inner_text()
    assert len(submissions) == 1
    assert observed["external"] == [] and observed["errors"] == []


# Each request is held by the test router until the edited draft is observable.
# No recruiting website, account, applicant material or service write is used.
_TASK_DRAFT_A = {"company": "Synthetic Company A", "role": "Synthetic Role A",
                 "location": "", "campaign": "", "employment_type": "",
                 "target_url": "https://jobs.example.test/original-a"}
_TASK_DRAFT_B = {"company": "Synthetic Company B", "role": "Synthetic Role B",
                 "location": "", "campaign": "", "employment_type": "",
                 "target_url": "https://jobs.example.test/unsent-b"}
_TASK_DRAFT_CANDIDATE = "synthetic-candidate-a"


@pytest.fixture
def pending_task_add_ui(preparation_ui):
    page, observed = preparation_ui
    pending = {"routes": [], "submissions": []}

    def hold_submission(route):
        pending["submissions"].append((route.request.method, route.request.post_data_json))
        pending["routes"].append(route)
        page.evaluate("count => window.__taskAddRequests = count", len(pending["submissions"]))

    page.route("https://preparation.test/ui/api/tasks", hold_submission)
    yield page, observed, pending


def _fill_task_draft(page, values):
    for key, value in values.items():
        page.locator(f'#newtask input[name="{key}"]').fill(value)


def _release_task_discovery(page, pending):
    pending["routes"].pop(0).fulfill(status=200, content_type="application/json", body=json.dumps({
        "discovery": {"status": "AMBIGUOUS", "candidates": [{
            "candidate_id": _TASK_DRAFT_CANDIDATE, "job_id": "synthetic-job-a",
            "title": "Synthetic Role A", "location": "Synthetic Location A"}]}}))
    expect(page.get_by_role("button", name="选择此岗位", exact=True)).to_be_enabled()


def _assert_task_draft_requests(page, observed, pending, candidate):
    expected = [("POST", _TASK_DRAFT_A)]
    if candidate:
        expected.append(("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_DRAFT_CANDIDATE}))
    # Refreshing ordinary read-only state must never submit the unsent draft.
    page.evaluate("state()")
    assert pending["submissions"] == expected
    assert all(method == "GET" for method, _, _ in observed["requests"])
    assert observed["external"] == [] and observed["errors"] == []
    assert page.evaluate("window.__copied") == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.mark.parametrize("edit_phase", ["direct_pending", "discovery_pending", "before_selection", "selection_pending"])
@pytest.mark.parametrize("changed_field", ["company", "role", "target_url", "edit_reverted"])
def test_task_workspace_delayed_add_preserves_newer_draft(pending_task_add_ui, edit_phase, changed_field):
    page, observed, pending = pending_task_add_ui
    expected_draft = dict(_TASK_DRAFT_A)

    def edit_draft():
        key = "role" if changed_field == "edit_reverted" else changed_field
        _fill_task_draft(page, {key: _TASK_DRAFT_B[key]})
        if changed_field == "edit_reverted":
            # Matching values do not make a later edit the submitted form version.
            _fill_task_draft(page, {key: _TASK_DRAFT_A[key]})
        else:
            expected_draft[key] = _TASK_DRAFT_B[key]

    _fill_task_draft(page, _TASK_DRAFT_A)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    candidate = edit_phase != "direct_pending"
    if edit_phase in {"direct_pending", "discovery_pending"}:
        edit_draft()
    if candidate:
        _release_task_discovery(page, pending)
        if edit_phase == "before_selection":
            edit_draft()
        page.get_by_role("button", name="选择此岗位", exact=True).click()
        page.wait_for_function("window.__taskAddRequests === 2")
        if edit_phase == "selection_pending":
            edit_draft()
    pending["routes"].pop(0).fulfill(status=200, content_type="application/json", body=json.dumps({
        "task_id": "prep-a", "revision": 7, "task_binding": "requested",
        "discovery": {"status": "VERIFIED"}}))
    # Wait for this success to be processed. Checking B before this would pass
    # even if the delayed response subsequently erased it.
    expect(page.locator("#toast")).to_contain_text("任务已保留在列表中")
    expect(page.locator("#newtask button")).to_be_enabled()
    expect(page.locator("#candidates button[data-candidate]")).to_have_count(0)
    for key, value in expected_draft.items():
        expect(page.locator(f'#newtask input[name="{key}"]')).to_have_value(value)
    _assert_task_draft_requests(page, observed, pending, candidate)


@pytest.mark.parametrize("candidate", [False, True], ids=["direct", "candidate"])
@pytest.mark.parametrize("submit_method", ["click", "enter"])
def test_task_workspace_delayed_add_clears_only_unchanged_submitted_draft(pending_task_add_ui, candidate, submit_method):
    page, observed, pending = pending_task_add_ui
    _fill_task_draft(page, _TASK_DRAFT_A)
    if submit_method == "enter":
        # Keep the edited field focused through discovery; selecting a candidate
        # later blurs it but must not invent a new draft revision.
        page.locator('#newtask input[name="target_url"]').press("Enter")
    else:
        page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    if candidate:
        _release_task_discovery(page, pending)
        page.get_by_role("button", name="选择此岗位", exact=True).click()
        page.wait_for_function("window.__taskAddRequests === 2")
    pending["routes"].pop(0).fulfill(status=200, content_type="application/json", body=json.dumps({
        "task_id": "prep-a", "revision": 7, "task_binding": "requested",
        "discovery": {"status": "VERIFIED"}}))
    expect(page.locator("#toast")).to_contain_text("任务已保留在列表中")
    for key in _TASK_DRAFT_A:
        expect(page.locator(f'#newtask input[name="{key}"]')).to_have_value("")
    _assert_task_draft_requests(page, observed, pending, candidate)


@pytest.mark.parametrize("candidate", [False, True], ids=["direct", "candidate"])
@pytest.mark.parametrize("outcome", ["failure", "aborted", "duplicate", "reconciliation", "expired"])
def test_task_workspace_delayed_add_non_success_preserves_draft_without_replay(pending_task_add_ui, candidate, outcome):
    page, observed, pending = pending_task_add_ui
    _fill_task_draft(page, _TASK_DRAFT_A)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    if candidate:
        _release_task_discovery(page, pending)
        page.get_by_role("button", name="选择此岗位", exact=True).click()
        page.wait_for_function("window.__taskAddRequests === 2")
    _fill_task_draft(page, _TASK_DRAFT_B)
    response = pending["routes"].pop(0)
    if outcome == "aborted":
        response.abort("failed")
    else:
        status, payload = {
            "failure": (503, {"error": "synthetic_unavailable"}),
            "duplicate": (200, {"task_id": "prep-a", "revision": 7,
                                "task_binding": "existing_different", "discovery": {"status": "VERIFIED"}}),
            "reconciliation": (409, {"error": "profile_reconciliation_required", "submit_capability": False}),
            "expired": (401, {"error": "ui_session_required"}),
        }[outcome]
        response.fulfill(status=status, content_type="application/json", body=json.dumps(payload))
    if outcome == "expired":
        expect(page.locator("#session-expired")).to_be_visible()
        expect(page.locator("#newtask button")).to_be_disabled()
        for key in _TASK_DRAFT_B:
            assert page.locator(f'#newtask input[name="{key}"]').evaluate("node => node.readOnly")
    elif outcome == "duplicate":
        expect(page.get_by_role("button", name="查看已有任务", exact=True)).to_be_visible()
        expect(page.locator("#candidates")).to_contain_text("本次没有替换或启动任务")
    elif outcome == "reconciliation":
        expect(page.get_by_role("button", name="打开资料并核对", exact=True)).to_be_visible()
        expect(page.locator("#toast")).to_contain_text("不会自动重试")
    else:
        expect(page.locator("#toast")).to_contain_text("候选已变化" if candidate else "暂时无法安全查找岗位")
        expect(page.locator("#newtask button")).to_be_enabled()
        if candidate:
            expect(page.get_by_role("button", name="选择此岗位", exact=True)).to_be_enabled()
    for key, value in _TASK_DRAFT_B.items():
        expect(page.locator(f'#newtask input[name="{key}"]')).to_have_value(value)
    _assert_task_draft_requests(page, observed, pending, candidate)


def test_task_workspace_delayed_candidate_recheck_keeps_its_own_request_snapshot(candidate_panel_ui):
    page, observed, pending = candidate_panel_ui
    _fill_task_draft(page, _TASK_DRAFT_A)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    _release_task_discovery(page, pending)
    page.get_by_role("button", name="选择此岗位", exact=True).click()
    page.wait_for_function("window.__taskAddRequests === 2")
    candidate_response = pending["routes"].pop(0)
    _fill_task_draft(page, _TASK_DRAFT_B)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 3")
    # B owns the panel after its explicit lookup. A's later response still
    # belongs to A, so it cannot replace B's panel or its submitted snapshot.
    _fulfill_task_panel(pending["routes"].pop(0),
        _task_panel_discovery(_TASK_PANEL_B_CANDIDATE, "Synthetic Role B"))
    expect(page.locator("#candidates")).to_contain_text("Synthetic Role B")
    _fill_task_draft(page, _TASK_PANEL_C)
    next_candidate = "synthetic-candidate-a-rechecked"
    candidate_response.fulfill(status=200, content_type="application/json", body=json.dumps({
        "discovery": {"status": "AMBIGUOUS", "candidates": [{
            "candidate_id": next_candidate, "job_id": "synthetic-job-a-rechecked",
            "title": "Synthetic Role A Rechecked", "location": "Synthetic Location A"}]}}))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    expect(page.locator("#candidates")).to_contain_text("Synthetic Role B")
    expect(page.locator("#candidates")).not_to_contain_text("Synthetic Role A Rechecked")
    # Only this fresh, explicit click may issue the next request.
    assert len(pending["submissions"]) == 3
    page.get_by_role("button", name="选择此岗位", exact=True).click()
    page.wait_for_function("window.__taskAddRequests === 4")
    expected_submissions = [
        ("POST", _TASK_DRAFT_A),
        ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_DRAFT_CANDIDATE}),
        ("POST", _TASK_DRAFT_B),
        ("POST", {**_TASK_DRAFT_B, "selected_candidate_id": _TASK_PANEL_B_CANDIDATE}),
    ]
    assert pending["submissions"] == expected_submissions
    pending["routes"].pop(0).fulfill(status=200, content_type="application/json", body=json.dumps({
        "task_id": "prep-b", "revision": 9, "task_binding": "requested",
        "discovery": {"status": "VERIFIED"}}))
    expect(page.locator("#toast")).to_contain_text("任务已保留在列表中")
    for key, value in _TASK_PANEL_C.items():
        expect(page.locator(f'#newtask input[name="{key}"]')).to_have_value(value)
    page.evaluate("state()")
    assert pending["submissions"] == expected_submissions
    assert all(method == "GET" for method, _, _ in observed["requests"])
    assert observed["external"] == [] and observed["errors"] == []
    assert page.evaluate("window.__copied") == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.fixture
def candidate_panel_ui(pending_task_add_ui):
    """Observe real handler completion, not merely response/body arrival."""
    page, observed, pending = pending_task_add_ui
    page.add_init_script("""(() => {
      window.__candidateHandlersDone = [];
      const original = EventTarget.prototype.addEventListener;
      EventTarget.prototype.addEventListener = function(type, listener, options) {
        if (this.id === 'candidates' && type === 'click' && typeof listener === 'function') {
          const actual = listener;
          listener = function(event) {
            const candidate = event.target.closest('button[data-candidate]')?.dataset.candidate;
            const result = actual.call(this, event);
            // Synchronous navigation listeners are untouched. The real async
            // handler owns this promise, including any awaited state refresh.
            if (candidate && result && typeof result.then === 'function') {
              result.then(
                () => window.__candidateHandlersDone.push({candidate, status:'fulfilled'}),
                () => window.__candidateHandlersDone.push({candidate, status:'rejected'})
              );
            }
            return result;
          };
        }
        return original.call(this, type, listener, options);
      };
      window.addEventListener('load', () => {
        EventTarget.prototype.addEventListener = original;
      }, {once:true});
    })();""")
    page.reload()
    expect(page.locator('[data-task-preparation]')).to_have_count(2)
    yield page, observed, pending


_TASK_PANEL_B_CANDIDATE = "synthetic-candidate-b"
_TASK_PANEL_C = {**_TASK_DRAFT_B, "role": "Synthetic Unsent Role C",
                 "target_url": "https://jobs.example.test/unsent-c"}


def _task_panel_discovery(candidate_id, title):
    return {"discovery": {"status": "AMBIGUOUS", "candidates": [{
        "candidate_id": candidate_id, "job_id": candidate_id + "-job",
        "title": title, "location": title + " Location"}]}}


def _fulfill_task_panel(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _wait_candidate_handler(page, candidate_id):
    page.wait_for_function("candidate => window.__candidateHandlersDone.some(item => item.candidate === candidate)",
                           arg=candidate_id)
    assert page.evaluate("window.__candidateHandlersDone.every(item => item.status === 'fulfilled')"), \
        "the completion observer must not hide a rejected UI callback"


def _start_overlapping_task_queries(page, pending, *, finish_newer=True, newer_request=_TASK_DRAFT_B):
    _fill_task_draft(page, _TASK_DRAFT_A)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    _release_task_discovery(page, pending)
    page.get_by_role("button", name="选择此岗位", exact=True).click()
    page.wait_for_function("window.__taskAddRequests === 2")
    older = pending["routes"].pop(0)
    if newer_request is not None:
        _fill_task_draft(page, newer_request)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 3")
    newer = pending["routes"].pop(0)
    if finish_newer:
        _fulfill_task_panel(newer, _task_panel_discovery(_TASK_PANEL_B_CANDIDATE, "Synthetic Role B"))
        expect(page.locator("#candidates")).to_contain_text("Synthetic Role B")
        expect(page.locator("#newtask button")).to_be_enabled()
    return older, newer


def _task_panel_initial_submissions():
    return [("POST", _TASK_DRAFT_A),
            ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_DRAFT_CANDIDATE}),
            ("POST", _TASK_DRAFT_B)]


def _assert_task_panel_contracts(page, observed, pending, expected, draft):
    page.evaluate("state()")
    assert pending["submissions"] == expected, "responses and state reads must not replay a request"
    assert pending["routes"] == []
    for key, value in draft.items():
        expect(page.locator(f'#newtask input[name="{key}"]')).to_have_value(value)
    assert all(method == "GET" for method, _, _ in observed["requests"])
    assert observed["external"] == [] and observed["errors"] == []
    assert page.evaluate("window.__copied") == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.mark.parametrize("outcome", ["recheck", "unavailable", "created", "duplicate", "reconciliation", "failure", "aborted"])
def test_task_workspace_latest_query_owns_candidate_panel_after_older_reply(candidate_panel_ui, outcome):
    page, observed, pending = candidate_panel_ui
    older, _ = _start_overlapping_task_queries(page, pending)
    _fill_task_draft(page, _TASK_PANEL_C)
    panel_before = page.locator("#candidates").inner_html()
    if outcome == "created":
        # The polling interval already holds the original function object.
        # Count the handler's explicit state() call, not incidental polling.
        page.evaluate("""() => {
          const original = state;
          window.__candidateStateRefreshes = 0;
          state = function(...args) {
            window.__candidateStateRefreshes++;
            return original.apply(this, args);
          };
        }""")
    if outcome == "aborted":
        older.abort("failed")
    else:
        status, payload = {
            "recheck": (200, _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked")),
            "unavailable": (200, {"discovery": {"status": "UNAVAILABLE", "candidates": []}}),
            "created": (200, {"task_id": "prep-a", "revision": 7, "task_binding": "requested",
                              "discovery": {"status": "VERIFIED"}}),
            "duplicate": (200, {"task_id": "prep-a", "revision": 7, "task_binding": "existing_different",
                                "discovery": {"status": "VERIFIED"}}),
            "reconciliation": (409, {"error": "profile_reconciliation_required", "submit_capability": False}),
            "failure": (503, {"error": "synthetic_unavailable"}),
        }[outcome]
        _fulfill_task_panel(older, payload, status)
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    if outcome == "created":
        expect(page.locator("#toast")).to_contain_text("任务已保留在列表中")
        assert page.evaluate("window.__candidateStateRefreshes") == 1
    assert page.locator("#candidates").inner_html() == panel_before, "an older response took B's candidate panel"
    assert pending["submissions"] == _task_panel_initial_submissions()
    # B stays actionable, but a fresh explicit click is still required. The
    # visible C draft must never leak into B's already submitted request.
    page.locator(f'[data-candidate="{_TASK_PANEL_B_CANDIDATE}"]').click()
    page.wait_for_function("window.__taskAddRequests === 4")
    expected = _task_panel_initial_submissions() + [
        ("POST", {**_TASK_DRAFT_B, "selected_candidate_id": _TASK_PANEL_B_CANDIDATE})]
    assert pending["submissions"] == expected
    _fulfill_task_panel(pending["routes"].pop(0), {"task_id": "prep-b", "revision": 9,
        "task_binding": "requested", "discovery": {"status": "VERIFIED"}})
    _wait_candidate_handler(page, _TASK_PANEL_B_CANDIDATE)
    expect(page.locator("#candidates button[data-candidate]")).to_have_count(0)
    _assert_task_panel_contracts(page, observed, pending, expected, _TASK_PANEL_C)


@pytest.mark.parametrize("newer_phase", ["lookup_pending", "selection_pending"])
def test_task_workspace_latest_query_owns_candidate_panel_while_newer_request_is_pending(candidate_panel_ui, newer_phase):
    page, observed, pending = candidate_panel_ui
    older, newer = _start_overlapping_task_queries(page, pending, finish_newer=newer_phase == "selection_pending")
    expected = _task_panel_initial_submissions()
    if newer_phase == "selection_pending":
        page.locator(f'[data-candidate="{_TASK_PANEL_B_CANDIDATE}"]').click()
        page.wait_for_function("window.__taskAddRequests === 4")
        newer = pending["routes"].pop(0)
        expected.append(("POST", {**_TASK_DRAFT_B, "selected_candidate_id": _TASK_PANEL_B_CANDIDATE}))
    _fill_task_draft(page, _TASK_PANEL_C)
    panel_before = page.locator("#candidates").inner_html()
    _fulfill_task_panel(older, _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked"))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    assert page.locator("#candidates").inner_html() == panel_before, "an older response replaced the pending query's panel"
    expect(page.locator("#candidates button[data-candidate]:enabled")).to_have_count(0)
    if newer_phase == "lookup_pending":
        expect(page.locator("#newtask button")).to_be_disabled()
    assert pending["submissions"] == expected
    next_candidate = "synthetic-b-rechecked"
    _fulfill_task_panel(newer, _task_panel_discovery(next_candidate, "Synthetic Role B Rechecked"))
    expect(page.locator(f'[data-candidate="{next_candidate}"]')).to_be_enabled()
    expect(page.locator("#newtask button")).to_be_enabled()
    if newer_phase == "selection_pending":
        _wait_candidate_handler(page, _TASK_PANEL_B_CANDIDATE)
    page.locator(f'[data-candidate="{next_candidate}"]').click()
    page.wait_for_function("count => window.__taskAddRequests === count", arg=len(expected) + 1)
    expected.append(("POST", {**_TASK_DRAFT_B, "selected_candidate_id": next_candidate}))
    _fulfill_task_panel(pending["routes"].pop(0), {"task_id": "prep-b", "revision": 9,
        "task_binding": "requested", "discovery": {"status": "VERIFIED"}})
    _wait_candidate_handler(page, next_candidate)
    _assert_task_panel_contracts(page, observed, pending, expected, _TASK_PANEL_C)


@pytest.mark.parametrize("older_outcome", ["recheck", "created"])
def test_task_workspace_repeated_identical_query_has_its_own_panel_owner(candidate_panel_ui, older_outcome):
    page, observed, pending = candidate_panel_ui
    # Submit the unchanged form again: equal payloads and draft revisions do
    # not make two explicit requests the same owner.
    older, _ = _start_overlapping_task_queries(page, pending, newer_request=None)
    panel_before = page.locator("#candidates").inner_html()
    payload = _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked")
    if older_outcome == "created":
        payload = {"task_id": "prep-a", "revision": 7, "task_binding": "requested",
                   "discovery": {"status": "VERIFIED"}}
    _fulfill_task_panel(older, payload)
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    assert page.locator("#candidates").inner_html() == panel_before
    expected = [("POST", _TASK_DRAFT_A),
        ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_DRAFT_CANDIDATE}),
        ("POST", _TASK_DRAFT_A)]
    _assert_task_panel_contracts(page, observed, pending, expected, _TASK_DRAFT_A)
    page.locator(f'[data-candidate="{_TASK_PANEL_B_CANDIDATE}"]').click()
    page.wait_for_function("window.__taskAddRequests === 4")
    expected.append(("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_PANEL_B_CANDIDATE}))
    _fulfill_task_panel(pending["routes"].pop(0), {"task_id": "prep-a", "revision": 7,
        "task_binding": "requested", "discovery": {"status": "VERIFIED"}})
    _wait_candidate_handler(page, _TASK_PANEL_B_CANDIDATE)
    _assert_task_panel_contracts(page, observed, pending, expected, {key: "" for key in _TASK_DRAFT_A})


def test_task_workspace_older_candidate_cannot_reopen_panel_after_latest_task_succeeds(candidate_panel_ui):
    page, observed, pending = candidate_panel_ui
    older, newer = _start_overlapping_task_queries(page, pending, finish_newer=False)
    _fill_task_draft(page, _TASK_PANEL_C)
    _fulfill_task_panel(newer, {"task_id": "prep-b", "revision": 9,
        "task_binding": "requested", "discovery": {"status": "VERIFIED"}})
    expect(page.locator("#newtask button")).to_be_enabled()
    expect(page.locator("#toast")).to_contain_text("任务已保留在列表中")
    expect(page.locator("#candidates")).to_be_empty()
    _fulfill_task_panel(older, _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked"))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    expect(page.locator("#candidates")).to_be_empty()
    _assert_task_panel_contracts(page, observed, pending, _task_panel_initial_submissions(), _TASK_PANEL_C)


def test_task_workspace_latest_explicit_candidate_choice_owns_panel(candidate_panel_ui):
    page, observed, pending = candidate_panel_ui
    _fill_task_draft(page, _TASK_DRAFT_A)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    alternate = "synthetic-candidate-a-alternate"
    discovery = _task_panel_discovery(_TASK_DRAFT_CANDIDATE, "Synthetic Role A")
    discovery["discovery"]["candidates"] += _task_panel_discovery(alternate, "Synthetic Alternate A")["discovery"]["candidates"]
    _fulfill_task_panel(pending["routes"].pop(0), discovery)
    page.locator(f'[data-candidate="{_TASK_DRAFT_CANDIDATE}"]').click()
    page.wait_for_function("window.__taskAddRequests === 2")
    older = pending["routes"].pop(0)
    # This second choice is an existing enabled control, not a forced submit.
    page.locator(f'[data-candidate="{alternate}"]').click()
    page.wait_for_function("window.__taskAddRequests === 3")
    _fill_task_draft(page, _TASK_DRAFT_B)
    latest = "synthetic-alternate-rechecked"
    _fulfill_task_panel(pending["routes"].pop(0), _task_panel_discovery(latest, "Synthetic Alternate Rechecked"))
    _wait_candidate_handler(page, alternate)
    panel_before = page.locator("#candidates").inner_html()
    _fulfill_task_panel(older, _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked"))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    assert page.locator("#candidates").inner_html() == panel_before, "an older choice replaced the latest explicit candidate choice"
    expected = [("POST", _TASK_DRAFT_A),
        ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_DRAFT_CANDIDATE}),
        ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": alternate})]
    assert pending["submissions"] == expected
    page.locator(f'[data-candidate="{latest}"]').click()
    page.wait_for_function("window.__taskAddRequests === 4")
    expected.append(("POST", {**_TASK_DRAFT_A, "selected_candidate_id": latest}))
    _fulfill_task_panel(pending["routes"].pop(0), {"task_id": "prep-a", "revision": 7,
        "task_binding": "requested", "discovery": {"status": "VERIFIED"}})
    _wait_candidate_handler(page, latest)
    _assert_task_panel_contracts(page, observed, pending, expected, _TASK_DRAFT_B)


def test_task_workspace_unsent_edit_does_not_take_candidate_panel_ownership(candidate_panel_ui):
    page, observed, pending = candidate_panel_ui
    _fill_task_draft(page, _TASK_DRAFT_A)
    page.locator("#newtask button").click()
    page.wait_for_function("window.__taskAddRequests === 1")
    _release_task_discovery(page, pending)
    page.locator(f'[data-candidate="{_TASK_DRAFT_CANDIDATE}"]').click()
    page.wait_for_function("window.__taskAddRequests === 2")
    _fill_task_draft(page, _TASK_DRAFT_B)
    rechecked = "synthetic-a-rechecked"
    _fulfill_task_panel(pending["routes"].pop(0), _task_panel_discovery(rechecked, "Synthetic Role A Rechecked"))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    expect(page.locator(f'[data-candidate="{rechecked}"]')).to_be_enabled()
    assert len(pending["submissions"]) == 2
    page.locator(f'[data-candidate="{rechecked}"]').click()
    page.wait_for_function("window.__taskAddRequests === 3")
    expected = [("POST", _TASK_DRAFT_A),
        ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": _TASK_DRAFT_CANDIDATE}),
        ("POST", {**_TASK_DRAFT_A, "selected_candidate_id": rechecked})]
    _fulfill_task_panel(pending["routes"].pop(0), {"task_id": "prep-a", "revision": 7,
        "task_binding": "requested", "discovery": {"status": "VERIFIED"}})
    _wait_candidate_handler(page, rechecked)
    _assert_task_panel_contracts(page, observed, pending, expected, _TASK_DRAFT_B)


@pytest.mark.parametrize("action", ["navigate", "cancel_existing_task"])
def test_task_workspace_latest_candidate_panel_survives_existing_task_controls(candidate_panel_ui, action):
    page, observed, pending = candidate_panel_ui
    older, _ = _start_overlapping_task_queries(page, pending)
    commands = []
    def cancel(route):
        commands.append(route.request.post_data_json)
        observed["tasks"][1].update(stage="CANCELLED", revision=10)
        _fulfill_task_panel(route, {"ok": True})
    page.route("https://preparation.test/ui/api/command", cancel)
    page.locator('[data-task-select="prep-b"]').click()
    expect(page.locator('[data-task-card="prep-b"]')).to_have_attribute("data-current-task", "true")
    if action == "cancel_existing_task":
        # This cancels only this existing task. There is no query-cancel UI.
        page.locator('[data-action="CANCEL"][data-task="prep-b"]').click()
        expect(page.locator('[data-task-card="prep-b"] .stage')).to_have_text("已取消")
        assert len(commands) == 1
        command = commands[0]
        assert command == {"task_id": "prep-b", "action": "CANCEL", "expected_revision": 9,
                           "command_id": command["command_id"]}
        assert command["command_id"].startswith("ui-")
    else:
        assert commands == []
    commands_before = list(commands)
    panel_before = page.locator("#candidates").inner_html()
    _fulfill_task_panel(older, _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked"))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    assert page.locator("#candidates").inner_html() == panel_before
    expect(page.locator('[data-task-card="prep-b"]')).to_have_attribute("data-current-task", "true")
    _assert_task_panel_contracts(page, observed, pending, _task_panel_initial_submissions(), _TASK_DRAFT_B)
    assert commands == commands_before, "stale candidate replies must not replay task commands"


@pytest.mark.parametrize("expiry_source", ["older_response", "state_before_older_response"])
def test_task_workspace_candidate_panel_ownership_preserves_global_session_expiry(candidate_panel_ui, expiry_source):
    page, observed, pending = candidate_panel_ui
    older, _ = _start_overlapping_task_queries(page, pending)
    _fill_task_draft(page, _TASK_PANEL_C)
    panel_text = page.locator("#candidates").inner_text()
    observed["expired"] = True
    if expiry_source == "older_response":
        _fulfill_task_panel(older, {"error": "ui_session_required"}, 401)
    else:
        page.evaluate("state()")
        expect(page.locator("#session-expired")).to_be_visible()
        _fulfill_task_panel(older, _task_panel_discovery("synthetic-a-rechecked", "Synthetic Role A Rechecked"))
    _wait_candidate_handler(page, _TASK_DRAFT_CANDIDATE)
    expect(page.locator("#session-expired")).to_be_visible()
    assert page.locator("#candidates").inner_text() == panel_text
    expect(page.locator("#candidates button[data-candidate]:enabled")).to_have_count(0)
    expect(page.locator("#newtask button")).to_be_disabled()
    for key in _TASK_PANEL_C:
        assert page.locator(f'#newtask input[name="{key}"]').evaluate("node => node.readOnly && !node.disabled")
    _assert_task_panel_contracts(page, observed, pending, _task_panel_initial_submissions(), _TASK_PANEL_C)


# Synthetic private-editor payloads never leave the loopback/routed test origin.
def _editor_payload():
    from executor.autonomy.task_preparation import CHECKLIST_FIELDS
    return {"schema_version": 1, "settings_version": "a" * 64, "profile_version": "b" * 64,
            "mode": "canonical", "future_tasks_only": True, "submit_capability": False,
            "admission_status": "ready", "resume": {"status": "missing", "kind": None},
            "fields": [{"key": key, "label": "UNTRUSTED_LABEL", "type":
                        "text_list" if key == "preferences.preferred_cities" else
                        "text_or_number" if key == "language.cet6.score" else "text",
                        "status": "missing", "editable": True, "value": None}
                       for key, _ in CHECKLIST_FIELDS]}


@pytest.fixture
def profile_editor_ui():
    import copy
    observed = {"payload": _editor_payload(), "status": 200, "requests": [], "pending": [],
                "hold": False, "expired": False, "save_status": 200, "save_payload": None,
                "reconcile_status": 200, "errors": [], "external": [], "profile_selected": True,
                "reconciliation_required": False, "readiness_checks": {}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        page.on("pageerror", lambda error: observed["errors"].append(str(error)))
        def route_request(route):
            request = route.request
            if not request.url.startswith("http://profile-editor.test/"):
                observed["external"].append(request.url)
                route.abort()
                return
            path = request.url.split("profile-editor.test", 1)[1]
            observed["requests"].append((request.method, path, request.post_data_buffer))
            if path == "/":
                route.fulfill(content_type="text/html; charset=utf-8", body=DASHBOARD_HTML)
                return
            if observed["expired"]:
                route.fulfill(status=401, content_type="application/json", body='{}')
                return
            if path == "/ui/api/profile-setup":
                payload = {"settings_version": "a" * 64, "profile_selected": observed["profile_selected"],
                           "submit_capability": False, "reconciliation_required": observed["reconciliation_required"]}
            elif path == "/ui/api/profile-editor":
                if request.method == "POST":
                    payload = observed["save_payload"] or {**copy.deepcopy(observed["payload"]), "save_status": "saved"}
                    route.fulfill(status=observed["save_status"], content_type="application/json", body=json.dumps(payload))
                    return
                if observed["hold"]:
                    observed["pending"].append((route, copy.deepcopy(observed["payload"])))
                    page.evaluate("count => window.__editorHeldReads=count", len(observed["pending"]))
                    return
                route.fulfill(status=observed["status"], content_type="application/json", body=json.dumps(observed["payload"]))
                return
            elif path == "/ui/api/profile-editor/reconcile":
                payload = {**observed["payload"], "reconciliation_status": "reconciled"}
                route.fulfill(status=observed["reconcile_status"], content_type="application/json", body=json.dumps(payload))
                return
            elif path == "/ui/api/readiness":
                payload = {"ready_for_live_e2e": False, "submit_capability": False, "checks": observed["readiness_checks"]}
            else:
                payload = {"tasks": []}
            route.fulfill(content_type="application/json", body=json.dumps(payload))
        page.route("**/*", route_request)
        page.goto("http://profile-editor.test/")
        try:
            yield page, observed
        finally:
            assert observed["errors"] == []
            assert observed["external"] == []
            browser.close()


def _open_editor(page):
    page.locator("#profile-setup").click()
    expect(page.locator("#profile-editor-open")).to_be_enabled()
    page.locator("#profile-editor-open").click()
    expect(page.locator("#profile-editor-dialog")).to_be_visible()


@pytest.mark.parametrize("pending", [None, 0, "false"])
def test_task_workspace_profile_setup_unknown_reconciliation_status_is_not_ready(profile_editor_ui, pending):
    page, observed = profile_editor_ui
    observed["reconciliation_required"] = pending
    page.locator("#profile-setup").click()
    expect(page.locator("#profile-status")).to_contain_text("无法读取设置")
    expect(page.locator("#profile-editor-open")).to_be_disabled()
    expect(page.locator("#profile-file")).to_be_disabled()
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_pending_profile_setup_late_reply_never_reopens_editor(profile_editor_ui, interruption):
    page, observed = profile_editor_ui
    pending = []
    page.route("**/ui/api/profile-setup", lambda route: pending.append(route))
    page.locator("#profile-setup").click()
    expect(page.locator("#profile-dialog")).to_be_visible()
    if interruption == "close": page.locator("#profile-close").click()
    elif interruption == "escape": page.locator("#profile-dialog").press("Escape")
    else:
        observed["expired"] = True;page.evaluate("readiness()")
        expect(page.locator("#session-expired")).to_be_visible()
    with page.expect_response("**/ui/api/profile-setup"):
        pending[0].fulfill(content_type="application/json", body=json.dumps({
            "settings_version": "a" * 64, "profile_selected": True,
            "submit_capability": False, "reconciliation_required": True}))
    expect(page.locator("#profile-dialog")).not_to_be_visible()
    expect(page.locator("#profile-editor-dialog")).not_to_be_visible()
    assert page.locator("#profile-status").inner_text() == ""
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("status,body", [
    (409, {"error": "state_conflict"}),
    (500, {"error": "profile_reconciliation_required", "submit_capability": False}),
    (409, {"error": "profile_reconciliation_required", "submit_capability": False,
           "message": "SYNTHETIC_PRIVATE_ERROR"}),
])
def test_task_workspace_unrelated_task_conflict_does_not_claim_profile_cause(profile_editor_ui, status, body):
    page, observed = profile_editor_ui
    page.route("**/ui/api/tasks", lambda route: route.fulfill(
        status=status, content_type="application/json", body=json.dumps(body)))
    page.locator('#newtask [name="company"]').fill("Synthetic")
    page.locator('#newtask [name="role"]').fill("Retained role")
    page.locator("#newtask button").click()
    expect(page.locator("#toast")).to_contain_text("请核对公司、岗位和官方链接")
    expect(page.locator("button[data-profile-reconciliation]")).not_to_be_visible()
    assert "SYNTHETIC_PRIVATE_ERROR" not in page.locator("body").inner_text()


@pytest.mark.parametrize("candidate", [False, True])
def test_task_workspace_profile_blocked_task_guidance_only_opens_existing_editor(profile_editor_ui, candidate):
    page, observed = profile_editor_ui
    observed["reconciliation_required"] = True
    observed["payload"]["admission_status"] = "reconciliation_required"
    posts = []
    def blocked(route):
        posts.append(route.request.post_data_buffer)
        route.fulfill(status=409, content_type="application/json",
                      body='{"error":"profile_reconciliation_required","submit_capability":false}')
    page.route("**/ui/api/tasks", blocked)
    page.locator('#newtask [name="company"]').fill("Synthetic")
    page.locator('#newtask [name="role"]').fill("Retained role")
    if candidate:
        page.evaluate("""showDiscovery({discovery:{status:'AMBIGUOUS',candidates:[{
          candidate_id:'synthetic-choice',title:'Synthetic role',location:'Synthetic city'}]}},
          {company:'Synthetic',role:'Retained role'})""")
        page.locator('button[data-candidate="synthetic-choice"]').click()
    else:
        page.locator("#newtask button").click()
    expect(page.locator("#profile-reconciliation-note")).to_be_visible()
    expect(page.locator('#newtask [name="role"]')).to_have_value("Retained role")
    page.locator("button[data-profile-reconciliation]").click()
    expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
    assert len(posts) == 1 and _editor_posts(observed) == []
    page.locator("#profile-editor-close").click()
    assert _editor_posts(observed) == [] and len(posts) == 1
    expect(page.locator("#profile-editor-dialog")).not_to_be_visible()


def _editor_posts(observed):
    return [(path, body) for method, path, body in observed["requests"] if method == "POST"]


def _missing_resume_payload():
    data = _editor_payload()
    data["admission_status"] = "resume_replacement_required"
    data["resume"] = {"status": "missing_managed", "kind": "resume_pdf"}
    for field in data["fields"]:
        field["editable"] = False
    data["fields"][0].update(status="supported", value="SYNTHETIC_RETAINED_NAME")
    return data


def _resume_repair_payload(kind):
    data = _missing_resume_payload()
    if kind == "damaged":
        data["resume"].update(status="damaged_managed", version="e" * 64)
    return data


@pytest.mark.parametrize("repair_kind", ["missing", "damaged"])
def test_task_workspace_missing_resume_requires_explicit_file_and_preserves_readonly_facts(profile_editor_ui, repair_kind):
    import os
    from pathlib import Path
    page, observed = profile_editor_ui
    observed["payload"] = _resume_repair_payload(repair_kind)
    _open_editor(page)
    expect(page.locator("#profile-editor-status")).to_contain_text("简历文件缺失" if repair_kind == "missing" else "简历内容已变化")
    expect(page.locator("#profile-editor-resume")).to_be_enabled()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert not page.locator("#profile-editor-preserved").get_attribute("open")
    assert page.locator("#profile-editor-resume").is_visible()
    assert _editor_posts(observed) == []
    screenshot_dir = os.environ.get("JAE_UI_SCREENSHOT_DIR")
    if screenshot_dir:
        destination = Path(screenshot_dir);destination.mkdir(parents=True, exist_ok=True)
        page.locator("#profile-editor-dialog").screenshot(path=str(destination / ("profile-" + repair_kind + "-resume.png")), animations="disabled")
    page.locator("#profile-editor-preserved-summary").click()
    expect(page.locator("#profile-editor-field-0")).to_have_value("SYNTHETIC_RETAINED_NAME")
    assert all(not node.is_enabled() for node in page.locator("#profile-editor-fields input, #profile-editor-fields textarea").all())
    page.locator("#profile-editor-preserved-summary").click()
    page.locator("#profile-editor-resume").set_input_files({"name": "chosen-new.pdf", "mimeType": "application/pdf", "buffer": b"SYNTHETIC_NEW_RESUME"})
    expect(page.locator("#profile-editor-save")).to_be_enabled()
    assert _editor_posts(observed) == []
    after = _editor_payload()
    after.update(settings_version="c" * 64, profile_version="d" * 64)
    after["resume"] = {"status": "recorded_locally", "kind": "resume_pdf"}
    after["fields"][0].update(status="supported", value="SYNTHETIC_RETAINED_NAME")
    observed["payload"] = after
    observed["save_payload"] = {**after, "save_status": "saved"}
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("已保存在本机并重新读取确认")
    posts = _editor_posts(observed)
    assert len(posts) == 1 and posts[0][0] == "/ui/api/profile-editor"
    action = b'"resume_action":"replace_missing"' if repair_kind == "missing" else b'"resume_action":"replace_damaged"'
    assert action in posts[0][1] and b'"edits":{}' in posts[0][1]
    if repair_kind == "damaged":
        assert b'"expected_resume_version":"' + b'e' * 64 + b'"' in posts[0][1]
    else:
        assert b'expected_resume_version' not in posts[0][1]
    assert b"SYNTHETIC_NEW_RESUME" in posts[0][1]
    assert b"SYNTHETIC_RETAINED_NAME" not in posts[0][1] and b"chosen-new.pdf" not in posts[0][1]
    expect(page.locator("#profile-editor-field-0")).to_have_value("SYNTHETIC_RETAINED_NAME")
    assert page.locator("#profile-editor-resume").input_value() == ""


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
@pytest.mark.parametrize("repair_kind", ["missing", "damaged"])
def test_task_workspace_missing_resume_cancel_clears_file_without_save(profile_editor_ui, interruption, repair_kind):
    page, observed = profile_editor_ui
    observed["payload"] = _resume_repair_payload(repair_kind)
    _open_editor(page)
    expect(page.locator("#profile-editor-resume")).to_be_enabled()
    page.locator("#profile-editor-resume").set_input_files({"name": "new.pdf", "mimeType": "application/pdf", "buffer": b"SYNTHETIC_NOT_SAVED"})
    if interruption == "close": page.locator("#profile-editor-close").click()
    elif interruption == "escape": page.locator("#profile-editor-dialog").press("Escape")
    else:
        observed["expired"] = True;page.evaluate("readiness()")
        expect(page.locator("#session-expired")).to_be_visible()
    expect(page.locator("#profile-editor-dialog")).not_to_be_visible()
    assert page.locator("#profile-editor-resume").input_value() == ""
    assert page.locator("#profile-editor-fields").inner_text() == ""
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("repair_kind", ["missing", "damaged"])
def test_task_workspace_missing_resume_reconcile_is_separate_from_replacement(profile_editor_ui, repair_kind):
    page, observed = profile_editor_ui
    observed["payload"] = _resume_repair_payload(repair_kind)
    observed["payload"]["admission_status"] = "reconciliation_required"
    _open_editor(page)
    expect(page.locator("#profile-editor-resume")).to_be_disabled()
    expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
    observed["payload"]["admission_status"] = "resume_replacement_required"
    page.locator("#profile-editor-reconcile").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("简历文件仍然缺失" if repair_kind == "missing" else "简历内容仍与保存记录不符")
    expect(page.locator("#profile-editor-resume")).to_be_enabled()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert [path for path, _ in _editor_posts(observed)] == ["/ui/api/profile-editor/reconcile"]


@pytest.mark.parametrize("failure", ["conflict", "unknown_response"])
@pytest.mark.parametrize("repair_kind", ["missing", "damaged"])
def test_task_workspace_missing_resume_save_uncertainty_never_replays(profile_editor_ui, failure, repair_kind):
    page, observed = profile_editor_ui
    observed["payload"] = _resume_repair_payload(repair_kind)
    _open_editor(page)
    expect(page.locator("#profile-editor-resume")).to_be_enabled()
    page.locator("#profile-editor-resume").set_input_files({"name": "new.pdf", "mimeType": "application/pdf", "buffer": b"SYNTHETIC_UNCERTAIN"})
    observed["save_status"] = 409 if failure == "conflict" else 500
    observed["save_payload"] = {"error": "state_conflict" if failure == "conflict" else "PRIVATE_UNTRUSTED_ERROR"}
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert page.locator("#profile-editor-resume").input_value() == ""
    page.evaluate("document.querySelector('#profile-editor-save').dispatchEvent(new MouseEvent('click'))")
    assert len(_editor_posts(observed)) == 1
    assert "PRIVATE_UNTRUSTED_ERROR" not in page.locator("#profile-editor-dialog").inner_text()


@pytest.mark.parametrize("repair_kind", ["missing", "damaged"])
def test_task_workspace_missing_resume_held_read_requires_ready_chooser(profile_editor_ui, repair_kind):
    """Reproduce the non-human early-selection race, then use the real UI gate.

    set_input_files has no enabled/actionability check. Dialog visibility alone
    does not admit a selection while the asynchronous profile read is pending.
    """
    page, observed = profile_editor_ui
    observed["payload"] = _resume_repair_payload(repair_kind)
    observed["hold"] = True
    _open_editor(page)
    page.wait_for_function("window.__editorHeldReads === 1")
    chooser = page.locator("#profile-editor-resume")
    save = page.locator("#profile-editor-save")
    expect(chooser).to_be_disabled()
    expect(save).to_be_disabled()
    # Deliberate diagnostic bypass only: reproduce the old test actor's mistake.
    # A person cannot select through this disabled control.
    chooser.set_input_files({"name": "premature.pdf", "mimeType": "application/pdf",
                             "buffer": b"SYNTHETIC_PREMATURE_SELECTION"})
    assert chooser.evaluate("node => node.files.length") == 1
    expect(chooser).to_be_disabled()
    expect(save).to_be_disabled()
    route, payload = observed["pending"].pop()
    observed["hold"] = False
    route.fulfill(content_type="application/json", body=json.dumps(payload))
    expect(chooser).to_be_enabled()
    expect(chooser).to_have_value("")
    expect(save).to_be_disabled()
    assert _editor_posts(observed) == []
    # Actual consumer action starts only after the trusted read admits input.
    chooser.set_input_files({"name": "chosen.pdf", "mimeType": "application/pdf",
                             "buffer": b"SYNTHETIC_ADMITTED_SELECTION"})
    expect(save).to_be_enabled()
    observed["save_status"] = 409
    observed["save_payload"] = {"error": "state_conflict"}
    save.click()
    expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
    expect(save).to_be_disabled()
    expect(chooser).to_have_value("")
    posts = _editor_posts(observed)
    assert len(posts) == 1
    assert b"SYNTHETIC_ADMITTED_SELECTION" in posts[0][1]
    assert b"SYNTHETIC_PREMATURE_SELECTION" not in posts[0][1]
    page.evaluate("document.querySelector('#profile-editor-save').dispatchEvent(new MouseEvent('click'))")
    assert len(_editor_posts(observed)) == 1


@pytest.mark.parametrize("failure", ["new_profile", "missing_version", "missing_kind", "editable_fact", "ready_status"])
@pytest.mark.parametrize("repair_kind", ["missing", "damaged"])
def test_task_workspace_missing_resume_malformed_projection_fails_closed(profile_editor_ui, failure, repair_kind):
    page, observed = profile_editor_ui
    data = _resume_repair_payload(repair_kind)
    if failure == "new_profile": data["mode"] = "new"
    elif failure == "missing_version": data["profile_version"] = None
    elif failure == "missing_kind": data["resume"]["kind"] = None
    elif failure == "editable_fact": data["fields"][0]["editable"] = True
    else: data["admission_status"] = "ready"
    observed["payload"] = data
    _open_editor(page)
    expect(page.locator("#profile-editor-status")).to_contain_text("无法读取资料")
    expect(page.locator("#profile-editor-resume")).to_be_disabled()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert page.locator("#profile-editor-fields").inner_text() == ""
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("fault", ["absent", "invalid", "healthy_with_token", "missing_with_token"])
def test_task_workspace_damaged_resume_version_projection_refuses_reuse(profile_editor_ui, fault):
    page, observed = profile_editor_ui
    data = _resume_repair_payload("damaged")
    if fault == "absent": data["resume"].pop("version")
    elif fault == "invalid": data["resume"]["version"] = "invalid"
    elif fault == "healthy_with_token":
        data = _editor_payload();data["resume"]["version"] = "e" * 64
    else: data["resume"]["status"] = "missing_managed"
    observed["payload"] = data
    _open_editor(page)
    expect(page.locator("#profile-editor-status")).to_contain_text("无法读取资料")
    expect(page.locator("#profile-editor-resume")).to_be_disabled()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("checks,visible", [
    ({"profile_configured": False, "profile_exists": False, "profile_loadable": False}, True),
    ({"profile_configured": True, "profile_exists": True, "profile_loadable": True}, False),
    ({"profile_configured": True, "profile_exists": False, "profile_loadable": False}, False),
    ({"profile_configured": False}, False),
    ({"profile_configured": "false", "profile_exists": False, "profile_loadable": False}, False),
    ({}, False),
])
def test_task_workspace_profile_onboarding_uses_observed_missing_state_without_private_reads(profile_editor_ui, checks, visible):
    page, observed = profile_editor_ui
    observed["readiness_checks"] = checks
    page.evaluate("readiness()")
    assert page.locator("#profile-onboarding").is_visible() is visible
    assert not any(path == "/ui/api/profile-editor" for _, path, _ in observed["requests"])
    assert _editor_posts(observed) == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_task_workspace_profile_onboarding_opens_existing_editor_without_json_or_duplicate_read(profile_editor_ui):
    import os
    from pathlib import Path
    page, observed = profile_editor_ui
    observed["profile_selected"] = False
    observed["payload"].update(mode="new", profile_version=None)
    observed["readiness_checks"] = {"profile_configured": False, "profile_exists": False, "profile_loadable": False}
    page.evaluate("readiness()")
    expect(page.locator("#profile-onboarding-open")).to_be_visible()
    expect(page.locator("#profile-onboarding")).to_contain_text("本机还没有选择个人资料")
    screenshot_dir = os.environ.get("JAE_UI_SCREENSHOT_DIR")
    destination = Path(screenshot_dir) if screenshot_dir else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination / "profile-onboarding-start.png"), full_page=True, animations="disabled")
    page.locator("#profile-onboarding-open").click()
    expect(page.locator("#profile-editor-dialog")).to_be_visible()
    expect(page.locator("#profile-editor-status")).to_contain_text("尚无资料")
    assert not page.locator("#profile-dialog").is_visible()
    assert not page.locator("#profile-file").is_visible()
    page.evaluate("document.querySelector('#profile-onboarding-open').click()")
    assert [path for _, path, _ in observed["requests"]].count("/ui/api/profile-editor") == 1
    assert _editor_posts(observed) == []
    page.locator("#profile-editor-field-0").fill("SYNTHETIC_UNSAVED")
    page.locator("#profile-editor-close").click()
    expect(page.locator("#profile-editor-dialog")).not_to_be_visible()
    assert page.locator("#profile-editor-fields").inner_text() == ""
    assert _editor_posts(observed) == []
    page.locator("#profile-setup").click()
    expect(page.locator("#profile-status")).to_contain_text("无需准备资料文件")
    assert not page.locator("#profile-file").is_visible()
    if destination:
        page.locator("#profile-dialog").screenshot(path=str(destination / "profile-onboarding-settings.png"), animations="disabled")
    page.locator("#profile-import summary").click()
    expect(page.locator("#profile-file")).to_be_visible()


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_profile_onboarding_late_setup_reply_never_opens_private_editor(profile_editor_ui, interruption):
    page, observed = profile_editor_ui
    observed["readiness_checks"] = {"profile_configured": False, "profile_exists": False, "profile_loadable": False}
    page.evaluate("readiness()")
    pending = []
    page.route("**/ui/api/profile-setup", lambda route: pending.append(route))
    page.locator("#profile-onboarding-open").click()
    expect(page.locator("#profile-dialog")).to_be_visible()
    assert len(pending) == 1
    if interruption == "close":
        page.locator("#profile-close").click()
    elif interruption == "escape":
        page.locator("#profile-dialog").press("Escape")
    else:
        observed["expired"] = True
        page.evaluate("readiness()")
        expect(page.locator("#session-expired")).to_be_visible()
    with page.expect_response("**/ui/api/profile-setup"):
        pending[0].fulfill(content_type="application/json", body=json.dumps({
            "settings_version": "a" * 64, "profile_selected": False, "submit_capability": False,
            "reconciliation_required": False}))
    page.wait_for_function("!document.querySelector('#profile-dialog').open")
    assert not page.locator("#profile-editor-dialog").is_visible()
    assert not any(path == "/ui/api/profile-editor" for _, path, _ in observed["requests"])
    assert _editor_posts(observed) == []


def test_task_workspace_profile_onboarding_late_readiness_cannot_replace_newer_saved_observation(profile_editor_ui):
    page, observed = profile_editor_ui
    pending = []
    def hold(route):
        pending.append(route)
        page.evaluate("count => window.__onboardingReadinessRequests=count", len(pending))
    page.route("**/ui/api/readiness", hold)
    page.evaluate("void readiness()")
    page.wait_for_function("window.__onboardingReadinessRequests===1")
    page.evaluate("void readiness()")
    page.wait_for_function("window.__onboardingReadinessRequests===2")
    assert len(pending) == 2
    def answer(route, configured):
        route.fulfill(content_type="application/json", body=json.dumps({
            "ready_for_live_e2e": False, "submit_capability": False,
            "message": "SAVED_OBSERVATION" if configured else "OLD_OBSERVATION",
            "checks": {name: configured for name in ("profile_configured", "profile_exists", "profile_loadable")}}))
    answer(pending[1], True)
    expect(page.locator("#readiness")).to_have_text("SAVED_OBSERVATION")
    with page.expect_response("**/ui/api/readiness"):
        answer(pending[0], False)
    expect(page.locator("#readiness")).to_have_text("SAVED_OBSERVATION")
    assert not page.locator("#profile-onboarding").is_visible()
    assert _editor_posts(observed) == []


def test_task_workspace_profile_onboarding_actual_save_resume_reopen_preserves_existing_task(profile_setup_service, monkeypatch):
    from pathlib import Path
    from executor.autonomy.preflight import collect_live_preflight
    settings, queue, supervisor, base, tid, calls = profile_setup_service
    old_task = queue.get(tid)
    old = Path(old_task["spec"]["profile_ref"])
    old_bytes = old.read_bytes()
    previous = settings.load_settings()
    settings.save_settings({**previous, "profile_path": None})
    monkeypatch.setattr(supervisor, "readiness", lambda: collect_live_preflight(
        settings.load_settings(), supervisor_running=True, browser_mode_value="isolated",
        chrome_exists=False, cdp_alive=False, deepseek_available=False))
    writes, external, errors = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 600, "height": 800})
            def local_only(route):
                if route.request.url.startswith(base + "/"):
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()
            context.route("**/*", local_only)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            expect(page.locator("#profile-onboarding")).to_be_visible()
            page.locator("#profile-onboarding-open").click()
            expect(page.locator("#profile-editor-field-0")).to_be_enabled()
            page.locator("#profile-editor-field-0").fill("SYNTHETIC_FIRST_USE")
            resume = b"%PDF-1.4\nSYNTHETIC_LOCAL_RESUME\n%%EOF"
            page.locator("#profile-editor-resume").set_input_files({
                "name": "synthetic-resume.pdf", "mimeType": "application/pdf", "buffer": resume})
            assert writes == [] and settings.load_settings()["profile_path"] is None
            page.locator("#profile-editor-save").click()
            expect(page.locator("#profile-editor-status")).to_contain_text("已保存在本机并重新读取确认")
            page.locator("#profile-editor-close").click()
            expect(page.locator("#profile-onboarding")).not_to_be_visible()
            assert "已就绪" not in page.locator("#readiness").inner_text()
            current = settings.load_settings()
            selected = Path(current["profile_path"])
            profile = json.loads(selected.read_text())
            assert profile["fields"]["identity.full_name"]["value"] == "SYNTHETIC_FIRST_USE"
            asset = profile["assets"]["resume"]
            assert Path(asset["path"]).read_bytes() == resume
            assert queue.get(tid) == old_task and old.read_bytes() == old_bytes
            assert current["unknown_preference"] == previous["unknown_preference"]
            assert supervisor.manager._profile_ref() == str(selected)
            assert writes == [base + "/ui/api/profile-editor"] and calls == []
            page.close()
            reopened = context.new_page()
            reopened.on("pageerror", lambda error: errors.append(str(error)))
            reopened.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            _open_editor(reopened)
            expect(reopened.locator("#profile-editor-field-0")).to_have_value("SYNTHETIC_FIRST_USE")
            expect(reopened.locator("#profile-editor-resume-status")).to_contain_text("本机已记录 PDF")
            assert not reopened.locator("#profile-onboarding").is_visible()
            assert reopened.locator("#profile-editor-resume").input_value() == ""
            assert reopened.evaluate("localStorage.length + sessionStorage.length") == 0
            assert external == [] and errors == [] and calls == []
        finally:
            browser.close()


def test_task_workspace_profile_editor_explicit_private_projection_narrow_and_safe_screenshot(profile_editor_ui):
    import os
    from pathlib import Path
    page, observed = profile_editor_ui
    observed["payload"]["unknown"] = {"identity.id_number": "PRIVATE_HIDDEN_ID", "path": "/private/PRIVATE_PATH"}
    observed["payload"]["fields"][0].update(value="示例姓名", status="supported")
    observed["payload"]["fields"][4].update(value=None, status="unsupported", editable=False)
    page.locator("#message").fill("UNSENT_EDITOR_CHAT")
    assert not any(path == "/ui/api/profile-editor" for _, path, _ in observed["requests"])
    _open_editor(page)
    dialog = page.locator("#profile-editor-dialog")
    expect(page.locator("#profile-editor-field-0")).to_have_value("示例姓名")
    expect(page.locator("#profile-editor-field-4")).to_be_disabled()
    expect(page.locator("#profile-editor-close")).to_be_focused()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert page.locator("#profile-editor-fields input, #profile-editor-fields textarea").count() == 12
    assert "仅用于以后新任务，已有任务仍使用原资料" in dialog.inner_text()
    assert "已记录在本机" in dialog.inner_text() and "缺少记录" in dialog.inner_text()
    private_surface = dialog.evaluate("node => node.outerHTML + [...node.querySelectorAll(\"input,textarea\")].map(item => item.value).join(\"|\")")
    for forbidden in ("PRIVATE_HIDDEN_ID", "PRIVATE_PATH", "UNTRUSTED_LABEL"):
        assert forbidden not in private_surface
    assert not page.locator("#profile-editor-version-details").get_attribute("open")
    assert page.locator("#profile-editor-version-details").evaluate("node => node.open") is False
    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
    assert all(page.locator(selector).bounding_box()["height"] >= 44 for selector in (
        "#profile-editor-close", "#profile-editor-save", "#profile-editor-clear-resume"))
    screenshot_dir = os.environ.get("JAE_UI_SCREENSHOT_DIR")
    if screenshot_dir:
        destination = Path(screenshot_dir)
        destination.mkdir(parents=True, exist_ok=True)
        dialog.screenshot(path=str(destination / "profile-editor-dialog.png"), animations="disabled")
        page.locator("#profile-editor-resume").scroll_into_view_if_needed()
        assert page.locator("#profile-editor-resume").input_value() == ""
        dialog.screenshot(path=str(destination / "profile-editor-resume.png"), animations="disabled")
    page.locator("#profile-editor-close").click()
    expect(dialog).not_to_be_visible()
    expect(page.locator("#profile-setup")).to_be_focused()
    assert page.locator("#profile-editor-fields").inner_text() == ""
    assert page.locator("#profile-editor-version").inner_text() == ""
    assert page.locator("#profile-editor-resume").input_value() == ""
    assert page.locator("#message").input_value() == "UNSENT_EDITOR_CHAT"
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("failure", ["legacy", "capability", "wrong_keys", "malformed", "http", "unsupported_value"])
def test_task_workspace_profile_editor_uneditable_or_malformed_state_fails_closed(profile_editor_ui, failure):
    page, observed = profile_editor_ui
    if failure == "legacy":
        observed["payload"].update(mode="legacy", fields=[])
    elif failure == "capability":
        observed["payload"]["submit_capability"] = True
    elif failure == "wrong_keys":
        observed["payload"]["fields"][0]["key"] = "identity.id_number"
    elif failure == "malformed":
        observed["payload"] = {"message": "PRIVATE_ERROR"}
    elif failure == "http":
        observed["status"] = 500
    else:
        observed["payload"]["fields"][0]["value"] = {"secret": "PRIVATE_ERROR"}
    _open_editor(page)
    expect(page.locator("#profile-editor-status")).to_contain_text("旧版资料格式" if failure == "legacy" else "无法读取资料")
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    expect(page.locator("#profile-editor-resume")).to_be_disabled()
    assert page.locator("#profile-editor-fields").inner_text() == ""
    assert "PRIVATE_ERROR" not in page.locator("#profile-editor-dialog").inner_text()
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_profile_editor_late_get_does_not_revive_private_values(profile_editor_ui, interruption):
    page, observed = profile_editor_ui
    observed["payload"]["fields"][0].update(value="PRIVATE_LATE", status="supported")
    observed["hold"] = True
    _open_editor(page)
    page.wait_for_function("document.querySelector('#profile-editor-status').textContent.includes('正在读取')")
    page.wait_for_function("window.__editorHeldReads === 1")
    assert len(observed["pending"]) == 1
    if interruption == "close":
        page.locator("#profile-editor-close").click()
    elif interruption == "escape":
        page.locator("#profile-editor-dialog").press("Escape")
    else:
        observed["expired"] = True
        page.evaluate("state()")
        expect(page.locator("#session-expired")).to_be_visible()
    route, payload = observed["pending"][0]
    with page.expect_response("**/ui/api/profile-editor"):
        route.fulfill(content_type="application/json", body=json.dumps(payload))
    page.evaluate("async () => {await Promise.resolve()}")
    expect(page.locator("#profile-editor-dialog")).not_to_be_visible()
    assert page.locator("#profile-editor-fields").inner_text() == ""
    assert page.locator("#profile-editor-version").inner_text() == ""
    assert "PRIVATE_LATE" not in page.locator("body").inner_text()
    assert _editor_posts(observed) == []


def test_task_workspace_profile_editor_old_get_cannot_replace_reopened_editor(profile_editor_ui):
    page, observed = profile_editor_ui
    observed["hold"] = True
    _open_editor(page)
    page.wait_for_function("window.__editorHeldReads === 1")
    assert len(observed["pending"]) == 1
    page.locator("#profile-editor-close").click()
    observed["hold"] = False
    observed["payload"]["fields"][0].update(value="NEW_LOCAL_VALUE", status="supported")
    _open_editor(page)
    expect(page.locator("#profile-editor-field-0")).to_have_value("NEW_LOCAL_VALUE")
    route, payload = observed["pending"][0]
    with page.expect_response("**/ui/api/profile-editor"):
        route.fulfill(content_type="application/json", body=json.dumps(payload))
    page.evaluate("async () => {await Promise.resolve()}")
    expect(page.locator("#profile-editor-field-0")).to_have_value("NEW_LOCAL_VALUE")
    assert _editor_posts(observed) == []


@pytest.mark.parametrize("interruption", ["close", "escape", "expired_session"])
def test_task_workspace_profile_editor_delayed_serialization_never_posts_after_interruption(profile_editor_ui, interruption):
    page, observed = profile_editor_ui
    _open_editor(page)
    page.locator("#profile-editor-field-0").fill("PRIVATE_UNSENT")
    page.evaluate("""() => {
      const original = Response.prototype.arrayBuffer;
      Response.prototype.arrayBuffer = function() {
        return new Promise(resolve => {window.__finishEditorBody = async () => resolve(await original.call(this));});
      };
    }""")
    page.locator("#profile-editor-save").click()
    page.wait_for_function("typeof window.__finishEditorBody === 'function'")
    page.evaluate("document.querySelector('#profile-editor-save').dispatchEvent(new MouseEvent('click'))")
    if interruption == "close":
        page.locator("#profile-editor-close").click()
    elif interruption == "escape":
        page.locator("#profile-editor-dialog").press("Escape")
    else:
        observed["expired"] = True
        page.evaluate("state()")
        expect(page.locator("#session-expired")).to_be_visible()
    page.evaluate("window.__finishEditorBody()")
    page.evaluate("async () => {await Promise.resolve()}")
    assert _editor_posts(observed) == []
    assert page.locator("#profile-editor-fields").inner_text() == ""
    expect(page.locator("#profile-editor-dialog")).not_to_be_visible()


@pytest.mark.parametrize("failure", ["conflict", "uncertain", "readback", "network"])
def test_task_workspace_profile_editor_unconfirmed_save_needs_explicit_reconcile_without_replay(profile_editor_ui, failure):
    page, observed = profile_editor_ui
    _open_editor(page)
    page.locator("#profile-editor-field-0").fill("PRIVATE_MODIFIED")
    if failure in {"conflict", "uncertain"}:
        observed["save_status"] = 409
        observed["save_payload"] = {"error": "publication_uncertain" if failure == "uncertain" else "state_conflict",
                                    "reconciliation_status": "required", "message": "PRIVATE_ERROR"}
    elif failure == "readback":
        observed["save_payload"] = {**observed["payload"], "settings_version": "c" * 64, "save_status": "saved"}
    else:
        def broken(route):
            if route.request.method == "POST":
                observed["requests"].append(("POST", "/ui/api/profile-editor", route.request.post_data_buffer))
                route.abort()
            else:
                route.fallback()
        page.route("**/ui/api/profile-editor", broken)
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("本次保存因资料版本变化" if failure == "conflict" else "保存尚未确认")
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
    assert len(_editor_posts(observed)) == 1
    assert "PRIVATE_ERROR" not in page.locator("#profile-editor-dialog").inner_text()
    page.evaluate("document.querySelector('#profile-editor-save').dispatchEvent(new MouseEvent('click'))")
    assert len(_editor_posts(observed)) == 1
    page.locator("#profile-editor-reconcile").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("已重新读取本机当前记录")
    expect(page.locator("#profile-editor-field-0")).to_have_value("")
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert [path for path, _ in _editor_posts(observed)] == ["/ui/api/profile-editor", "/ui/api/profile-editor/reconcile"]


def test_task_workspace_profile_editor_json_selection_blocks_switch_and_resume_can_be_withdrawn(profile_editor_ui):
    page, observed = profile_editor_ui
    page.locator("#profile-setup").click()
    expect(page.locator("#profile-file")).to_be_enabled()
    page.locator("#profile-file").set_input_files({"name": "profile.json", "mimeType": "application/json", "buffer": b'{}'})
    expect(page.locator("#profile-editor-open")).to_be_disabled()
    page.locator("#profile-file").set_input_files([])
    page.locator("#profile-editor-open").click()
    expect(page.locator("#profile-editor-resume")).to_be_enabled()
    page.locator("#profile-editor-resume").set_input_files({"name": "unsafe.html", "mimeType": "text/html", "buffer": b'<script>1</script>'})
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("请选择非空")
    assert _editor_posts(observed) == []
    page.locator("#profile-editor-clear-resume").click()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert page.locator("#profile-editor-resume").input_value() == ""
    assert _editor_posts(observed) == []


def test_task_workspace_profile_editor_actual_save_resume_cas_and_restart_binding(profile_setup_service):
    import hashlib
    from pathlib import Path
    from executor.autonomy.queue import TaskSpec
    settings, queue, supervisor, base, tid, calls = profile_setup_service
    old_task = queue.get(tid)
    old = Path(old_task["spec"]["profile_ref"])
    profile = json.loads(old.read_text())
    profile["fields"]["identity.id_number"] = {"value": "PRIVATE_HIDDEN_ID"}
    profile["fields"]["language.cet6.score"] = {"value": 550, "note": "PRESERVED"}
    profile["unknown_extension"] = {"full": "PRIVATE_PRESERVED"}
    old.write_text(json.dumps(profile))
    old_bytes = old.read_bytes()
    writes, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 600, "height": 800})
        try:
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: writes.append(request.url) if request.method == "POST" else None)
            page.goto(base + "/ui-login?ticket=" + supervisor.issue_ui_ticket())
            _open_editor(page)
            expect(page.locator("#profile-editor-field-0")).to_have_value("PRIVATE_OLD")
            expect(page.locator("#profile-editor-field-10")).to_have_value("550")
            assert "PRIVATE_HIDDEN_ID" not in page.locator("body").inner_text()
            assert "PRIVATE_PRESERVED" not in page.locator("body").inner_text()
            visible_private_values = page.locator("#profile-editor-dialog").evaluate("node => node.outerHTML + [...node.querySelectorAll(\"input,textarea\")].map(item => item.value).join(\"|\")")
            assert "PRIVATE_HIDDEN_ID" not in visible_private_values and "PRIVATE_PRESERVED" not in visible_private_values
            page.locator("#profile-editor-field-0").fill("PRIVATE_NEW")
            page.locator("#profile-editor-field-2").fill("synthetic@example.test")
            page.locator("#profile-editor-field-11").fill("武汉\n北京")
            resume = b'%PDF-1.4\nsynthetic opaque resume\n%%EOF'
            page.locator("#profile-editor-resume").set_input_files({"name": "PRIVATE_FILENAME.pdf", "mimeType": "application/pdf", "buffer": resume})
            assert writes == [] and old.read_bytes() == old_bytes
            page.locator("#profile-editor-save").click()
            expect(page.locator("#profile-editor-status")).to_contain_text("已保存在本机并重新读取确认")
            new = Path(settings.load_settings()["profile_path"])
            updated = json.loads(new.read_text())
            assert new != old and old.read_bytes() == old_bytes and queue.get(tid) == old_task
            assert updated["fields"]["identity.full_name"]["value"] == "PRIVATE_NEW"
            assert updated["fields"]["preferences.preferred_cities"]["value"] == ["武汉", "北京"]
            assert updated["fields"]["language.cet6.score"] == profile["fields"]["language.cet6.score"]
            assert updated["fields"]["identity.id_number"] == profile["fields"]["identity.id_number"]
            assert updated["unknown_extension"] == profile["unknown_extension"]
            asset = updated["assets"]["resume"]
            assert Path(asset["path"]).read_bytes() == resume
            assert asset["sha256"] == hashlib.sha256(resume).hexdigest()
            assert "PRIVATE_FILENAME" not in asset["path"]
            assert new.stat().st_mode & 0o777 == 0o600
            assert Path(asset["path"]).stat().st_mode & 0o777 == 0o600
            assert supervisor.manager._profile_ref() == str(new)
            assert supervisor.worker.settings["profile_path"] == str(new)
            assert writes == [base + "/ui/api/profile-editor"] and calls == []
            assert page.locator("#profile-editor-resume").input_value() == ""
            page.locator("#profile-editor-close").click()
            page.reload()
            expect(page.locator("#profile-setup")).to_be_visible()
            assert page.locator("#profile-editor-fields").inner_text() == ""
            _open_editor(page)
            expect(page.locator("#profile-editor-field-0")).to_have_value("PRIVATE_NEW")
            expect(page.locator("#profile-editor-resume-status")).to_contain_text("本机已记录 PDF")
            assert page.evaluate("localStorage.length + sessionStorage.length") == 0
            # Reconstructed manager/worker use persisted future selection; old task remains exact.
            from executor.autonomy.worker import Worker
            from executor.autonomy.manager import ManagerController
            restarted_worker = Worker(queue, settings=settings.load_settings())
            restarted_manager = ManagerController(queue, restarted_worker, provider=supervisor.manager.provider,
                                                  settings=settings.load_settings())
            assert restarted_manager._profile_ref() == str(new)
            future = queue.enqueue(TaskSpec(company="Future Synthetic Co", role="Engineer", job_id="future-editor",
                                            target_url="https://synthetic.example.test/future",
                                            profile_ref=restarted_manager._profile_ref(), live_authorized=False))
            assert future["spec"]["profile_ref"] == str(new)
            assert future["spec"]["live_authorized"] is False
            assert queue.get(tid) == old_task and old.read_bytes() == old_bytes
            assert errors == [] and calls == []
        finally:
            browser.close()


def test_task_workspace_profile_editor_oversize_metadata_and_city_rows_block_before_post(profile_editor_ui):
    page, observed = profile_editor_ui
    # Existing supported Unicode is counted as code points, not UTF-16 units.
    observed["payload"]["fields"][0].update(value="😀" * 1800, status="supported")
    _open_editor(page)
    expect(page.locator("#profile-editor-field-0")).to_have_value("😀" * 1800)
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    page.locator("#profile-editor-field-11").fill("城市\n" * 33)
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("城市最多 32 行")
    assert _editor_posts(observed) == []
    page.locator("#profile-editor-field-11").fill("")
    for index in range(11):
        page.locator(f"#profile-editor-field-{index}").fill("汉" * 2048)
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("本次资料内容过长")
    assert _editor_posts(observed) == []


def test_task_workspace_profile_editor_deterministic_rejection_is_not_reported_as_saved_or_uncertain(profile_editor_ui):
    page, observed = profile_editor_ui
    observed["save_status"] = 400
    observed["save_payload"] = {"error": "invalid_request", "message": "PRIVATE_DO_NOT_RENDER"}
    _open_editor(page)
    page.locator("#profile-editor-field-0").fill("PRIVATE_EDIT")
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("资料未保存")
    expect(page.locator("#profile-editor-reconcile")).not_to_be_visible()
    expect(page.locator("#profile-editor-field-0")).to_have_value("PRIVATE_EDIT")
    assert "PRIVATE_DO_NOT_RENDER" not in page.locator("#profile-editor-dialog").inner_text()
    assert len(_editor_posts(observed)) == 1


@pytest.mark.parametrize("interruption", ["close_reopen", "expired_session"])
def test_task_workspace_profile_editor_late_post_response_cannot_revive_or_rewrite_dialog(profile_editor_ui, interruption):
    page, observed = profile_editor_ui
    pending = []
    def held_post(route):
        if route.request.method == "POST":
            pending.append(route)
            observed["requests"].append(("POST", "/ui/api/profile-editor", route.request.post_data_buffer))
            page.evaluate("window.__editorHeldPost = true")
        else:
            route.fallback()
    page.route("**/ui/api/profile-editor", held_post)
    _open_editor(page)
    page.locator("#profile-editor-field-0").fill("PRIVATE_POSTED")
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    page.wait_for_function("window.__editorHeldPost === true")
    assert len(pending) == 1
    if interruption == "close_reopen":
        page.locator("#profile-editor-close").click()
        _open_editor(page)
        expect(page.locator("#profile-editor-field-0")).to_have_value("")
    else:
        observed["expired"] = True
        page.evaluate("state()")
        expect(page.locator("#session-expired")).to_be_visible()
    with page.expect_response("**/ui/api/profile-editor"):
        pending[0].fulfill(content_type="application/json", body=json.dumps({**observed["payload"], "save_status": "saved"}))
    page.evaluate("async () => {await Promise.resolve()}")
    if interruption == "close_reopen":
        expect(page.locator("#profile-editor-field-0")).to_have_value("")
        assert "已保存在本机并重新读取确认" not in page.locator("#profile-editor-status").inner_text()
    else:
        expect(page.locator("#profile-editor-dialog")).not_to_be_visible()
        assert page.locator("#profile-editor-fields").inner_text() == ""
    assert len(_editor_posts(observed)) == 1
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_task_workspace_profile_editor_pending_admission_requires_successful_explicit_reconciliation(profile_editor_ui):
    page, observed = profile_editor_ui
    observed["payload"]["admission_status"] = "reconciliation_required"
    observed["reconcile_status"] = 409
    _open_editor(page)
    expect(page.locator("#profile-editor-reconcile")).to_be_enabled()
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    expect(page.locator("#profile-editor-resume")).to_be_disabled()
    page.locator("#profile-editor-reconcile").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("当前记录仍未确认")
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    assert [path for path, _ in _editor_posts(observed)] == ["/ui/api/profile-editor/reconcile"]
    observed["payload"]["admission_status"] = "ready"
    observed["reconcile_status"] = 200
    page.locator("#profile-editor-reconcile").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("已重新读取本机当前记录")
    expect(page.locator("#profile-editor-resume")).to_be_enabled()
    assert all(path == "/ui/api/profile-editor/reconcile" for path, _ in _editor_posts(observed))


@pytest.mark.parametrize("field_index,original", [(0, "\n姓名\t尾\n"), (10, "\n550\t\n")])
def test_task_workspace_profile_editor_untouched_multiline_value_is_never_sanitized_into_patch(profile_editor_ui, field_index, original):
    page, observed = profile_editor_ui
    observed["payload"]["fields"][field_index].update(value=original, status="supported")
    _open_editor(page)
    field = page.locator(f"#profile-editor-field-{field_index}")
    expect(field).to_have_value(original)
    assert field.evaluate("node => node.tagName") == "TEXTAREA"
    expect(page.locator("#profile-editor-save")).to_be_disabled()
    page.locator("#profile-editor-field-2").fill("synthetic@example.test")
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("已保存在本机并重新读取确认")
    posts = _editor_posts(observed)
    assert len(posts) == 1
    metadata = json.loads(posts[0][1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0])
    assert metadata["edits"] == {"identity.email": "synthetic@example.test"}
    expect(field).to_have_value(original)


def test_task_workspace_profile_editor_explicit_multiline_edit_preserves_exact_line_breaks(profile_editor_ui):
    page, observed = profile_editor_ui
    observed["payload"]["fields"][0].update(value="旧\n资料", status="supported")
    _open_editor(page)
    changed = "\n新\t资料\n第二行\n"
    page.locator("#profile-editor-field-0").fill(changed)
    page.locator("#profile-editor-save").click()
    expect(page.locator("#profile-editor-status")).to_contain_text("已保存在本机并重新读取确认")
    posts = _editor_posts(observed)
    assert len(posts) == 1
    metadata = json.loads(posts[0][1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0])
    assert metadata["edits"] == {"identity.full_name": changed}
