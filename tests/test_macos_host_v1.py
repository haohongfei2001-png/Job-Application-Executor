from __future__ import annotations

import hashlib
import json
import socketserver
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from executor.autonomy.consumer_presentation import ConsumerSurface, present_surface
from executor.autonomy.macos_host import (
    HOST_RECEIPT, HOST_SOURCE, NativePresenter, build_native_host,
    native_command, verify_native_host,
)


@pytest.mark.parametrize("surface", [
    ConsumerSurface.dashboard(43210, "PRIVATE_TICKET_CANARY"),
    ConsumerSurface.from_url("bootstrap", "http://127.0.0.1:43211/?token=PRIVATE_TOKEN_CANARY",
                             service_port=43210),
])
def test_native_command_preserves_private_surface_only_in_memory(surface):
    command = native_command(surface, 1)
    assert command["url"] == surface.url
    assert command["service_origin"] == "http://127.0.0.1:43210"
    assert set(command) == {"command", "request", "surface", "url", "service_origin"}
    assert "PRIVATE_" not in repr(surface)
    assert "PRIVATE_" not in repr(NativePresenter("unused"))
    assert "PRIVATE_" not in json.dumps(surface.safe_summary())


@pytest.mark.parametrize("request_id", [True, False, 0, -1, 1.0, "1", None])
def test_native_command_rejects_non_integer_request(request_id):
    with pytest.raises(ValueError, match="invalid consumer presentation"):
        native_command(ConsumerSurface.dashboard(43210, "opaque"), request_id)


@pytest.mark.parametrize("surface", [
    ConsumerSurface("dashboard", "https://example.invalid/ui-login?ticket=PRIVATE_BAD", "https://example.invalid", "http://127.0.0.1:43210"),
    ConsumerSurface("dashboard", "http://127.0.0.1:43210/ui-login?ticket=PRIVATE_BAD", "http://127.0.0.1:43211", "http://127.0.0.1:43210"),
    ConsumerSurface("bootstrap", "http://127.0.0.1:43211/?token=PRIVATE_BAD", "http://127.0.0.1:43211", "http://localhost:43210"),
])
def test_native_command_revalidates_manually_constructed_dataclass(surface):
    with pytest.raises(ValueError, match="invalid consumer presentation") as refused:
        native_command(surface, 1)
    assert "PRIVATE_" not in str(refused.value)


def _candidate(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    program = root / "AIApplicationWindow"
    program.write_bytes(b"SYNTHETIC_EXECUTABLE")
    program.chmod(0o755)
    (root / HOST_RECEIPT).write_text(json.dumps({
        "format": "jae-native-host-v1",
        "source_sha256": hashlib.sha256(HOST_SOURCE.encode()).hexdigest(),
        "executable_sha256": hashlib.sha256(program.read_bytes()).hexdigest(),
    }))
    return root


def test_native_host_verification_binds_source_binary_permissions_and_exact_payload(tmp_path):
    root = _candidate(tmp_path)
    assert verify_native_host(root)
    program = root / "AIApplicationWindow"
    program.chmod(0o644)
    assert not verify_native_host(root)
    program.chmod(0o755)
    program.write_bytes(b"CHANGED_BINARY")
    assert not verify_native_host(root)


def test_native_host_verification_refuses_alias_or_unrecognized_payload(tmp_path):
    root = _candidate(tmp_path)
    (root / "unexpected").write_text("PRIVATE_UNKNOWN")
    assert not verify_native_host(root)
    (root / "unexpected").unlink()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert not verify_native_host(alias)
    target = root / HOST_RECEIPT
    original = tmp_path / "receipt"
    target.rename(original)
    target.symlink_to(original)
    assert not verify_native_host(root)


def test_refused_native_host_never_opens_browser_or_launches_child(tmp_path, monkeypatch):
    import executor.autonomy.macos_host as module
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid host spawned"))
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("external browser fallback"))
    presenter = NativePresenter(tmp_path / "missing")
    assert not present_surface(ConsumerSurface.dashboard(43210, "PRIVATE_MISSING"), presenter=presenter)
    assert presenter.process is None


@pytest.fixture(scope="module")
def compiled_host(tmp_path_factory):
    if sys.platform != "darwin":
        pytest.skip("Cocoa/WebKit compile and NSWindow oracle run on hosted macOS")
    root = tmp_path_factory.mktemp("native-host") / "candidate"
    receipt = build_native_host(root)
    assert verify_native_host(root)
    assert receipt["format"] == "jae-native-host-v1"
    return root


def test_hosted_mac_actual_cocoa_window_loopback_redirect_cookie_and_page(compiled_host):
    state = {"admissions": 0, "authenticated_pages": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path == "/ui-login?ticket=PRIVATE_NATIVE_TICKET" and not state["admissions"]:
                state["admissions"] += 1
                self.send_response(303)
                self.send_header("Location", "/ui")
                self.send_header("Set-Cookie", "native=SYNTHETIC_COOKIE; HttpOnly; SameSite=Strict; Path=/ui")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            elif self.path == "/ui" and self.headers.get("Cookie") == "native=SYNTHETIC_COOKIE":
                state["authenticated_pages"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"<html><body><p id='native-canary'>SYNTHETIC_NATIVE_CANARY</p><label>Note <textarea></textarea></label></body></html>")
            else:
                self.send_response(401)
                self.end_headers()
    class Loopback(ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name = "127.0.0.1"
            self.server_port = self.server_address[1]
    server = Loopback(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        surface = ConsumerSurface.dashboard(server.server_port, "PRIVATE_NATIVE_TICKET")
        result = subprocess.run([str(compiled_host / "AIApplicationWindow"), "--smoke"],
                                input=json.dumps(native_command(surface, 1)) + "\n",
                                text=True, capture_output=True, timeout=25, check=False)
        assert result.returncode == 0, "native host process failed"
        assert "PRIVATE_NATIVE_TICKET" not in result.stdout + result.stderr
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert replies[0] == {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1}
        assert replies[-1] == {"ok": True, "native_page": True, "window_count": 1,
                               "zoom_reset": True, "external_navigation_denied": True}
        assert state == {"admissions": 1, "authenticated_pages": 1}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_hosted_mac_presenter_reuses_actual_native_process_without_external_browser(compiled_host, monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("external browser opened"))
    presenter = NativePresenter(compiled_host)
    try:
        # No service authority is inferred from window construction. The known
        # loopback endpoint may be unavailable and presentation still owns one
        # window; launch/service health remains the existing separate gate.
        surface = ConsumerSurface.dashboard(43210, "PRIVATE_REUSE_TICKET")
        assert present_surface(surface, presenter=presenter)
        process = presenter.process
        assert process.poll() is None
        assert present_surface(surface, presenter=presenter)
        assert presenter.process is process
        assert presenter.sequence == 2
    finally:
        presenter.close()
