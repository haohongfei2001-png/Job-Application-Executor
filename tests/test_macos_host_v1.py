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


def test_hosted_mac_actual_window_inherits_lease_and_blocks_second_launch(compiled_host, tmp_path):
    from executor.autonomy.state_compatibility import native_window_guard

    root = tmp_path / "owned-state"
    presenter = None
    try:
        with native_window_guard(root) as fd:
            presenter = NativePresenter(compiled_host, ownership_fd=fd)
            surface = ConsumerSurface.dashboard(43210, "PRIVATE_SINGLETON_CANARY")
            assert present_surface(surface, presenter=presenter)
            process = presenter.process
            assert process.poll() is None
            with pytest.raises(BlockingIOError):
                with native_window_guard(root):
                    pytest.fail("second launch admitted while actual NSWindow alive")
        # The Python owner descriptor is now closed; actual Cocoa process owns
        # the inherited descriptor. Do not force-unlock an orphaned live window.
        assert process.poll() is None
        with pytest.raises(BlockingIOError):
            with native_window_guard(root):
                pytest.fail("parent loss admitted a second native window")
        presenter.close()
        assert process.poll() is not None
        with native_window_guard(root):
            pass
        assert (root / "native-window.lock").read_bytes() == b""
    finally:
        if presenter is not None:
            presenter.close()


def test_repeated_exact_surface_focus_command_cannot_reload_or_reissue_ticket(tmp_path, monkeypatch):
    import io
    from executor.autonomy import macos_host as module

    root = _candidate(tmp_path)
    replies = [
        {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1},
        {"ok": True, "request": 2, "surface": "dashboard", "window_count": 1, "focus_only": True},
        {"ok": True, "request": 3, "surface": "dashboard", "window_count": 1},
        {"ok": True, "request": 4, "surface": "dashboard", "window_count": 1, "focus_only": True},
    ]
    class Child:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(json.dumps(reply) + "\n" for reply in replies))
            self.returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = 0
        def wait(self, **kwargs):
            return self.returncode
    child = Child()
    starts = []
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: starts.append((a, k)) or child)
    monkeypatch.setattr(module.select, "select", lambda readers, *a: (readers, [], []))
    presenter = NativePresenter(root)
    try:
        first = ConsumerSurface.dashboard(43210, "PRIVATE_FOCUS_TICKET")
        assert presenter(first)
        assert presenter(first)
        other = ConsumerSurface.dashboard(43210, "PRIVATE_NEW_EXPLICIT_TICKET")
        assert presenter(other)
        assert presenter.focus()
        commands = [json.loads(line) for line in child.stdin.getvalue().splitlines()]
        assert commands[0] == native_command(first, 1)
        assert commands[1] == {"command": "focus", "request": 2}
        assert commands[2] == native_command(other, 3)
        assert commands[3] == {"command": "focus", "request": 4}
        assert "PRIVATE_" not in json.dumps(commands[1])
        assert "PRIVATE_" not in json.dumps(commands[3])
        assert len(starts) == 1
        assert presenter.current_surface == other
        assert "PRIVATE_" not in repr(presenter)
    finally:
        presenter.close()
    assert presenter.current_surface is None


@pytest.mark.parametrize("bad_reply", [
    {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1, "focus_only": True},
    {"ok": True, "request": 2, "surface": "dashboard", "window_count": 1},
    {"ok": True, "request": True, "surface": "dashboard", "window_count": 1, "focus_only": True},
    {"ok": True, "request": 2, "surface": "dashboard", "window_count": True, "focus_only": True},
    {"ok": True, "request": 2, "surface": "dashboard", "window_count": 1, "focus_only": 1},
])
def test_focus_requires_exact_ack_and_never_falls_back_to_reloading(tmp_path, monkeypatch, bad_reply):
    import io
    from executor.autonomy import macos_host as module

    root = _candidate(tmp_path)
    first = {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1}
    class Child:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(json.dumps(first) + "\n" + json.dumps(bad_reply) + "\n")
            self.returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = 0
        def wait(self, **kwargs):
            return self.returncode
    child = Child()
    commands = []
    original_write = child.stdin.write
    child.stdin.write = lambda payload: commands.append(json.loads(payload)) or original_write(payload)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(module.select, "select", lambda readers, *a: (readers, [], []))
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("focus refusal opened browser"))
    presenter = NativePresenter(root)
    surface = ConsumerSurface.dashboard(43210, "PRIVATE_FOCUS_REFUSAL")
    assert presenter(surface)
    assert not presenter(surface)
    assert commands == [native_command(surface, 1), {"command": "focus", "request": 2}]
    assert child.returncode == 0
    assert presenter.process is None and presenter.current_surface is None


@pytest.mark.parametrize("long_runtime", [False, True])
def test_hosted_mac_focus_and_cocoa_reopen_preserve_actual_unsent_workbench(compiled_host, long_runtime):
    import queue
    import tempfile
    from pathlib import Path
    from executor.autonomy.native_reopen import create_owned_reopen_server
    from executor.autonomy.state_compatibility import native_window_guard

    state = {"admissions": 0, "pages": 0, "writes": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            state["writes"] += 1
            self.send_response(403)
            self.end_headers()
        def do_GET(self):
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            capability = cookies.get("focus_session")
            if self.path == "/ui-login?ticket=PRIVATE_FOCUS_CANARY" and state["admissions"] == 0:
                state["admissions"] += 1
                self.send_response(303)
                self.send_header("Location", "/ui")
                self.send_header("Set-Cookie", "focus_session=SYNTHETIC_FOCUS_COOKIE; HttpOnly; SameSite=Strict; Path=/ui")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            elif self.path == "/ui" and capability and capability.value == "SYNTHETIC_FOCUS_COOKIE":
                state["pages"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"""<!doctype html><body><label>Unsent note
                  <textarea id='native-draft'>PRIVATE_UNSENT_FOCUS_CANARY</textarea></label>
                  <script>const draft=document.getElementById('native-draft');
                    draft.focus();draft.setSelectionRange(3,9);</script></body>""")
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
    child = None
    reader = None
    messages = queue.Queue()
    stdout = []
    temporary = tempfile.TemporaryDirectory(prefix="jwr-", dir="/private/tmp")
    root = Path(temporary.name) / (("long-owned-segment-" * 7) if long_runtime else "state")
    owner = native_window_guard(root)
    ownership_fd = owner.__enter__()
    reopen = None
    try:
        child = subprocess.Popen([str(compiled_host / "AIApplicationWindow"), "--focus-smoke"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, pass_fds=(ownership_fd,))
        def read_messages():
            for line in child.stdout:
                stdout.append(line)
                messages.put(json.loads(line))
        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()
        def send(command):
            child.stdin.write(json.dumps(command) + "\n")
            child.stdin.flush()
        def reply():
            return messages.get(timeout=5)
        send({"command": "focus", "request": 1})
        assert reply() == {"ok": False, "reason": "host_focus_unavailable"}
        for command in [
            {"command": "focus", "request": True},
            {"command": "focus", "request": 1.0},
            {"command": "focus", "request": -1},
            {"command": "focus", "request": 1, "url": "PRIVATE_FORGED_ROUTE"},
        ]:
            send(command)
            assert reply() == {"ok": False, "reason": "host_input_invalid"}
        surface = ConsumerSurface.dashboard(server.server_port, "PRIVATE_FOCUS_CANARY")
        send(native_command(surface, 1))
        assert reply() == {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1}
        assert reply() == {"ok": True, "focus_fixture_ready": True, "window_count": 1}
        current_request = [1]
        original_pid = child.pid
        def owned_focus():
            current_request[0] += 1
            request = current_request[0]
            send({"command": "focus", "request": request})
            observed = reply()
            assert observed == {"ok": True, "request": request, "surface": "dashboard",
                                "window_count": 1, "focus_only": True}
            assert child.pid == original_pid
            return True
        reopen = create_owned_reopen_server(root, ownership_fd, owned_focus)
        assert reopen.start()
        child_source = """
import sys
sys.path.insert(0, sys.argv[1])
from executor.autonomy.native_reopen import request_owned_focus
print("FOCUSED" if request_owned_focus(sys.argv[2]) else "REFUSED")
"""
        for request in (2, 3, 4):
            secondary = subprocess.run([sys.executable, "-I", "-B", "-c", child_source,
                str(Path(__file__).resolve().parents[1]), str(root)],
                capture_output=True, text=True, timeout=8, check=False)
            assert secondary.returncode == 0 and secondary.stdout == "FOCUSED\n"
            assert secondary.stderr == "" and current_request[0] == request
            with pytest.raises(BlockingIOError):
                with native_window_guard(root):
                    pytest.fail("reopen admitted another native writer")
        reopen.close()
        assert reply() == {"ok": True, "focus_only": True, "window_count": 1,
                           "draft_and_selection_retained": True, "reopen_count": 3}
        assert child.wait(timeout=5) == 0
        reader.join(timeout=3)
        assert state == {"admissions": 1, "pages": 1, "writes": 0}
        assert "PRIVATE_" not in "".join(stdout)
        assert "127.0.0.1" not in "".join(stdout)
    finally:
        if reopen is not None:
            reopen.close()
        if child is not None:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            if reader is not None:
                reader.join(timeout=3)
            child.stdin.close()
            child.stdout.close()
        owner.__exit__(None, None, None)
        assert not (root / "native-focus.sock").exists()
        assert not (root / "native-focus.loopback.json").exists()
        temporary.cleanup()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

def test_cold_focus_never_constructs_surface_or_starts_native_child(tmp_path, monkeypatch):
    from executor.autonomy import macos_host as module
    root = _candidate(tmp_path)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "Popen",
        lambda *a, **k: pytest.fail("cold focus started a child"))
    presenter = NativePresenter(root)
    assert presenter.focus() is False
    assert presenter.process is None and presenter.current_surface is None
    assert presenter.sequence == 0
    presenter.close()

def test_child_exit_during_focus_verification_never_replays_surface_or_starts_child(tmp_path, monkeypatch):
    import io
    from executor.autonomy import macos_host as module
    root = _candidate(tmp_path)
    class Child:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(json.dumps(
                {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1}) + "\n")
            self.polls = 0
        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0
        def wait(self, **kwargs):
            return 0
    child, starts = Child(), []
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.subprocess, "Popen",
        lambda *a, **k: starts.append(True) or child)
    monkeypatch.setattr(module.select, "select", lambda readers, *a: (readers, [], []))
    presenter = NativePresenter(root)
    surface = ConsumerSurface.dashboard(43210, "PRIVATE_EXIT_RACE_TICKET")
    assert presenter(surface)
    assert presenter.focus() is False
    assert starts == [True]
    assert [json.loads(line) for line in child.stdin.getvalue().splitlines()] == [native_command(surface, 1)]
    presenter.close()

def _prebuilt_release(tmp_path):
    from pathlib import Path
    from executor.autonomy.release import copy_source_candidate

    source = tmp_path / "owned-release"
    copy_source_candidate(Path(__file__).resolve().parents[1], source)
    return source


@pytest.mark.parametrize("defect", [
    "digest", "source", "receipt_alias", "image_alias", "hardlink",
    "fifo", "extra", "target_exists", "target_parent_alias",
])
def test_prebuilt_window_refuses_unsafe_delivery_without_execution_or_authority_write(
    tmp_path, monkeypatch, defect
):
    import os
    from executor.autonomy import consumer

    release = _prebuilt_release(tmp_path)
    root = _candidate(tmp_path)
    program = root / "AIApplicationWindow"
    receipt = root / HOST_RECEIPT
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "private-canary"
    canary.write_bytes(b"PRIVATE_PREBUILT_CANARY")
    target = tmp_path / "staged-window"
    if defect == "digest":
        program.write_bytes(b"CHANGED_BINARY")
    elif defect == "source":
        data = json.loads(receipt.read_text())
        data["source_sha256"] = "0" * 64
        receipt.write_text(json.dumps(data))
    elif defect == "receipt_alias":
        saved = outside / "receipt"
        receipt.rename(saved)
        receipt.symlink_to(saved)
    elif defect == "image_alias":
        program.unlink()
        program.symlink_to(canary)
    elif defect == "hardlink":
        os.link(program, outside / "linked-window")
    elif defect == "fifo":
        program.unlink()
        os.mkfifo(program)
    elif defect == "extra":
        (root / "unknown").write_bytes(b"PRIVATE_UNKNOWN")
    elif defect == "target_exists":
        target.mkdir()
        (target / "retained").write_bytes(b"RETAINED_TARGET")
    else:
        alias = tmp_path / "target-alias"
        alias.symlink_to(outside, target_is_directory=True)
        target = alias / "staged-window"
    def authority():
        return [(p.relative_to(tmp_path).as_posix(), p.lstat().st_mode,
                 p.readlink().as_posix() if p.is_symlink() else
                 (p.read_bytes() if p.is_file() else None))
                for p in sorted(tmp_path.rglob("*"))]
    before = authority()
    monkeypatch.setattr(consumer.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("prebuilt refusal executed a compiler/image"))
    assert consumer._copy_native_host_candidate(release, root, target) is False
    assert authority() == before
    assert canary.read_bytes() == b"PRIVATE_PREBUILT_CANARY"


def test_prebuilt_window_copies_exact_source_bound_image_without_compiler(tmp_path, monkeypatch):
    from executor.autonomy import consumer

    release = _prebuilt_release(tmp_path)
    root = _candidate(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    target = tmp_path / "staged-window"
    monkeypatch.setattr(consumer.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("staging cannot execute compiler/image"))
    assert consumer._copy_native_host_candidate(release, root, target) is True
    assert {p.name: p.read_bytes() for p in target.iterdir()} == before
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert consumer._native_bundle_matches(release, target)


@pytest.mark.parametrize("change", ["inode", "bytes"])
def test_prebuilt_window_refuses_source_change_during_copy_and_cleans_only_fresh_stage(
    tmp_path, monkeypatch, change
):
    from executor.autonomy import consumer

    release = _prebuilt_release(tmp_path)
    root = _candidate(tmp_path)
    target = tmp_path / "staged-window"
    retained = tmp_path / "retained"
    retained.write_bytes(b"PRIVATE_RETAINED")
    real_copy = consumer.shutil.copyfileobj
    calls = []
    def changed_copy(source, output, *args, **kwargs):
        real_copy(source, output, *args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            program = root / "AIApplicationWindow"
            if change == "inode":
                program.rename(tmp_path / "original-image")
                program.write_bytes(b"SYNTHETIC_EXECUTABLE")
                program.chmod(0o755)
            else:
                program.write_bytes(b"CHANGED_BINARY")
    monkeypatch.setattr(consumer.shutil, "copyfileobj", changed_copy)
    assert consumer._copy_native_host_candidate(release, root, target) is False
    assert not target.exists()
    assert retained.read_bytes() == b"PRIVATE_RETAINED"
    assert len(calls) == 1
    assert (root / "AIApplicationWindow").exists()


@pytest.mark.parametrize("options", [
    {"native_presentation": False, "standalone_runtime": "unused"},
    {"native_presentation": True},
])
def test_prebuilt_install_requires_native_standalone_mode_before_any_staging(
    tmp_path, monkeypatch, options
):
    from executor.autonomy import consumer

    monkeypatch.setattr(consumer, "copy_source_candidate",
        lambda *_args: pytest.fail("invalid prebuilt mode staged source"))
    result = consumer._install_macos_app_unlocked(tmp_path / "missing-source",
        destination=tmp_path / "Applications", platform="darwin",
        native_host=tmp_path / "missing-window", **options)
    assert result["ok"] is False and result["reason"] == "native_prebuilt_mode_invalid"
    assert not (tmp_path / "Applications").exists()


def test_delivered_bundle_cli_keeps_explicit_and_default_task_authorities(tmp_path, monkeypatch, capsys):
    from executor.autonomy import cli, consumer

    candidate, apps, state = tmp_path / "delivery.app", tmp_path / "Applications", tmp_path / "state"
    calls = []
    monkeypatch.setattr(consumer, "install_macos_bundle",
        lambda app, **kwargs: calls.append((app, kwargs)) or {"ok": True})
    assert cli.main(["install-bundle", "--candidate", str(candidate),
                     "--destination", str(apps)]) == 0
    assert calls == [(candidate, {"destination": apps, "task_state_root": None})]
    assert cli.main(["--runtime", str(state), "install-bundle",
                     "--candidate", str(candidate)]) == 0
    assert calls[-1] == (candidate, {"destination": None, "task_state_root": state})
    assert "ticket=" not in capsys.readouterr().out


def test_delivered_bundle_refuses_unverified_or_aliased_image_without_transaction(tmp_path, monkeypatch):
    from executor.autonomy import consumer

    root = tmp_path / "delivery.app"
    root.mkdir()
    alias = tmp_path / "delivery-alias.app"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(consumer, "install_macos_app",
        lambda *_args, **_kwargs: pytest.fail("unverified bundle entered transaction"))
    for candidate in (root, alias):
        result = consumer.install_macos_bundle(candidate, destination=tmp_path / "Applications",
                                              platform="darwin")
        assert result == {"ok": False, "reason": "bundle_candidate_invalid",
                         "message": "交付应用无法核对；现有应用和任务保持不变。"}
    assert list(root.iterdir()) == [] and alias.is_symlink()


def test_hosted_mac_prebuilt_delivery_updates_rolls_back_and_reopens_without_compiler(
    native_installed_app, tmp_path, monkeypatch
):
    from executor.autonomy import consumer
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.release import copy_source_candidate, source_manifest
    from pathlib import Path

    f = native_installed_app
    candidate = f["app"]
    candidate_source = candidate / "Contents/Resources/release"
    image = candidate / "Contents/Resources/native-host"
    image_before = {p.name: p.read_bytes() for p in image.iterdir()}
    apps = tmp_path / "Consumer Applications"
    state = tmp_path / "existing-task-state"
    queue = TaskQueue(state)
    before = []
    for i in range(3):
        task = queue.enqueue(TaskSpec(company="Synthetic delivery " + str(i), role="Engineer",
            target_url="https://example.invalid/jobs/delivery-" + str(i),
            profile_ref="synthetic-profile-" + str(i) + ".json"))
        before.append(queue.pause(task["task_id"]))
    from cryptography.fernet import Fernet
    private_key = Fernet.generate_key()
    (state / "task-answers.key").write_bytes(private_key)
    (state / "task-answers.key").chmod(0o600)
    # A previously used consumer journal includes the real worker's auth and
    # encrypted-answer schema. Freeze complete bytes only after that necessary
    # initialization; never suppress those production migrations at launch.
    from executor.autonomy.worker import Worker
    existing_worker = Worker(queue, settings={})
    assert existing_worker.active is None
    with queue.tx() as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"auth_attempts", "task_answer_events"}.issubset(tables)
        assert db.execute("SELECT count(*) FROM auth_attempts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM task_answer_events").fetchone()[0] == 0
    def tasks():
        return [queue.get(task["task_id"]) for task in before]
    def private_bytes():
        names = ("tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm", "task-answers.key")
        return {name: (state / name).read_bytes() if (state / name).exists() else None for name in names}
    actual_bytes = private_bytes()
    monkeypatch.setattr(consumer, "_stage_native_host",
        lambda *_args: pytest.fail("delivered consumer update required Xcode"))
    # Deliver the SAME complete compiled app/runtime through the real archive
    # and receipt entry. No fixture shrink or second build/compile is needed.
    from executor.autonomy.app_distribution import (
        ARCHIVE_NAME, RECEIPT_NAME, _archive_app, install_macos_distribution,
    )
    delivery = tmp_path / "Complete Delivered Distribution"
    delivery.mkdir()
    archive_path = delivery / ARCHIVE_NAME
    _archive_app(candidate, archive_path)
    with archive_path.open("rb") as file:
        archive_sha = hashlib.file_digest(file, "sha256").hexdigest()
    runtime_identity = json.loads(
        (candidate / "Contents/Resources/runtime/release-runtime-manifest.json").read_text())
    receipt = {
        "format": "jae-macos-distribution-v1", "archive": ARCHIVE_NAME,
        "archive_sha256": archive_sha, "app_name": consumer.APP_NAME + ".app",
        "source_sha256": source_manifest(candidate_source)["source_sha256"],
        "runtime_sha256": runtime_identity["runtime_sha256"],
        "requirements_sha256": runtime_identity["requirements_sha256"],
        "signing": "unsigned", "certification": "NOT_CERTIFIED",
        "final_click_actor": "user", "task_state": "excluded",
        "build_host_metadata": "excluded", "presentation": "native",
    }
    (delivery / RECEIPT_NAME).write_text(json.dumps(receipt), encoding="utf-8")
    archive_before = archive_path.stat()
    installed = install_macos_distribution(delivery, destination=apps, task_state_root=state)
    assert installed["ok"] is True and installed["presentation"] == "native"
    assert json.loads((delivery / RECEIPT_NAME).read_text()) == receipt
    with archive_path.open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == archive_sha
    archive_after = archive_path.stat()
    assert (archive_after.st_ino, archive_after.st_size, archive_after.st_mtime_ns) == (
        archive_before.st_ino, archive_before.st_size, archive_before.st_mtime_ns)
    assert sorted(path.name for path in delivery.iterdir()) == [ARCHIVE_NAME, RECEIPT_NAME]
    current = apps / (consumer.APP_NAME + ".app")
    current_source = current / "Contents/Resources/release"
    current_host = current / "Contents/Resources/native-host"
    initial = source_manifest(current_source)
    assert {p.name: p.read_bytes() for p in current_host.iterdir()} == image_before
    assert consumer._trusted_bundle(current) and tasks() == before
    assert private_bytes() == actual_bytes
    active = consumer.install_macos_bundle(current, destination=apps, task_state_root=state)
    assert active["reason"] == "bundle_candidate_is_active"
    # Run the real installed entry in its owned isolated Python. This fixture
    # deliberately uses an explicit synthetic authority, not the user's default:
    # the new installed updater must refuse substitution before archive intake.
    from executor.autonomy.process_entry import CLI_ENTRY_SCRIPT
    import os
    owned_update_python = current / "Contents/Resources/runtime/bin/python"
    refusal = subprocess.run([
        str(owned_update_python), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
        str(current_source), "--runtime", str(state), "native-launch",
        "--update-distribution", str(delivery)],
        cwd=tmp_path, env={**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated",
                          "BROWSER": "/usr/bin/false"},
        capture_output=True, text=True, timeout=30)
    assert refusal.returncode == 1
    assert json.loads(refusal.stdout) == {
        "ok": False, "updated": False, "reason": "installed_update_state_unverified",
        "final_click_actor": "user", "submit_capability": False}
    assert private_key.decode() not in refusal.stdout + refusal.stderr
    assert str(state) not in refusal.stdout and str(delivery) not in refusal.stdout
    assert source_manifest(current_source) == initial
    assert consumer._trusted_bundle(current) and tasks() == before
    assert private_bytes() == actual_bytes
    assert not (apps / ("." + consumer.APP_NAME + ".app.previous")).exists()
    # A genuinely different complete source version retains the exact same
    # compiled HOST_SOURCE contract, so no compiler is needed for this update.
    replacement = tmp_path / "replacement-source"
    copy_source_candidate(candidate_source, replacement)
    initializer = replacement / "executor/__init__.py"
    initializer.write_text(initializer.read_text() + "\n# prebuilt second source version\n")
    updated = consumer.install_macos_app(replacement, destination=apps, task_state_root=state,
        standalone_runtime=candidate / "Contents/Resources/runtime",
        native_presentation=True, native_host=image)
    assert updated["ok"] is True and updated["replaced"] is True
    assert source_manifest(current_source) != initial
    assert consumer._trusted_bundle(current) and tasks() == before
    assert private_bytes() == actual_bytes
    restored = consumer.rollback_macos_app(apps, task_state_root=state)
    assert restored["ok"] is True and restored["restored"] is True
    assert source_manifest(current_source) == initial
    assert {p.name: p.read_bytes() for p in current_host.iterdir()} == image_before
    assert consumer._trusted_bundle(current) and tasks() == before
    assert private_bytes() == actual_bytes
    assert {p.name: p.read_bytes() for p in image.iterdir()} == image_before
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    assert consumer._trusted_bundle(failed)

    from executor.autonomy.process_entry import CLI_ENTRY_SCRIPT
    import os
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    owned_python = current / "Contents/Resources/runtime/bin/python"
    command = [str(owned_python), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
               str(current_source), "--runtime", str(state), "--port", str(port)]
    env = {**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated",
           "BROWSER": "/usr/bin/false"}
    try:
        launched = subprocess.run(command + ["native-launch", "--native-smoke"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
        assert launched.returncode == 0, launched.stdout
        assert json.loads(launched.stdout) == {"ok": True, "opened": True,
            "native_window": True, "native_page": True, "final_click_actor": "user"}
        assert private_key.decode() not in launched.stdout + launched.stderr
        assert "ticket=" not in launched.stdout + launched.stderr
        assert "Bearer " not in launched.stdout + launched.stderr
        health = subprocess.run(command + ["health"], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=10)
        assert health.returncode == 0 and json.loads(health.stdout)["worker_active"] is None
        assert tasks() == before and private_bytes() == actual_bytes
    finally:
        stopped = subprocess.run(command + ["stop"], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=15)
        assert stopped.returncode == 0
    assert not (state / "service.json").exists()

@pytest.mark.parametrize("already_initialized", [False, True])
def test_real_consumer_journal_initialization_keeps_task_authority_and_private_bytes(
    tmp_path, already_initialized
):
    """Cold schema creation is necessary; reopening a complete journal is inert."""
    import sqlite3
    from cryptography.fernet import Fernet
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.worker import Worker

    state = tmp_path / "existing-consumer-journal"
    queue = TaskQueue(state)
    tasks = []
    for index in range(3):
        task = queue.enqueue(TaskSpec(
            company="Synthetic journal " + str(index), role="Engineer",
            target_url="https://example.invalid/jobs/journal-" + str(index),
            profile_ref="synthetic-journal-profile-" + str(index) + ".json"))
        tasks.append(queue.pause(task["task_id"]))
    key_path = state / "task-answers.key"
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    key_path.chmod(0o600)

    def snapshot():
        with sqlite3.connect(queue.path.as_uri() + "?mode=ro", uri=True) as db:
            schema = list(db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
            names = [row[1] for row in schema if row[0] == "table"]
            # Every table, including operational receipts/events and encrypted
            # facts, participates. Identifiers originate in sqlite_master only.
            rows = {name: list(db.execute('SELECT * FROM "' +
                    name.replace('"', '""') + '" ORDER BY rowid')) for name in names}
        return schema, rows

    def private_bytes():
        return {name: (state / name).read_bytes() if (state / name).exists() else None
                for name in ("tasks.sqlite3", "tasks.sqlite3-wal",
                             "tasks.sqlite3-shm", "task-answers.key")}

    initial_schema, initial_rows = snapshot()
    if already_initialized:
        initialized = Worker(queue, settings={})
        assert initialized.active is None
        initial_schema, initial_rows = snapshot()
    original = private_bytes()
    reopened = TaskQueue(state)
    worker = Worker(reopened, settings={})
    schema, rows = snapshot()
    assert worker.active is None and worker.stop_event.is_set() is False
    assert [reopened.get(task["task_id"]) for task in tasks] == tasks
    assert all(rows[name] == values for name, values in initial_rows.items())
    assert {"auth_attempts", "task_answer_events"}.issubset(rows)
    assert rows["auth_attempts"] == [] and rows["task_answer_events"] == []
    assert key_path.read_bytes() == key and key_path.stat().st_mode & 0o777 == 0o600
    if already_initialized:
        assert schema == initial_schema
        assert private_bytes() == original
    else:
        # Check the exact new tables/indexes, retaining every old definition.
        added = {(kind, name) for kind, name, _table, _sql in schema} - {
            (kind, name) for kind, name, _table, _sql in initial_schema}
        assert added == {
            ("table", "auth_attempts"), ("index", "sqlite_autoindex_auth_attempts_1"),
            ("index", "one_current_auth_attempt"), ("index", "unique_resend_command"),
            ("table", "task_answer_events"), ("index", "task_answer_latest")}
        assert all(entry in schema for entry in initial_schema)
    complete = private_bytes()
    repeated = Worker(TaskQueue(state), settings={})
    assert repeated.active is None
    assert snapshot() == (schema, rows)
    assert private_bytes() == complete
@pytest.mark.parametrize("alias_kind", ["leaf", "ancestor"])
@pytest.mark.parametrize("operation", ["install", "rollback", "delivered", "lock"])
def test_app_transaction_refuses_directory_alias_before_private_state_or_bundle_work(
    tmp_path, monkeypatch, alias_kind, operation
):
    """Routing refusal, not a substitute for the actual native delivery gate."""
    from executor.autonomy import consumer, state_compatibility
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.worker import Worker
    from cryptography.fernet import Fernet

    state = tmp_path / "private-task-authority"
    queue = TaskQueue(state)
    tasks = []
    for index in range(3):
        task = queue.enqueue(TaskSpec(
            company="Synthetic alias refusal " + str(index), role="Engineer",
            target_url="https://example.invalid/jobs/alias-" + str(index),
            profile_ref="synthetic-alias-profile-" + str(index) + ".json"))
        tasks.append(queue.pause(task["task_id"]))
    key = state / "task-answers.key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    assert Worker(queue, settings={}).active is None

    owned = tmp_path / "owned-applications"
    owned.mkdir()
    for name in (consumer.APP_NAME + ".app", "." + consumer.APP_NAME + ".app.previous",
                 "." + consumer.APP_NAME + ".app.failed"):
        bundle = owned / name
        bundle.mkdir()
        (bundle / "identity").write_bytes(("PRESERVE " + name).encode())
    alias = tmp_path / "application-alias"
    alias.symlink_to(owned, target_is_directory=True)
    destination = alias if alias_kind == "leaf" else alias / "missing-parent" / "Applications"

    candidate = tmp_path / "delivery.app"
    launcher = candidate / "Contents/MacOS/AIApplicationManager"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(consumer._native_packaged_launcher(), encoding="utf-8")
    # These two finite admission stubs reach the delivered-entry destination
    # check only. Actual source/runtime/native verification remains exercised
    # by the unchanged full hosted-Mac delivery/update/rollback/reopen journey.
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda _path: True)
    monkeypatch.setattr(consumer, "verify_standalone_runtime", lambda *_args: True)

    def tree_bytes(root):
        return {str(path.relative_to(root)):
                None if path.is_dir() else path.read_bytes()
                for path in root.rglob("*")}

    before_tasks = [queue.get(task["task_id"]) for task in tasks]
    private_before = tree_bytes(state)
    bundles_before = tree_bytes(owned)
    candidate_before = tree_bytes(candidate)

    def forbidden(*_args, **_kwargs):
        pytest.fail("aliased application authority entered locks, private state or staging")

    monkeypatch.setattr(state_compatibility, "_private_lock_fd", forbidden)
    monkeypatch.setattr(state_compatibility, "task_state_guard", forbidden)
    monkeypatch.setattr(consumer, "_install_macos_app_unlocked", forbidden)
    monkeypatch.setattr(consumer, "_rollback_macos_app_unlocked", forbidden)
    monkeypatch.setattr(consumer, "_candidate_starts", forbidden)
    monkeypatch.setattr(consumer, "_prepare_task_state_release", forbidden)
    if operation == "lock":
        with pytest.raises(ValueError, match="^app_transaction_path_invalid$"):
            consumer._acquire_app_transaction_lock(destination)
    elif operation == "rollback":
        result = consumer.rollback_macos_app(destination, task_state_root=state)
        assert result == {"ok": False, "reason": "update_lock_unavailable",
                         "message": "无法安全锁定应用目录；没有修改当前应用。"}
    elif operation == "install":
        result = consumer.install_macos_app(tmp_path / "missing-source",
            destination=destination, task_state_root=state, platform="darwin")
        assert result == {"ok": False, "reason": "update_lock_unavailable",
                         "message": "无法安全锁定应用目录；没有修改当前应用。"}
    else:
        monkeypatch.setattr(consumer, "install_macos_app", forbidden)
        result = consumer.install_macos_bundle(candidate, destination=destination,
            task_state_root=state, platform="darwin")
        assert result == {"ok": False, "reason": "bundle_candidate_invalid",
                         "message": "交付应用无法核对；现有应用和任务保持不变。"}
    assert tree_bytes(state) == private_before
    assert tree_bytes(owned) == bundles_before
    assert tree_bytes(candidate) == candidate_before
    assert alias.is_symlink() and alias.readlink() == owned
    assert not (owned / "missing-parent").exists()
    assert [queue.get(task["task_id"]) for task in tasks] == before_tasks
    assert tree_bytes(state) == private_before


def test_app_transaction_directory_admission_is_inert_for_ordinary_missing_path(tmp_path):
    from executor.autonomy import consumer

    destination = tmp_path / "ordinary-missing-parent" / "Applications"
    assert consumer._app_transaction_directory(destination) == destination.resolve()
    assert not destination.parent.exists()


@pytest.mark.parametrize("value", [
    None, [], {}, {"action": True}, {"action": "restore", "distribution": "/private"},
    {"action": "stop"}, {"action": "update"}, {"action": "update", "distribution": None},
    {"action": "update", "distribution": "relative"},
    {"action": "update", "distribution": "/a\nPRIVATE_HOST_PATH"},
    {"action": "update", "distribution": "/" + "a" * 2048},
    {"action": "update", "distribution": "/ok", "destination": "/other"},
    {"action": "update", "distribution": "/ok", "task_state_root": "/other"},
])
def test_native_release_terminal_intent_is_finite_and_never_a_task_or_destination(value):
    from executor.autonomy.macos_host import native_release_request
    assert native_release_request(value) is None


@pytest.mark.parametrize("intent", [
    {"action": "restore"}, {"action": "update", "distribution": "/Synthetic Delivery"},
])
def test_presenter_yields_one_release_intent_only_after_actual_child_exit(tmp_path, intent):
    import io
    presenter = NativePresenter(tmp_path)
    class Child:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(json.dumps({"ok": True, "release_request": intent}) + "\n")
            self.returncode = None
            self.closed = False
        def poll(self):
            return self.returncode
        def wait(self, **kwargs):
            self.returncode = 0
            self.closed = True
            return 0
        def terminate(self):
            pytest.fail("normal terminal intent must not kill its owner")
    child = Child()
    presenter.process = child
    assert presenter.take_release_request() is None
    assert presenter.wait_for_close()
    assert child.closed and child.stdin.closed and child.stdout.closed
    assert presenter.process is None
    assert presenter.take_release_request() == intent
    assert presenter.take_release_request() is None
    assert "Synthetic Delivery" not in repr(presenter)


@pytest.mark.parametrize("raw,code", [
    ('{"ok":true,"release_request":{"action":"restore"}}\n', 1),
    ('{"ok":1,"release_request":{"action":"restore"}}\n', 0),
    ('{"ok":true,"release_request":{"action":"restore","task":"private"}}\n', 0),
    ('{"ok":true,"release_request":{"action":"restore"}}\n' * 2, 0),
    ("PRIVATE_BAD_REPLY" * 1000, 0),
])
def test_presenter_never_admits_failed_ambiguous_or_oversized_terminal_stream(tmp_path, raw, code):
    import io
    presenter = NativePresenter(tmp_path)
    class Child:
        stdin = io.StringIO()
        stdout = io.StringIO(raw)
        def poll(self):
            return code
        def wait(self, **kwargs):
            return code
    presenter.process = Child()
    presenter.wait_for_close()
    assert presenter.take_release_request() is None
    assert presenter.process is None


def test_terminal_release_during_focus_is_retained_without_becoming_focus_ack(tmp_path, monkeypatch):
    import io
    from executor.autonomy import macos_host as module
    root = _candidate(tmp_path)
    surface = ConsumerSurface.dashboard(43210, "PRIVATE_FOCUS_RELEASE")
    event = {"ok": True, "release_request": {"action": "restore"}}
    class Child:
        stdin = io.StringIO()
        stdout = io.StringIO(json.dumps(event) + "\n")
        returncode = None
        def poll(self):
            return self.returncode
        def wait(self, **kwargs):
            self.returncode = 0
            return 0
        def terminate(self):
            pytest.fail("focus cannot kill a terminal release owner")
    presenter = NativePresenter(root)
    presenter.process = Child()
    presenter.current_surface = surface
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.select, "select", lambda readers, *args: (readers, [], []))
    assert not presenter.focus()
    assert presenter.take_release_request() is None
    assert presenter.wait_for_close()
    assert presenter.take_release_request() == {"action": "restore"}


def test_hosted_mac_actual_menu_handoff_exits_one_window_without_service_or_task_action(compiled_host):
    from executor.autonomy.loopback_http import LoopbackHTTPServer
    state = {"pages": 0, "writes": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            state["pages"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body>SYNTHETIC_RELEASE_MENU</body></html>")
        def do_POST(self):
            state["writes"] += 1
            self.send_response(403)
            self.end_headers()
    server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        surface = ConsumerSurface.dashboard(server.server_port, "PRIVATE_RELEASE_MENU_TICKET")
        result = subprocess.run([str(compiled_host / "AIApplicationWindow"), "--release-smoke"],
            input=json.dumps(native_command(surface, 1)) + "\n",
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25)
        assert result.returncode == 0
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        # Cocoa termination may close the same window again. The wire oracle
        # must stay exact: one admission and one terminal intent, no filtering.
        assert replies == [
            {"ok": True, "request": 1, "surface": "dashboard", "window_count": 1},
            {"ok": True, "release_request": {"action": "restore"}},
        ]
        assert state == {"pages": 1, "writes": 0}
        assert "PRIVATE_" not in result.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_hosted_mac_two_version_foreground_handoff_uses_actual_runtime_window_service_and_current_journal(
    native_installed_app, tmp_path, monkeypatch
):
    """Whole installed handoff boundary; native picker/device certification stays separate."""
    import os
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from executor.autonomy import consumer
    from executor.autonomy.app_distribution import ARCHIVE_NAME, RECEIPT_NAME, _archive_app
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.release import copy_source_candidate, source_manifest
    from executor.autonomy.state_compatibility import _snapshot, verify_task_state_backup
    from executor.autonomy.worker import Worker
    from test_task_state_compatibility_v1 import encrypted_answers

    f = native_installed_app
    current, state = f["app"], f["state"]
    source = current / "Contents/Resources/release"
    original = source_manifest(source)["source_sha256"]
    queue = TaskQueue(state)
    tasks = []
    for index in range(3):
        task = queue.enqueue(TaskSpec(company="Synthetic foreground " + str(index),
            role="Synthetic role", target_url="https://jobs.example.test/foreground/" + str(index),
            profile_ref=str(state / "profile.json"), live_authorized=False))
        tasks.append(queue.pause(task["task_id"]))
    with closing(sqlite3.connect(state / "tasks.sqlite3")) as db:
        db.execute("PRAGMA journal_mode=WAL")
        key = encrypted_answers(state, db)
        db.execute("UPDATE task_answer_events SET task_id=? WHERE task_id='synthetic-task'",
                   (tasks[0]["task_id"],))
        db.commit()
    assert Worker(queue, settings={}).active is None
    view = queue.remember_task_view(tasks[1]["task_id"], expected_revision=0)
    for name, value in {
        "profile.json": {"complete": "\n".join(
            f"{i}: PRIVATE_FOREGROUND_PROFILE_中文_<literal>" for i in range(1000))},
        "preferences.json": {"complete": [
            f"{i}: PRIVATE_FOREGROUND_PREF_中文_<literal>" for i in range(1000)]},
    }.items():
        path = state / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
    def authority():
        with closing(sqlite3.connect(state / "tasks.sqlite3")) as db:
            db.execute("BEGIN")
            return _snapshot(db)
    def private_files():
        return {name: ((state / name).read_bytes(), (state / name).stat().st_mode,
                       (state / name).stat().st_dev, (state / name).stat().st_ino)
                for name in ("profile.json", "preferences.json", "task-answers.key")}
    before, files_before = authority(), private_files()
    assert files_before["task-answers.key"][0] == key
    assert len(queue.tasks()) == 3 and queue.task_view_context() == view

    # A complete genuine second version, using the already compiled exact
    # native contract and standalone runtime. No Xcode or ambient checkout at
    # the installed user's update boundary.
    replacement = tmp_path / "second-version-source"
    copy_source_candidate(source, replacement)
    initializer = replacement / "executor/__init__.py"
    initializer.write_text(initializer.read_text() + "\n# complete foreground second source version\n")
    monkeypatch.setattr(consumer, "_stage_native_host",
        lambda *_args: pytest.fail("prebuilt foreground transaction compiled a host"))
    second_apps = tmp_path / "second-version-apps"
    built = consumer.install_macos_app(replacement, destination=second_apps,
        standalone_runtime=current / "Contents/Resources/runtime",
        native_presentation=True, native_host=current / "Contents/Resources/native-host",
        task_state_root=tmp_path / "second-build-empty-state")
    assert built["ok"] is True
    second_app = second_apps / (consumer.APP_NAME + ".app")
    expected = source_manifest(second_app / "Contents/Resources/release")["source_sha256"]
    assert expected != original
    delivery = tmp_path / "Complete Foreground Delivery"
    delivery.mkdir()
    archive = delivery / ARCHIVE_NAME
    _archive_app(second_app, archive)
    with archive.open("rb") as handle:
        archive_sha = hashlib.file_digest(handle, "sha256").hexdigest()
    runtime_receipt = json.loads(
        (second_app / "Contents/Resources/runtime/release-runtime-manifest.json").read_text())
    receipt = {
        "format": "jae-macos-distribution-v1", "archive": ARCHIVE_NAME,
        "archive_sha256": archive_sha, "app_name": consumer.APP_NAME + ".app",
        "source_sha256": expected, "runtime_sha256": runtime_receipt["runtime_sha256"],
        "requirements_sha256": runtime_receipt["requirements_sha256"],
        "signing": "unsigned", "certification": "NOT_CERTIFIED",
        "final_click_actor": "user", "task_state": "excluded",
        "build_host_metadata": "excluded", "presentation": "native",
    }
    (delivery / RECEIPT_NAME).write_text(json.dumps(receipt), encoding="utf-8")
    delivery_before = archive.stat()
    # Remove the assembly checkouts. Both foreground phases execute the actual
    # installed source with its own -I Python; neither can use a repository venv.
    f["repo"].rename(tmp_path / "first-checkout-removed")
    replacement.rename(tmp_path / "second-checkout-removed")
    poison = tmp_path / "ambient"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('PRIVATE_AMBIENT_STARTUP')")
    env = {**os.environ, "HOME": str(f["home"]), "PYTHONHOME": str(poison),
           "PYTHONPATH": str(poison), "BROWSER": "/usr/bin/false",
           "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"}
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]

    # The explicitly selected synthetic release intent starts after a complete
    # real WebKit dashboard presentation closes and releases its actual kernel
    # lease. This does not automate or certify the human NSOpenPanel selection.
    # Reopen Popen is observed and passed through unchanged, not mocked.
    script = r"""
import json,os,signal,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import cli
from executor.autonomy.macos_host import NativePresenter
from executor.autonomy.native_reopen import request_owned_focus
from executor.autonomy.state_compatibility import native_window_guard
root,port,intent,expected=Path(sys.argv[2]),int(sys.argv[3]),json.loads(sys.argv[4]),sys.argv[5]
source=Path(sys.argv[1])
before=cli._consumer_release_identity()
assert before["packaged"] and before["expected"]
with native_window_guard(root) as fd:
    presenter=NativePresenter(source.parent/"native-host",consumer_smoke=True,ownership_fd=fd)
    try:
        opened=cli.launch_consumer(root,port,presenter=presenter)
        assert opened["ok"] is True and opened["opened"] is True
        old=cli._service_record(root/"service.json")
        old_health=cli.request(root,port,"/health")
        assert old_health["ok"] is True and old_health["loaded_source_sha256"]==before["expected"]
        assert presenter.wait_for_close() is True
    finally:
        presenter.close()
with native_window_guard(root):
    pass
real_subprocess=cli.subprocess
children=[]
class ObservedSubprocess:
    def __getattr__(self,name):
        return getattr(real_subprocess,name)
    def Popen(self,args,**kwargs):
        child=real_subprocess.Popen(args,**kwargs)
        if args[-1]=="native-launch":
            assert args[0]==str(source.parent/"runtime/bin/python")
            assert args[1:3]==["-I","-B"] and kwargs["start_new_session"] is True
            assert kwargs["stdin"]==kwargs["stdout"]==kwargs["stderr"]==subprocess.DEVNULL
            children.append(child)
        return child
cli.subprocess=ObservedSubprocess()
try:
    result=cli.handoff_installed_consumer(root,port,intent)
    completed="updated" if intent["action"]=="update" else "restored"
    assert result=={"ok":True,completed:True,"reopen_requested":True,
                   "final_click_actor":"user","submit_capability":False}
    assert len(children)==1 and children[0].poll() is None
    deadline=time.monotonic()+25
    observed=None
    while time.monotonic()<deadline:
        assert children[0].poll() is None
        try:
            observed=cli.request(root,port,"/health")
            if (observed.get("ok") is True and observed.get("loaded_source_sha256")==expected
                    and request_owned_focus(root)):
                break
        except (OSError,ValueError):
            pass
        time.sleep(.1)
    else:
        raise AssertionError("actual activated service/window did not become ready")
    fresh=cli._service_record(root/"service.json")
    assert fresh["instance"]!=old["instance"] and fresh["pid"]!=old["pid"]
    assert observed["worker_active"] is None and observed["final_click_actor"]=="user"
    with_exception=False
    try:
        with native_window_guard(root):
            raise AssertionError("second actual window admitted")
    except BlockingIOError:
        with_exception=True
    assert with_exception
    print(json.dumps({"handoff":result,"new_service_identity":True,
                     "actual_focus_ack":True,"actual_release_sha":expected,
                     "single_window_lease":True}))
finally:
    cli.subprocess=real_subprocess
    for child in children:
        # Only this test's newly created, proven separate process group. The
        # user has no device/process involved; hosted synthetic UI teardown.
        if child.poll() is None:
            assert os.getpgid(child.pid)==child.pid
            os.killpg(child.pid,signal.SIGTERM)
        child.wait(timeout=10)
    stopped=cli._stop_owned_service(root,port)
    assert stopped.get("ok") is True or stopped.get("reason")=="service_record_missing"
    deadline=time.monotonic()+10
    while True:
        try:
            with native_window_guard(root):
                break
        except BlockingIOError:
            if time.monotonic()>deadline:
                raise AssertionError("actual window lease remained after hosted teardown")
            time.sleep(.1)
"""
    def phase(intent, expected_sha):
        result = subprocess.run([str(current / "Contents/Resources/runtime/bin/python"),
            "-I", "-B", "-c", script, str(source), str(state), str(port),
            json.dumps(intent), expected_sha], cwd=poison, env=env,
            capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        observed = json.loads(result.stdout)
        assert observed == {"handoff": {"ok": True,
            "updated" if intent["action"] == "update" else "restored": True,
            "reopen_requested": True, "final_click_actor": "user", "submit_capability": False},
            "new_service_identity": True, "actual_focus_ack": True,
            "actual_release_sha": expected_sha, "single_window_lease": True}
        assert "PRIVATE_" not in result.stdout + result.stderr
        assert "ticket=" not in result.stdout + result.stderr
        assert "Bearer " not in result.stdout + result.stderr
        assert str(state) not in result.stdout
        assert not (state / "service.json").exists()
        assert consumer._trusted_bundle(current)
        assert source_manifest(source)["source_sha256"] == expected_sha
        assert private_files() == files_before
        return observed

    phase({"action": "update", "distribution": str(delivery)}, expected)
    assert authority() == before
    assert [queue.get(task["task_id"]) for task in tasks] == tasks
    assert queue.task_view_context() == view
    # Saved view and typed encrypted history changed AFTER update are current
    # authority. Returning the app version must never activate an older backup.
    queue.remember_task_view(tasks[2]["task_id"], expected_revision=view["revision"])
    from cryptography.fernet import Fernet
    with closing(sqlite3.connect(state / "tasks.sqlite3")) as db:
        db.execute("""INSERT INTO task_answer_events
            (task_id,field_key,ciphertext,answer_version,source,task_revision,created)
            VALUES(?,?,?,?,?,?,?)""", (tasks[0]["task_id"], "family.primary.role",
            Fernet(key).encrypt(json.dumps("PRIVATE_CURRENT_AFTER_UPDATE").encode()),
            3, "user_explicit_task", 1, 3))
        db.commit()
    current_authority, current_view = authority(), queue.task_view_context()
    assert current_authority != before
    phase({"action": "restore"}, original)
    assert authority() == current_authority
    assert queue.task_view_context() == current_view
    assert [queue.get(task["task_id"]) for task in tasks] == tasks
    failed = f["apps"] / ("." + consumer.APP_NAME + ".app.failed")
    assert consumer._trusted_bundle(failed)
    assert source_manifest(failed / "Contents/Resources/release")["source_sha256"] == expected
    capsules = list(f["apps"].glob(".jae-task-state-backup-*"))
    assert len(capsules) == 2
    for capsule in capsules:
        manifest = json.loads((capsule / "backup-manifest.json").read_text())
        assert manifest["activation"] == "NOT_AUTHORIZED"
        assert verify_task_state_backup(capsule, manifest)
    assert json.loads((delivery / RECEIPT_NAME).read_text()) == receipt
    with archive.open("rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == archive_sha
    delivery_after = archive.stat()
    assert (delivery_after.st_ino, delivery_after.st_size, delivery_after.st_mtime_ns) == (
        delivery_before.st_ino, delivery_before.st_size, delivery_before.st_mtime_ns)
    assert not list(source.rglob("__pycache__"))


@pytest.mark.parametrize("value", [None, True, 1, "", "PRIVATE_REASON", {}, [],
                                    "https://example.invalid", {"result": "not-confirmed"}])
def test_native_release_result_refuses_nonfinite_input_without_launch(tmp_path, monkeypatch, value):
    from executor.autonomy import macos_host
    monkeypatch.setattr(macos_host.sys, "platform", "darwin")
    monkeypatch.setattr(macos_host.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("nonfinite result launched a child"))
    assert macos_host.present_native_release_result(tmp_path, value) is False


def test_native_release_result_refuses_unverified_or_aliased_host_without_fallback(tmp_path, monkeypatch):
    from executor.autonomy import macos_host
    monkeypatch.setattr(macos_host.sys, "platform", "darwin")
    monkeypatch.setattr(macos_host.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("unverified result host executed"))
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("browser fallback"))
    assert macos_host.present_native_release_result(tmp_path / "missing", "not-confirmed") is False
    root = _candidate(tmp_path)
    alias = tmp_path / "result-alias"
    alias.symlink_to(root, target_is_directory=True)
    assert macos_host.present_native_release_result(alias, "not-confirmed") is False
    (root / "AIApplicationWindow").write_bytes(b"PRIVATE_CHANGED_RESULT_BINARY")
    assert macos_host.present_native_release_result(root, "reopen-failed") is False


@pytest.mark.parametrize("kind", ["not-confirmed", "reopen-failed"])
def test_hosted_mac_actual_release_result_is_visible_accessible_without_web_or_task_surface(compiled_host, kind):
    from executor.autonomy.macos_host import present_native_release_result
    # Actual compiled Cocoa, no localhost server/ticket/browser/task writer.
    assert present_native_release_result(compiled_host, kind, smoke=True) is True


def test_hosted_mac_installed_release_result_uses_owned_runtime_lease_and_keeps_full_private_authority(
    native_installed_app, tmp_path
):
    import os
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.state_compatibility import _snapshot
    from test_task_state_compatibility_v1 import encrypted_answers

    f = native_installed_app
    queue = TaskQueue(f["state"])
    task = queue.pause(queue.enqueue(TaskSpec(company="Synthetic result",
        role="Synthetic", target_url="https://example.invalid/result",
        profile_ref="synthetic-result-profile.json"))["task_id"])
    with closing(sqlite3.connect(f["state"] / "tasks.sqlite3")) as db:
        key = encrypted_answers(f["state"], db)
        db.commit()
        before = _snapshot(db)
    private = f["state"] / "profile.json"
    private.write_text("\n".join(f"{i}: PRIVATE_RESULT_中文_<literal>" for i in range(1000)), encoding="utf-8")
    private.chmod(0o600)
    files_before = {name: ((f["state"] / name).read_bytes(),
                           (f["state"] / name).stat().st_mode,
                           (f["state"] / name).stat().st_dev,
                           (f["state"] / name).stat().st_ino)
                    for name in ("profile.json", "task-answers.key")}
    assert files_before["task-answers.key"][0] == key
    f["repo"].rename(tmp_path / "result-checkout-removed")
    source = f["app"] / "Contents/Resources/release"
    runtime = f["app"] / "Contents/Resources/runtime"
    poison = tmp_path / "result-ambient"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('PRIVATE_RESULT_AMBIENT')")
    env = {**os.environ, "HOME": str(f["home"]), "PYTHONHOME": str(poison),
           "PYTHONPATH": str(poison), "BROWSER": "/usr/bin/false"}
    script = r"""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from executor.autonomy import cli
root=Path(sys.argv[2])
def forbidden(*args,**kwargs):
    raise AssertionError("result cannot start/stop/retry a service or task")
cli.launch_consumer=cli.lifecycle=cli.request=cli._stop_owned_service=forbidden
outcomes=[{"ok":False,"reason":"PRIVATE_RESULT_PATH"},
          {"ok":True,"updated":True,"reopen_requested":False}]
for outcome in outcomes:
    assert cli.show_native_release_result(root,outcome,smoke=True) is True
assert not (root/"service.json").exists()
print(json.dumps({"release_results_visible":2,"no_service_started":True}))
"""
    result = subprocess.run([str(runtime / "bin/python"), "-I", "-B", "-c", script,
        str(source), str(f["state"])], cwd=poison, env=env, text=True,
        capture_output=True, timeout=55)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"release_results_visible": 2, "no_service_started": True}
    assert "PRIVATE_" not in result.stdout + result.stderr
    assert "ticket=" not in result.stdout + result.stderr and "Bearer " not in result.stdout + result.stderr
    assert str(f["state"]) not in result.stdout and not (f["state"] / "service.json").exists()
    with closing(sqlite3.connect(f["state"] / "tasks.sqlite3")) as db:
        assert _snapshot(db) == before
    assert queue.get(task["task_id"]) == task
    for name, previous in files_before.items():
        path = f["state"] / name
        assert (path.read_bytes(), path.stat().st_mode, path.stat().st_dev, path.stat().st_ino) == previous
    assert not list(source.rglob("__pycache__"))


@pytest.mark.parametrize("reason", ["worker_active", "otp_in_flight", "service_identity_changed",
                                    "rollback_state_incompatible", "native_release_unconfirmed"])
def test_foreground_release_refusal_yields_fixed_result_under_real_new_window_lease(
    tmp_path, monkeypatch, reason
):
    from types import SimpleNamespace
    from executor.autonomy import cli, macos_host, native_reopen, state_compatibility
    from test_consumer_entry_v1 import _native_handoff_entry

    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    before = canary.read_bytes()
    actual_guard = state_compatibility.native_window_guard
    calls = []
    class Presenter:
        def __init__(self, directory, **kwargs):
            assert directory == source.parent / "native-host"
            assert type(kwargs["ownership_fd"]) is int
        def focus(self):
            pytest.fail("failure cannot focus/replay the dashboard")
        def wait_for_close(self):
            return True
        def take_release_request(self):
            return {"action": "restore"}
        def close(self):
            pass
    class Reopen:
        def start(self):
            pass
        def close(self):
            pass
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    monkeypatch.setattr(native_reopen, "create_owned_reopen_server", lambda *a: Reopen())
    monkeypatch.setattr(cli, "launch_consumer", lambda *a, **k: {"ok": True, "opened": True})
    def handoff(authority, port, intent):
        assert authority == root and port == 9344 and intent == {"action": "restore"}
        with actual_guard(root):
            pass
        calls.append("one_handoff")
        return {"ok": False, "reason": reason, "final_click_actor": "user", "submit_capability": False}
    monkeypatch.setattr(cli, "handoff_installed_consumer", handoff)
    def visible(directory, kind, *, ownership_fd, smoke):
        assert directory == source.parent / "native-host" and kind == "not-confirmed"
        assert type(ownership_fd) is int and smoke is False
        with pytest.raises(BlockingIOError):
            with actual_guard(root):
                pytest.fail("result window did not own the actual kernel lease")
        calls.append("one_fixed_result")
        return True
    monkeypatch.setattr(macos_host, "present_native_release_result", visible)
    monkeypatch.setattr(cli, "subprocess",
        SimpleNamespace(Popen=lambda *a, **k: pytest.fail("failure reopened an app"), DEVNULL=-3))
    monkeypatch.setattr(cli, "_stop_owned_service", lambda *a: pytest.fail("result repeated service stop"))
    result = cli.launch_native_consumer(root, 9344)
    assert calls == ["one_handoff", "one_fixed_result"]
    assert result == {"ok": False, "reason": reason, "final_click_actor": "user",
                      "submit_capability": False, "release_result_visible": True}
    with actual_guard(root):
        pass
    assert canary.read_bytes() == before and not (root / "tasks.sqlite3").exists()
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("fault", ["foreign_root", "image_changed", "alias", "lease_busy", "already_reopened"])
def test_release_result_refuses_unadmitted_or_contended_authority_without_any_effect(
    tmp_path, monkeypatch, fault
):
    from executor.autonomy import cli, macos_host, state_compatibility
    from test_consumer_entry_v1 import _native_handoff_entry

    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    before = canary.read_bytes()
    supplied = root
    outcome = {"ok": False, "reason": "PRIVATE_RESULT_CANARY"}
    if fault == "foreign_root":
        supplied = tmp_path / "foreign"
    elif fault == "image_changed":
        executable.write_text("unverified result launcher")
    elif fault == "alias":
        supplied = tmp_path / "authority-alias"
        supplied.symlink_to(root, target_is_directory=True)
    elif fault == "already_reopened":
        outcome = {"ok": True, "restored": True, "reopen_requested": True}
    monkeypatch.setattr(macos_host, "present_native_release_result",
                        lambda *a, **k: pytest.fail("unadmitted result was presented"))
    if fault == "lease_busy":
        with state_compatibility.native_window_guard(root):
            assert cli.show_native_release_result(supplied, outcome) is False
    else:
        assert cli.show_native_release_result(supplied, outcome) is False
    assert canary.read_bytes() == before and not (root / "tasks.sqlite3").exists()
