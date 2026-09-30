"""Finite startup waits; no user process or live browser is involved."""

import io
import threading
import time
from types import SimpleNamespace
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from executor.autonomy import cli


@pytest.fixture
def startup_clock(monkeypatch):
    clock = SimpleNamespace(now=100.0, sleeps=[])

    def sleep(seconds):
        assert seconds > 0
        clock.sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(cli, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=sleep))
    return clock


def test_existing_healthy_service_returns_without_spawning(tmp_path, monkeypatch, startup_clock):
    observed = []
    health = {"ok": True, "final_click_actor": "user"}

    def request(root, port, path, data=None, *, timeout=10):
        observed.append((root, port, path, data, timeout))
        return health

    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: pytest.fail("already healthy"))
    root = tmp_path / "runtime"
    assert cli.lifecycle("start", root, 9344) == health
    assert observed == [(root, 9344, "/health", None, 1.0)]
    assert startup_clock.sleeps == []


@pytest.mark.parametrize("probe_duration", [0.0, 1.0])
def test_startup_uses_one_deadline_and_remaining_probe_budget(
    tmp_path, monkeypatch, startup_clock, probe_duration
):
    probes, children = [], []

    def unavailable(root, port, path, data=None, *, timeout=10):
        assert path == "/health" and data is None
        assert 0 < timeout <= 1.0
        assert timeout <= 115.0 - startup_clock.now + 1e-9
        probes.append((startup_clock.now, timeout))
        startup_clock.now += min(probe_duration, timeout)
        raise urllib.error.URLError("synthetic unavailable")

    def spawn(*args, **kwargs):
        child = SimpleNamespace(poll=lambda: None)
        children.append(child)
        return child

    monkeypatch.setattr(cli, "request", unavailable)
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    assert cli.lifecycle("start", tmp_path / "runtime", 9344) == {
        "ok": False, "reason": "health_timeout"}
    assert startup_clock.now == pytest.approx(115.0)
    assert len(children) == 1
    assert probes and all(start < 115.0 for start, _ in probes)
    assert all(0 < delay <= .1 for delay in startup_clock.sleeps)
    # No terminate/kill API exists on the synthetic child: a timeout must not
    # infer permission to signal an unverified process or start another child.


def test_initial_probe_exhaustion_does_not_spawn(tmp_path, monkeypatch, startup_clock):
    def late_health(*args, **kwargs):
        startup_clock.now = 115.0
        return {"ok": True}

    monkeypatch.setattr(cli, "request", late_health)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: pytest.fail("deadline elapsed"))
    assert cli.lifecycle("start", tmp_path / "runtime", 9344) == {
        "ok": False, "reason": "health_timeout"}


def test_spawn_time_counts_toward_startup_deadline(tmp_path, monkeypatch, startup_clock):
    probes = []

    def unavailable(*args, **kwargs):
        probes.append(kwargs["timeout"])
        raise OSError("synthetic absent")

    def spawn(*args, **kwargs):
        startup_clock.now = 115.0
        return SimpleNamespace(poll=lambda: pytest.fail("deadline elapsed"))

    monkeypatch.setattr(cli, "request", unavailable)
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    assert cli.lifecycle("start", tmp_path / "runtime", 9344) == {
        "ok": False, "reason": "health_timeout"}
    assert probes == [1.0]


def test_startup_returns_health_before_deadline(tmp_path, monkeypatch, startup_clock):
    probes = []
    health = {"ok": True, "final_click_actor": "user"}

    def delayed_health(*args, **kwargs):
        probes.append(kwargs["timeout"])
        startup_clock.now += .4
        if len(probes) < 4:
            raise OSError("synthetic starting")
        return health

    monkeypatch.setattr(cli, "request", delayed_health)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: SimpleNamespace(poll=lambda: None))
    assert cli.lifecycle("start", tmp_path / "runtime", 9344) == health
    assert len(probes) == 4 and startup_clock.now < 115.0


@pytest.mark.parametrize("failure", ["child_exit", "runtime_invalid", "spawn_refused"])
def test_startup_preserves_finite_failure_results(
    tmp_path, monkeypatch, startup_clock, failure
):
    count = 0

    def unavailable(*args, **kwargs):
        nonlocal count
        count += 1
        if failure == "runtime_invalid" and count > 1:
            raise ValueError("synthetic invalid runtime")
        raise OSError("synthetic absent")

    def spawn(*args, **kwargs):
        if failure == "spawn_refused":
            raise OSError("synthetic refused")
        return SimpleNamespace(poll=lambda: 1 if failure == "child_exit" else None)

    monkeypatch.setattr(cli, "request", unavailable)
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    assert cli.lifecycle("start", tmp_path / "runtime", 9344) == {
        "ok": False, "reason": "service_start_failed" if failure == "child_exit"
        else "service_runtime_unavailable"}
    assert startup_clock.now == 100.0


@pytest.mark.parametrize("timeout", [None, .25])
def test_request_preserves_default_and_forwards_explicit_timeout(tmp_path, monkeypatch, timeout):
    observed = []

    def open_request(request, *, timeout):
        observed.append((request.full_url, request.get_method(), timeout))
        assert request.get_header("Authorization").startswith("Bearer ")
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(cli.urllib.request, "urlopen", open_request)
    kwargs = {} if timeout is None else {"timeout": timeout}
    assert cli.request(tmp_path / "runtime", 9344, "/health", **kwargs) == {"ok": True}
    assert observed == [("http://127.0.0.1:9344/health", "GET", 10 if timeout is None else timeout)]


def test_real_stalled_listener_respects_startup_retry_budget(tmp_path, monkeypatch):
    """Actual loopback sockets exercise the timeout, with no spawned service."""
    release = threading.Event()
    calls = []
    children = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if len(calls) == 1:
                self.send_error(503)
                return
            release.wait(5)

        def log_message(self, *_args):
            pass

    def spawn(*args, **kwargs):
        children.append(True)
        return SimpleNamespace(poll=lambda: None)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(cli, "_SERVICE_STARTUP_BUDGET_SECONDS", .5)
    monkeypatch.setattr(cli.subprocess, "Popen", spawn)
    try:
        started = time.monotonic()
        assert cli.lifecycle("start", tmp_path / "runtime", server.server_port) == {
            "ok": False, "reason": "health_timeout"}
        elapsed = time.monotonic() - started
        assert .4 <= elapsed < 3
        assert calls == ["/health", "/health"]
        assert children == [True]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
