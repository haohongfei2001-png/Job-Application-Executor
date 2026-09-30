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
                route.fulfill(status=200, content_type="text/html", body='<link rel="icon" href="data:,"><p>合成官方页面</p>')
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
