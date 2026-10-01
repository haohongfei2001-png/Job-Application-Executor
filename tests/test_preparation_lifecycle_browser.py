"""Controller-death oracle using synthetic loopback data and an independent server.

No existing desktop browser or applicant profile is ever attached. This checks a
fresh Playwright-created context, including CDP dispose-on-detach, on CI hosts.
"""
from __future__ import annotations

import contextlib
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

CANARY = "SYNTHETIC_LIFECYCLE_CANARY"
HTML = b'''<!doctype html><input id="ordinary"><script>
ordinary.addEventListener('input',()=>{
 const v=ordinary.value;
 setTimeout(()=>fetch('/submit-fetch',{method:'POST',body:v}).catch(()=>{}),1100);
 setTimeout(()=>navigator.sendBeacon('/submit-beacon',v),1600);
 setTimeout(()=>{new Image().src='/submit-image?canary='+v},2100);
 setTimeout(()=>fetch('/submit-late',{method:'POST',body:v}).catch(()=>{}),4100);
},{once:true});</script>'''
CONTROLLER = r'''
import json,sys,time
from playwright.sync_api import sync_playwright
url,phase,cdp=sys.argv[1:]
with sync_playwright() as pw:
 browser=pw.chromium.connect_over_cdp(cdp) if cdp else pw.chromium.launch(headless=True)
 context=browser.new_context(service_workers='block')
 def guard(route):
  if route.request.url==url:
   route.continue_();return
  if phase=='pending' and route.request.url.endswith('/submit-fetch'):
   print(json.dumps({'event':'pending'}),flush=True)
   while True:time.sleep(1)
  route.abort()
 context.route('**/*',guard)
 context.route_web_socket('**/*',lambda route:route.close())
 page=context.new_page();page.goto(url,wait_until='domcontentloaded')
 page.locator('#ordinary').fill('SYNTHETIC_LIFECYCLE_CANARY')
 print(json.dumps({'event':'ready'}),flush=True)
 while True:page.wait_for_timeout(100)
'''


def process_table():
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,stat=,command="], capture_output=True, text=True, check=True)
    table = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2], parts[3])
    return table


def descendants(pid):
    table, selected = process_table(), {pid}
    while True:
        next_ids = {p for p, row in table.items() if row[0] in selected}
        if next_ids <= selected: break
        selected |= next_ids
    return {p: table[p] for p in selected if p in table}


def live_known(known):
    current = process_table()
    return {p: current[p] for p, row in known.items() if p in current and current[p][2] == row[2] and not current[p][1].startswith('Z')}


@pytest.mark.parametrize("transport", ["launch", "cdp_fresh"])
@pytest.mark.parametrize("phase", ["armed", "pending"])
def test_controller_sigkill_never_releases_delayed_requests(transport, phase, tmp_path):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            received.append(("GET", self.path, b""))
            data = HTML if self.path == "/" else b"ok"
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
            with contextlib.suppress(BrokenPipeError): self.wfile.write(data)
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(("POST", self.path, body)); self.send_response(204); self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url, cdp, external, process, known = f"http://127.0.0.1:{server.server_port}/", "", None, None, {}
    selector = selectors.DefaultSelector()
    try:
        if transport == "cdp_fresh":
            with sync_playwright() as pw: executable = pw.chromium.executable_path
            with socket.socket() as sock: sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            # Test-owned disposable browser; never an installed user profile.
            external = subprocess.Popen([executable, "--headless", "--no-sandbox", "--disable-extensions",
                "--disable-background-networking", "--no-first-run", f"--user-data-dir={tmp_path / 'chrome'}",
                "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            cdp = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(cdp + "/json/version", timeout=.5) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if time.monotonic() >= deadline: pytest.fail("test CDP browser did not start")
                    time.sleep(.05)
        process = subprocess.Popen([sys.executable, "-u", "-c", CONTROLLER, url, phase, cdp],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        selector.register(process.stdout, selectors.EVENT_READ)
        expected = "pending" if phase == "pending" else "ready"
        deadline, observed, output = time.monotonic() + 25, False, []
        while time.monotonic() < deadline and process.poll() is None:
            if not selector.select(.2): continue
            line = process.stdout.readline(); output.append(line)
            try: event = json.loads(line)
            except ValueError: continue
            if event.get("event") == expected: observed = True; break
        assert observed, "lifecycle milestone was not reached: " + "".join(output)[-1500:]
        assert any(item[1] == "/" for item in received), "independent source server was never visited"
        known = descendants(process.pid)
        assert len(known) > 1, "Playwright driver process was not observed"
        if external: known.update(descendants(external.pid))
        os.kill(process.pid, signal.SIGKILL); process.wait(timeout=5)
        # Retain the full longest timer window; an unchanged early sample is
        # not evidence that a delayed callback can never escape after detach.
        time.sleep(7)
        assert not [item for item in received if item[1].startswith('/submit-')], "request escaped after controller death"
        if cdp:
            with urllib.request.urlopen(cdp + "/json/list", timeout=2) as response: targets = json.load(response)
            assert not any(target.get("url") == url for target in targets), "fresh preparation context survived detach"
        else:
            assert not live_known(known), "launched driver/browser survived controller death"
    finally:
        if process and process.poll() is None:
            known.update(descendants(process.pid)); process.kill(); process.wait(timeout=5)
        if external and external.poll() is None: known.update(descendants(external.pid))
        for pid in live_known(known):
            with contextlib.suppress(ProcessLookupError): os.kill(pid, signal.SIGKILL)
        if external:
            with contextlib.suppress(subprocess.TimeoutExpired): external.wait(timeout=5)
        selector.close(); server.shutdown(); server.server_close(); thread.join()
