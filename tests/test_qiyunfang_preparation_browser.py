"""Independent synthetic browser oracle. No requests to real application sites."""
from __future__ import annotations

import contextlib
import faulthandler
import html
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from executor.preparation import qiyunfang as q
from executor.preparation.runner import PreparationKernel
from executor.preparation.transport import PreparationTransport


class Journal:
    def __init__(self): self.events, self.states, self.invalidated = [], {}, False
    def before(self, field_id):
        action = len(self.events)
        self.events.append((action, field_id, "ATTEMPTED"))
        self.states[action] = "ATTEMPTED"
        return action
    def after(self, action, outcome):
        self.events.append((action, outcome)); self.states[action] = outcome
    def invalidate(self):
        self.invalidated = True
        self.states = {action: "UNKNOWN_OUTCOME" for action in self.states}
        self.events.append(("INVALIDATED",))


def replica(script=""):
    # Independent dated live observation, not generated from driver constants.
    capture = json.loads((Path(__file__).parent / "fixtures/qiyunfang/public_form_observation_20261001.json").read_text())
    rows = []
    for field in capture["roots"][0]["fields"]:
        controls = []
        for observed in field["controls"]:
            attributes = {name: observed.get(name) for name in ("type", "id", "name", "maxlength", "placeholder")}
            if observed["type"] in {"radio", "checkbox"}:
                attributes["value"] = observed.get("public_option_value")
            elif observed["type"] == "button":
                attributes["value"] = observed["public_button_value"]
            elif observed["type"] == "file":
                attributes["style"] = "visibility:hidden;width:72px"
            attr = " ".join(f'{name}="{html.escape(str(value), quote=True)}"' for name, value in attributes.items() if value is not None)
            controls.append("<input " + attr + ">")
            for label in observed["associated_labels"]:
                controls.append(f'<label for="{observed["id"]}">{html.escape(label)}</label>')
        if field["field_id"] == "protocol":
            controls.append('<span class="form_protocol_text">' + field["protocol_text"] + '</span><span class="form_protocol_title">' + field["protocol_title"] + '</span>')
        if field["field_id"] == "Submit":
            controls.append('<div class="submit s_1"><div class="l"></div><div class="m" data-formid="6">' + field["submit_text"] + '</div><div class="r"></div></div>')
        label = field["title_spans"][0] if field["title_spans"] else ""
        rows.append(f'<div class="form_item" data-formid="{field["field_id"]}" data-type="{field["data_type"]}"><div class="title"><span>{label}</span></div>' +
                    ('<div class="star">*</div>' if field["required_marker"] else '') + ''.join(controls) + '</div>')
    return ('<!doctype html><meta charset="utf-8"><div id="popupLevelWrap"><div id="module1567"><div class="m_siteform"><div class="form_container">' +
            ''.join(rows) + '</div></div></div></div><script>' + script + '</script>')


@pytest.fixture(autouse=True)
def bounded_browser_oracle(request):
    print("PREPARATION_TEST_START " + request.node.nodeid, flush=True)
    faulthandler.dump_traceback_later(60, exit=True)
    try: yield
    finally:
        print("PREPARATION_TEST_FINISH " + request.node.nodeid, flush=True)
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        requests = []
        def do_GET(self):
            self.requests.append((self.command, self.path, b""))
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.requests.append((self.command, self.path, body))
            self.send_response(204); self.end_headers()
        def log_message(self, *_): pass
    service = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{service.server_port}", Handler.requests
    finally: service.shutdown(); service.server_close(); thread.join()


@pytest.fixture
def browser():
    with sync_playwright() as p:
        kwargs = {"headless": True}
        if os.environ.get("JAE_TEST_CHROMIUM"):
            kwargs["executable_path"] = os.environ["JAE_TEST_CHROMIUM"]
        b = p.chromium.launch(**kwargs)
        try: yield b
        finally:
            print("PREPARATION_BROWSER_CLOSE_START", flush=True)
            b.close()
            print("PREPARATION_BROWSER_CLOSE_FINISH", flush=True)


@contextlib.contextmanager
def session(browser, script=""):
    context = browser.new_context(service_workers="block")
    guard = PreparationTransport(); guard.install(context)
    # Synthetic source content only. No network request to the official site.
    def bootstrap(route): route.fulfill(status=200, content_type="text/html", body=replica(script))
    context.route(q.CONTRACT_URL, bootstrap)
    page = context.new_page()
    page.goto(q.CONTRACT_URL)
    context.unroute(q.CONTRACT_URL, bootstrap)
    guard.seal()
    try: yield page, guard
    finally:
        print("PREPARATION_CONTEXT_CLOSE_START", flush=True)
        context.close()
        print("PREPARATION_CONTEXT_CLOSE_FINISH", flush=True)


def test_complete_routine_plan_retains_protected_controls_and_never_becomes_ready(browser):
    with session(browser) as (page, guard):
        journal = Journal()
        runner = PreparationKernel(page, guard, lambda _plan_sha: None, journal)
        plan = [{"field_id": "0", "value": "Synthetic Applicant"}, {"field_id": "5", "value": "synthetic@example.test"},
                {"field_id": "8", "value": q.CONTRACT_ROLE}, {"field_id": "17", "value": ["武汉", "成都"]}]
        result = runner.run(plan)
        assert result == {"status": "PREPARED_UNVERIFIED", "field_count": 4, "account_verified": False,
                          "server_draft_verified": False, "ready_to_submit": False, "submit_capability": False}
        assert sum(event[-1] == "READBACK_VERIFIED" for event in journal.events) == 5
        assert "Synthetic" not in json.dumps(journal.events)
        assert q.validate_observation(page.locator(q.ROOT).evaluate(q.OBSERVE_ROOT), expected_values={x["field_id"]: x["value"] for x in plan})
        with pytest.raises(RuntimeError, match="one-shot"): runner.run(plan)


@pytest.mark.parametrize("attack", ["fetch_get", "fetch_post", "xhr", "beacon", "image", "form", "navigation", "popup", "websocket", "timer"])
def test_field_effect_transports_are_denied_and_independent_server_receives_nothing(browser, server, attack):
    origin, requests = server
    u = json.dumps(origin + "/submit-" + attack + "?cmd=addWafCk_addSubmit&value=SYNTHETIC_CANARY")
    attacks = {
        "fetch_get": f"fetch({u}).catch(()=>{{}})",
        "fetch_post": f"fetch({u},{{method:'POST',body:'cmd=addWafCk_addSubmit&value=SYNTHETIC_CANARY'}}).catch(()=>{{}})",
        "xhr": f"const x=new XMLHttpRequest();x.open('POST',{u});x.send('SYNTHETIC_CANARY')",
        "beacon": f"navigator.sendBeacon({u},'SYNTHETIC_CANARY')",
        "image": f"new Image().src={u}",
        "form": f"const f=document.createElement('form');f.method='POST';f.action={u};document.body.append(f);f.submit()",
        "navigation": f"location.href={u}",
        "popup": f"window.open({u})",
        "websocket": f"new WebSocket({u}.replace('http:','ws:'))",
        "timer": f"setTimeout(()=>fetch({u}).catch(()=>{{}}),75)",
    }
    script = 'document.querySelector(\'[data-formid="0"] input\').addEventListener("input",()=>{' + attacks[attack] + '});'
    with session(browser, script) as (page, guard):
        journal = Journal(); runner = PreparationKernel(page, guard, lambda _plan_sha: None, journal)
        try: runner.run([{"field_id": "0", "value": "SYNTHETIC_CANARY"}])
        except Exception: pass
        # Delayed side effects must remain blocked after kernel returns too.
        page.wait_for_timeout(250)
        assert guard.blocked > 0
        assert requests == []
        with pytest.raises(RuntimeError): guard.require_sealed()
    assert requests == []


@pytest.mark.parametrize("mutation", [
    "document.querySelector('.form_container').outerHTML=document.querySelector('.form_container').outerHTML",
    "document.querySelector('[data-formid=\"0\"] input').value=''",
    "document.querySelector('#M1567R13I19').checked=true",
])
def test_late_field_redraw_or_protected_mutation_stops_later_writes(browser, mutation):
    script = 'document.querySelector(\'[data-formid="5"] input\').addEventListener("input",()=>{' + mutation + '});'
    with session(browser, script) as (page, guard):
        journal = Journal()
        with pytest.raises(q.ContractChanged):
            PreparationKernel(page, guard, lambda _plan_sha: None, journal).run([
                {"field_id": "0", "value": "Synthetic"}, {"field_id": "5", "value": "synthetic@example.test"},
                {"field_id": "8", "value": q.CONTRACT_ROLE}])
        assert not page.locator('#M1567R8I19').is_checked()
        assert journal.invalidated
        assert set(journal.states.values()) == {"UNKNOWN_OUTCOME"}


def test_invalid_later_plan_is_rejected_before_first_write(browser):
    with session(browser) as (page, guard):
        journal = Journal()
        with pytest.raises(ValueError):
            PreparationKernel(page, guard, lambda _plan_sha: None, journal).run([
                {"field_id": "0", "value": "Synthetic"}, {"field_id": "5", "value": "x" * 51}])
        assert journal.events == []
        assert page.locator('[data-formid="0"] input').input_value() == ""
