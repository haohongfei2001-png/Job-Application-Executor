from __future__ import annotations

import hashlib
import json
import socket
import socketserver
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from http.client import HTTPConnection

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
    accepted_cookies = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def _native_cookie(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                return ""
            morsel = cookie.get("native")
            return morsel.value if morsel else ""
        def do_GET(self):
            if self.path == "/ui-login?ticket=PRIVATE_NATIVE_TICKET" and not state["admissions"]:
                state["admissions"] += 1
                self.send_response(303)
                self.send_header("Location", "/ui")
                self.send_header("Set-Cookie", "native=SYNTHETIC_COOKIE; HttpOnly; SameSite=Strict; Path=/ui")
                # Persistent WebKit contexts carry other loopback cookies too;
                # authentication binds the named capability, not the whole header.
                self.send_header("Set-Cookie", "unrelated_native_fixture=SYNTHETIC_OTHER; HttpOnly; SameSite=Strict; Path=/ui")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            elif self.path == "/ui" and self._native_cookie() == "SYNTHETIC_COOKIE":
                state["authenticated_pages"] += 1
                accepted_cookies.append(self.headers.get("Cookie", ""))
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
        # Additional cookies cannot substitute for the authenticated capability.
        for cookie in ("unrelated_native_fixture=SYNTHETIC_OTHER",
                       "unrelated_native_fixture=SYNTHETIC_OTHER; native=WRONG_CAPABILITY"):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("GET", "/ui", headers={"Cookie": cookie})
                response = connection.getresponse()
                assert response.status == 401
                response.read()
            finally:
                connection.close()
        assert state == {"admissions": 0, "authenticated_pages": 0}
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
        assert len(accepted_cookies) == 1
        cookies = SimpleCookie(accepted_cookies[0])
        assert cookies["native"].value == "SYNTHETIC_COOKIE"
        assert cookies["unrelated_native_fixture"].value == "SYNTHETIC_OTHER"
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


def test_retained_native_receipt_is_bound_to_its_owned_source_without_execution(tmp_path, monkeypatch):
    from executor.autonomy import consumer
    root = _candidate(tmp_path)
    release = tmp_path / "release"
    source = release / "executor" / "autonomy" / "macos_host.py"
    source.parent.mkdir(parents=True)
    historical = "SYNTHETIC_OLD_HOST_SOURCE"
    source.write_text("raise RuntimeError('CANDIDATE_SOURCE_MUST_NOT_EXECUTE')\nHOST_SOURCE = " + repr(historical))
    receipt = json.loads((root / HOST_RECEIPT).read_text())
    receipt["source_sha256"] = hashlib.sha256(historical.encode()).hexdigest()
    (root / HOST_RECEIPT).write_text(json.dumps(receipt))
    monkeypatch.setattr(consumer, "verify_source_candidate", lambda _root: True)
    assert not verify_native_host(root)
    assert consumer._native_bundle_matches(release, root)
    source.write_text("HOST_SOURCE = 'changed'")
    assert not consumer._native_bundle_matches(release, root)
    source.write_text("HOST_SOURCE = " + repr(historical) + "\nHOST_SOURCE = " + repr(historical))
    assert not consumer._native_bundle_matches(release, root)
    source.write_text("HOST_SOURCE = lambda: 'untrusted'")
    assert not consumer._native_bundle_matches(release, root)


def test_native_receipt_refuses_non_file_without_reading_fifo(tmp_path):
    import os
    root = _candidate(tmp_path)
    (root / HOST_RECEIPT).unlink()
    os.mkfifo(root / HOST_RECEIPT)
    assert not verify_native_host(root)


def test_unverified_native_app_never_starts_service_or_browser(tmp_path, monkeypatch):
    from executor.autonomy import cli
    monkeypatch.setattr(cli, "launch_consumer", lambda *a, **k: pytest.fail("unverified app started service"))
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("external fallback"))
    result = cli.launch_native_consumer(tmp_path / "state", 9344)
    assert result == {"ok": False, "opened": False, "reason": "native_bundle_unverified"}


@pytest.mark.parametrize("mode", [None, 0, 1, "native"])
def test_native_presenter_mode_requires_strict_bool(mode):
    with pytest.raises(ValueError, match="native_host_mode_invalid"):
        NativePresenter("unused", consumer_smoke=mode)


@pytest.fixture
def native_installed_app(tmp_path):
    import os
    from pathlib import Path
    from executor.autonomy import consumer
    from executor.autonomy.release import copy_source_candidate
    if sys.platform != "darwin":
        pytest.skip("Installed native application uses hosted macOS Cocoa/WebKit")
    runtime = Path(os.environ["JAE_STANDALONE_RUNTIME"])
    repo = tmp_path / "source"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    apps = tmp_path / "Applications"
    home = tmp_path / "home"
    home.mkdir()
    state = home / "Library" / "Application Support" / "AI投递经理" / "autonomy"
    result = consumer.install_macos_app(repo, destination=apps,
        standalone_runtime=runtime, task_state_root=state, native_presentation=True)
    assert result["ok"] is True and result["presentation"] == "native"
    app = apps / (consumer.APP_NAME + ".app")
    assert consumer._trusted_bundle(app) and consumer._isolated_bundle_startup(app)
    assert consumer._native_bundle_matches(app / "Contents/Resources/release",
                                          app / "Contents/Resources/native-host")
    return {"repo": repo, "apps": apps, "home": home, "state": state,
            "app": app, "runtime": runtime}


def test_hosted_mac_installed_native_app_opens_actual_ui_twice_without_checkout_or_browser(
    native_installed_app, tmp_path
):
    import os
    from pathlib import Path
    from executor.autonomy.process_entry import CLI_ENTRY_SCRIPT
    from executor.autonomy.queue import TaskQueue, TaskSpec

    f = native_installed_app
    task = TaskQueue(f["state"]).enqueue(TaskSpec(company="Synthetic native",
        role="Engineer", target_url="https://example.invalid/jobs/native",
        profile_ref="synthetic-profile.json"))
    # A runnable task is deliberately claimed by the existing supervisor.
    # Preserve the user's explicit pause across installation/launch instead of
    # treating ordinary scheduler progress as a native presentation mutation.
    before = TaskQueue(f["state"]).pause(task["task_id"])
    assert before["run_state"] == "PAUSED" and before["attempts"] == 0
    assert before["wait_reason"] == "user_paused"
    f["repo"].rename(tmp_path / "checkout-removed")
    source = f["app"] / "Contents/Resources/release"
    runtime = f["app"] / "Contents/Resources/runtime"
    executable = f["app"] / "Contents/MacOS/AIApplicationManager"
    poison = tmp_path / "ambient"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('ambient startup')")
    env = {**os.environ, "HOME": str(f["home"]), "PYTHONHOME": str(poison),
           "PYTHONPATH": str(poison), "BROWSER": "/usr/bin/false",
           "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"}
    command = [str(runtime / "bin/python"), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
               str(source), "--runtime", str(f["state"]), "--port", "9344"]
    first = None
    try:
        for _ in range(2):
            launched = subprocess.run(["/bin/zsh", str(executable), "--native-smoke"],
                cwd=poison, env=env, capture_output=True, text=True, timeout=30)
            log = f["home"] / "Library/Logs/AI投递经理/launcher.log"
            actual = log.read_text()
            assert launched.returncode == 0, actual
            assert '/ui-login?' not in actual and 'ticket=' not in actual and 'Bearer ' not in actual
            # Each isolated launch logs only safe result booleans, never a capability.
            # Two launches reuse the same real supervisor writer and task authority.
            decoder = json.JSONDecoder()
            results, rest = [], actual
            while rest.strip():
                result, end = decoder.raw_decode(rest.lstrip())
                results.append(result)
                rest = rest.lstrip()[end:]
            assert all(r == {"ok": True, "opened": True, "native_window": True,
                "native_page": True, "final_click_actor": "user"} for r in results)
            service = json.loads((f["state"] / "service.json").read_text())
            first = service if first is None else first
            assert service == first
            health = subprocess.run(command + ["health"], cwd=poison, env=env,
                capture_output=True, text=True, timeout=10)
            assert health.returncode == 0
            observed = json.loads(health.stdout)
            assert observed["ok"] is True and observed["worker_active"] is None
            assert observed["final_click_actor"] == "user"
            assert TaskQueue(f["state"]).get(task["task_id"]) == before
        assert not list(source.rglob("__pycache__"))
    finally:
        stopped = subprocess.run(command + ["stop"], cwd=poison, env=env,
            capture_output=True, text=True, timeout=15)
        assert stopped.returncode == 0
    assert not (f["state"] / "service.json").exists()


def test_hosted_mac_native_compile_and_post_activation_fault_preserve_known_good_app(
    native_installed_app, monkeypatch
):
    from executor.autonomy import consumer
    from executor.autonomy.release import source_manifest

    f = native_installed_app
    source = f["app"] / "Contents/Resources/release"
    initial = source_manifest(source)
    program = f["app"] / "Contents/Resources/native-host/AIApplicationWindow"
    image = program.read_bytes()
    real_stage = consumer._stage_native_host
    monkeypatch.setattr(consumer, "_stage_native_host", lambda *a: False)
    refused = consumer.install_macos_app(f["repo"], destination=f["apps"],
        standalone_runtime=f["runtime"], task_state_root=f["state"], native_presentation=True)
    assert refused["reason"] == "native_candidate_failed" and refused["ok"] is False
    assert program.read_bytes() == image and source_manifest(source) == initial
    assert not (f["apps"] / ('.' + consumer.APP_NAME + '.app.previous')).exists()
    assert not (f["apps"] / ('.' + consumer.APP_NAME + '.app.installing')).exists()
    monkeypatch.setattr(consumer, "_stage_native_host", real_stage)
    real_health, calls = consumer._candidate_starts, []
    def fail_only_active(python, release):
        calls.append(str(release))
        return False if len(calls) == 2 else real_health(python, release)
    monkeypatch.setattr(consumer, "_candidate_starts", fail_only_active)
    failed = consumer.install_macos_app(f["repo"], destination=f["apps"],
        standalone_runtime=f["runtime"], task_state_root=f["state"], native_presentation=True)
    assert failed["reason"] == "post_activation_unhealthy" and failed["ok"] is False
    assert len(calls) == 3
    assert consumer._trusted_bundle(f["app"]) and consumer._isolated_bundle_startup(f["app"])
    assert program.read_bytes() == image and source_manifest(source) == initial
    assert (f["apps"] / ('.' + consumer.APP_NAME + '.app.failed')).is_dir()


def test_installer_cli_native_mode_is_explicit_and_default_options_stay_compatible(
    tmp_path, monkeypatch, capsys
):
    from executor.autonomy import cli, consumer

    calls = []
    runtime = tmp_path / "prepared-runtime"
    monkeypatch.setattr(consumer, "install_macos_app",
        lambda repo, **kwargs: calls.append((repo, kwargs)) or {"ok": True})
    assert cli.main(["install-app"]) == 0
    assert calls[-1][1] == {}
    assert cli.main(["install-app", "--native-presentation",
                     "--standalone-runtime", str(runtime)]) == 0
    assert calls[-1][1] == {"standalone_runtime": runtime, "native_presentation": True}
    assert len(calls) == 2
    assert '"ok": true' in capsys.readouterr().out.lower()


@pytest.mark.parametrize("failure", ["connection_refused", "unauthorized_page"])
def test_hosted_mac_native_failure_is_visible_without_retry_or_private_url(
    compiled_host, failure
):
    attempts = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            attempts.append(self.path)
            self.send_response(401)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
    class Loopback(ThreadingHTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name = "127.0.0.1"
            self.server_port = self.server_address[1]
    server, thread, reserved = None, None, None
    try:
        if failure == "unauthorized_page":
            server = Loopback(("127.0.0.1", 0), Handler)
            port = server.server_port
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
        else:
            # Reserve an actual loopback port without listening: deterministic
            # connection refusal, not an arbitrary guessed port or callback mock.
            reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        surface = ConsumerSurface.dashboard(port, "PRIVATE_FAILURE_TICKET")
        result = subprocess.run(
            [str(compiled_host / "AIApplicationWindow"), "--failure-smoke"],
            input=json.dumps(native_command(surface, 1)) + "\n",
            text=True, capture_output=True, timeout=25, check=False,
        )
        assert result.returncode == 0
        assert "PRIVATE_FAILURE_TICKET" not in result.stdout + result.stderr
        assert "127.0.0.1" not in result.stdout + result.stderr
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert replies == [
            {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1},
            {"ok": True, "native_error_visible": True, "window_count": 1,
             "no_automatic_retry": True},
        ]
        assert attempts == (
            ["/ui-login?ticket=PRIVATE_FAILURE_TICKET"]
            if failure == "unauthorized_page" else []
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
        if reserved is not None:
            reserved.close()
