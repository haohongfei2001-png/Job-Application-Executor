from __future__ import annotations

import json
import fcntl
import os
from importlib.metadata import version
import plistlib
import shlex
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from executor.autonomy import bootstrap, cli, consumer, preflight, release
from executor.autonomy.consumer import (_candidate_starts, install_macos_app,
                                        rollback_macos_app)
from executor.autonomy.loopback_http import LoopbackHTTPServer
from executor.autonomy.release import verify_runtime_candidate, verify_source_candidate
from executor.autonomy.dashboard import DASHBOARD_HTML



def test_local_http_bind_does_not_resolve_hostname(monkeypatch):
    def forbidden_resolution(*_args, **_kwargs):
        raise AssertionError("loopback bind must not resolve a hostname")

    monkeypatch.setattr(socket, "getfqdn", forbidden_resolution)
    with LoopbackHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1]
        assert server.server_port > 0


@pytest.fixture(autouse=True)
def isolated_packaged_task_state(tmp_path, monkeypatch):
    # Consumer app transactions must never share the hosted runner's HOME.
    # Keep the production packaged path calculation, with a synthetic home.
    from executor.autonomy import queue, runtime_paths

    actual = runtime_paths.default_runtime
    isolated = lambda source, *, home=None: actual(
        source, home=home if home is not None else tmp_path / "synthetic-home")
    # Both the legacy re-export and cold transaction import use this same
    # synthetic authority. No test may fall back to the hosted runner HOME.
    monkeypatch.setattr(queue, "default_runtime", isolated)
    monkeypatch.setattr(runtime_paths, "default_runtime", isolated)


FAKE_CANDIDATE_CLI = """from __future__ import annotations
VERSION = 'fixture'

import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from executor.autonomy.loopback_http import LoopbackHTTPServer
from executor.autonomy.release import source_manifest

def serve():
    runtime = Path(sys.argv[sys.argv.index('--runtime') + 1])
    port = int(sys.argv[sys.argv.index('--port') + 1])
    runtime.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    token_file = runtime / 'auth.token'
    token_file.write_text(token, encoding='utf-8')
    token_file.chmod(0o600)
    digest = source_manifest(Path(__file__).resolve().parents[2])['source_sha256']

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/health' or self.headers.get('Authorization') != 'Bearer ' + token:
                self.send_error(404)
                return
            body = json.dumps({'ok': True, 'loaded_source_sha256': digest,
                               'final_click_actor': 'user'}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with LoopbackHTTPServer(('127.0.0.1', port), Handler) as server:
        server.serve_forever()

if __name__ == '__main__' and sys.argv[-1] == 'serve':
    serve()
"""


def _minimal_source(repo):
    package = repo / "executor"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "consumer_entry.py").write_text("VERSION = 'fixture'\n", encoding="utf-8")
    autonomy = package / "autonomy"
    autonomy.mkdir()
    (autonomy / "__init__.py").write_text("", encoding="utf-8")
    (autonomy / "cli.py").write_text(FAKE_CANDIDATE_CLI, encoding="utf-8")
    (autonomy / "release.py").write_text(
        (Path(__file__).resolve().parents[1] / "executor" / "autonomy" / "release.py")
        .read_text(encoding="utf-8"), encoding="utf-8"
    )
    (autonomy / "loopback_http.py").write_text(
        (Path(__file__).resolve().parents[1] / "executor" / "autonomy" / "loopback_http.py")
        .read_text(encoding="utf-8"), encoding="utf-8"
    )
    # The candidate exercises the release lock, including transitive pins.
    # A lone direct package is intentionally not a valid packaged release.
    (repo / "requirements.txt").write_text(
        (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(),
        encoding="utf-8",
    )


def _ready_preflight(*, supervisor_running):
    assert supervisor_running is True
    return {
        "ok": True,
        "ready_for_live_e2e": True,
        "checks": {
            "live_browser_mode": True,
            "chrome_installed": True,
            "existing_cdp_session": True,
            "profile_configured": True,
            "profile_exists": True,
            "profile_loadable": True,
            "deepseek_available": True,
            "supervisor_running": True,
        },
        "remediation": [],
        "final_click_actor": "user",
        "submit_capability": False,
    }


def test_consumer_launch_repairs_reversible_runtime_and_opens_ui(
    tmp_path, monkeypatch
):
    calls = []
    opened = []

    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    monkeypatch.setattr(cli, "ensure_chrome", lambda: calls.append("chrome"))

    def fake_lifecycle(action, root, port):
        calls.append(action)
        return {"ok": True}

    monkeypatch.setattr(cli, "lifecycle", fake_lifecycle)
    monkeypatch.setattr(preflight, "collect_live_preflight", _ready_preflight)
    monkeypatch.setattr(
        cli,
        "open_ui",
        lambda root, port: opened.append((root, port)) or {"ok": True, "opened": True},
    )

    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert result["ok"] is True
    assert result["ready_for_live_e2e"] is True
    assert result["opened"] is True
    assert result["message"] == "已就绪"
    assert result["submit_capability"] is False
    assert calls == ["chrome", "start", "health"]
    assert opened == [(tmp_path / "runtime", 9344)]


def test_packaged_launch_restarts_stale_daemon_before_opening_ui(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(release, "read_release_identity", lambda _: {
        "status": "verified", "source_sha256": "new-release"
    })
    def lifecycle(action, root, port):
        calls.append(action)
        if action == "health":
            return {"ok": True, "loaded_source_sha256":
                    "new-release" if "restart" in calls else "old-release"}
        return {"ok": True}
    monkeypatch.setattr(cli, "lifecycle", lifecycle)
    monkeypatch.setattr(preflight, "collect_live_preflight", _ready_preflight)
    monkeypatch.setattr(cli, "open_ui", lambda *_: {"ok": True, "opened": True})

    result = cli.launch_consumer(tmp_path / "runtime", 9344)
    assert result["ok"] is True
    assert result["opened"] is True
    assert calls == ["start", "health", "restart", "health"]


def test_packaged_launch_keeps_stale_daemon_out_of_consumer_ui(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(release, "read_release_identity", lambda _: {
        "status": "verified", "source_sha256": "new-release"
    })
    def lifecycle(action, root, port):
        calls.append(action)
        return {"ok": action != "restart", "loaded_source_sha256": "old-release"}
    monkeypatch.setattr(cli, "lifecycle", lifecycle)
    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **kwargs: {
        "ready_for_live_e2e": False, "remediation": [], "submit_capability": False
    })
    monkeypatch.setattr(bootstrap, "open_bootstrap", lambda *args: {
        "ok": True, "opened": True
    })
    monkeypatch.setattr(cli, "open_ui", lambda *_: pytest.fail("stale UI must not open"))

    result = cli.launch_consumer(tmp_path / "runtime", 9344)
    assert result["bootstrap_reason"] == "release_mismatch"
    assert result["submit_capability"] is False
    assert calls == ["start", "health", "restart", "health"]


def test_packaged_launch_refuses_unverified_source_before_service_start(tmp_path, monkeypatch):
    source = tmp_path / "release"
    module = source / "executor" / "autonomy" / "cli.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture\\n")
    (source / "release-source-manifest.json").write_bytes(bytes((0xff, 0xfe)))
    monkeypatch.setattr(cli, "__file__", str(module))
    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    monkeypatch.setattr(cli, "ensure_chrome", lambda: pytest.fail("unverified app must not start Chrome"))
    monkeypatch.setattr(release, "read_release_identity", lambda _: {
        "status": "unverified", "source_sha256": ""
    })
    monkeypatch.setattr(cli, "lifecycle", lambda *_: pytest.fail("unverified app must not start"))
    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **kwargs: {
        "ready_for_live_e2e": False, "remediation": [], "submit_capability": False
    })
    monkeypatch.setattr(bootstrap, "open_bootstrap", lambda *args: {
        "ok": True, "opened": True
    })
    monkeypatch.setattr(cli, "open_ui", lambda *_: pytest.fail("unverified UI must not open"))

    result = cli.launch_consumer(tmp_path / "runtime", 9344)
    assert result["bootstrap_reason"] == "release_unverified"
    assert result["submit_capability"] is False


def test_packaged_launch_refuses_removed_manifest_before_service_start(tmp_path, monkeypatch):
    source = tmp_path / "AI 投递经理.app" / "Contents" / "Resources" / "release"
    module = source / "executor" / "autonomy" / "cli.py"
    module.parent.mkdir(parents=True)
    module.write_text("# fixture\n", encoding="utf-8")
    assert release.is_packaged_source(source)
    monkeypatch.setattr(cli, "__file__", str(module))
    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    monkeypatch.setattr(cli, "ensure_chrome", lambda: pytest.fail("unverified app must not start Chrome"))
    monkeypatch.setattr(cli, "lifecycle", lambda *_: pytest.fail("unverified app must not start"))
    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **kwargs: {
        "ready_for_live_e2e": False, "remediation": [], "submit_capability": False
    })
    monkeypatch.setattr(bootstrap, "open_bootstrap", lambda *args: {
        "ok": True, "opened": True
    })
    monkeypatch.setattr(cli, "open_ui", lambda *_: pytest.fail("unverified UI must not open"))

    result = cli.launch_consumer(tmp_path / "runtime", 9344)
    assert result["bootstrap_reason"] == "release_unverified"
    assert result["submit_capability"] is False


def test_consumer_launch_uses_final_healthy_service_after_start_timeout(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    monkeypatch.setattr(cli, "ensure_chrome", lambda: None)

    def fake_lifecycle(action, root, port):
        calls.append(action)
        return {"ok": action == "health", "reason": "health_timeout" if action == "start" else None}

    monkeypatch.setattr(cli, "lifecycle", fake_lifecycle)
    monkeypatch.setattr(preflight, "collect_live_preflight", _ready_preflight)
    monkeypatch.setattr(cli, "open_ui", lambda *args: {"ok": True, "opened": True})

    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert calls == ["start", "health"]
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["ready_for_live_e2e"] is True
    assert result["message"] == "已就绪"


def test_consumer_launch_opens_bootstrap_when_model_is_not_ready(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    monkeypatch.setattr(cli, "ensure_chrome", lambda: None)
    monkeypatch.setattr(cli, "lifecycle", lambda *args: {"ok": True})
    monkeypatch.setattr(
        preflight,
        "collect_live_preflight",
        lambda *, supervisor_running: {
            "ok": False,
            "ready_for_live_e2e": False,
            "checks": {"deepseek_available": False},
            "remediation": ["configure_deepseek_key"],
            "final_click_actor": "user",
            "submit_capability": False,
        },
    )

    opened = []
    monkeypatch.setattr(cli, "open_ui", lambda *args: opened.append(True) or {"ok": True, "opened": True})
    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert result["ok"] is True
    assert result["ready_for_live_e2e"] is False
    assert result["opened"] is True
    assert opened == [True]
    assert result["submit_capability"] is False
    assert "DeepSeek" in result["message"]


def test_consumer_launch_does_not_start_live_browser_in_isolated_mode(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")

    def forbidden():
        raise AssertionError("isolated/test launch must not start live Chrome")

    monkeypatch.setattr(cli, "ensure_chrome", forbidden)
    monkeypatch.setattr(cli, "lifecycle", lambda action, root, port: {"ok": True})
    monkeypatch.setattr(cli, "open_ui", lambda *args: {"ok": True, "opened": True})
    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert result["ok"] is True
    assert result["opened"] is True
    assert result["ready_for_live_e2e"] is False
    assert result["submit_capability"] is False
    assert "use_live_browser_mode" in result["remediation"]


def test_consumer_launch_reports_service_failure_without_opening_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(cli, "lifecycle", lambda action, root, port: {"ok": False, "reason": "service_start_failed"})
    monkeypatch.setattr(cli, "open_ui", lambda *args: (_ for _ in ()).throw(AssertionError("no service")))
    monkeypatch.setattr(bootstrap, "open_bootstrap", lambda *args: {"ok": True, "opened": True})

    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert result["opened"] is True
    assert result["ok"] is True
    assert result["bootstrap_reason"] == "service_start_failed"
    assert result["ready_for_live_e2e"] is False
    assert result["submit_capability"] is False


def test_independent_bootstrap_recovers_isolated_supervisor(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        service_port = reservation.getsockname()[1]
    poison = tmp_path / "inherited-python"
    poison.mkdir()
    canary = tmp_path / "inherited-import-executed"
    (poison / "sitecustomize.py").write_text(
        f"from pathlib import Path;Path({str(canary)!r}).write_text('unsafe')\n"
    )
    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "missing-python-home"))
    monkeypatch.setenv("PYTHONPATH", str(poison))
    opened = []
    monkeypatch.setattr(bootstrap.webbrowser, "open",
                        lambda value, **kwargs: opened.append(value) or True)
    children = []
    real_popen = subprocess.Popen
    def record_child(command, **kwargs):
        child = real_popen(command, **kwargs)
        if "-I" in command and "-c" in command and any(
                "executor.autonomy.cli" in str(part) for part in command):
            children.append((command, child))
        return child
    monkeypatch.setattr(subprocess, "Popen", record_child)
    process = None
    try:
        assert bootstrap.open_bootstrap(runtime, service_port, "service_start_failed")["opened"]
        assert len(children) == 1
        process = children[0][1]
        state_path = runtime / "bootstrap.json"
        for _ in range(600):
            if state_path.exists():
                break
            assert process.poll() is None
            time.sleep(0.05)
        record = json.loads(state_path.read_text())
        assert record["pid"] == process.pid
        assert state_path.stat().st_mode & 0o077 == 0
        base = f"http://127.0.0.1:{record['port']}"
        url = base + "/?token=" + record["token"]
        with urllib.request.urlopen(url) as response:
            body = response.read().decode()
        assert "重试并打开面板" in body
        assert "提交申请" in body
        assert "本地服务启动失败" in body
        assert "本地版本：" in body
        assert bootstrap.open_bootstrap(runtime, service_port)["opened"] is True
        assert opened == [url, url]
        assert len(children) == 1  # Reuse the owned recovery page, no second writer.
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base + "/")
        assert denied.value.code == 403
        without_origin = urllib.request.Request(
            base + "/retry?token=" + record["token"], data=b"", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(without_origin)
        assert denied.value.code == 403
        retry = urllib.request.Request(
            base + "/retry?token=" + record["token"], data=b"",
            headers={"Origin": base}, method="POST",
        )
        no_redirect = urllib.request.build_opener(
            type("NoRedirect", (urllib.request.HTTPRedirectHandler,),
                 {"redirect_request": lambda self, *args: None})()
        )
        with pytest.raises(urllib.error.HTTPError) as redirected:
            no_redirect.open(retry, timeout=30)
        assert redirected.value.code == 303
        assert redirected.value.headers["Location"].startswith(
            f"http://127.0.0.1:{service_port}/ui-login?ticket="
        )
        assert cli.lifecycle("health", runtime, service_port)["ok"] is True
        # The supervisor is a child of the independent recovery process, so
        # this parent's Popen recorder cannot see it. Read its real PID/argv.
        assert len(children) == 1
        assert children[0][0][1:3] == ["-I", "-B"]
        service = json.loads((runtime / "service.json").read_text())
        assert service["pid"] != process.pid
        assert service["port"] == service_port
        command = subprocess.run(
            ["ps", "-ww", "-p", str(service["pid"]), "-o", "command="],
            capture_output=True, text=True, check=True,
        ).stdout
        assert shlex.split(command)[1:4] == ["-I", "-B", "-c"]
        assert "executor.autonomy.cli" in command
        assert str(runtime.resolve()) in command
        assert str(Path(cli.__file__).resolve().parents[2]) in command
        assert not canary.exists()
    finally:
        cli.lifecycle("stop", runtime, service_port)
        if process is not None:
            process.terminate()
            process.wait(timeout=5)
        for _, child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


def test_macos_app_install_and_rollback_refuse_concurrent_transaction(tmp_path):
    apps = tmp_path / "Applications"
    lock_fd = consumer._acquire_app_transaction_lock(apps)
    try:
        install = install_macos_app(tmp_path / "missing-source", destination=apps, platform="darwin")
        rollback = rollback_macos_app(apps)
        assert install["reason"] == "update_in_progress"
        assert rollback["reason"] == "update_in_progress"
        assert not (apps / "AI 投递经理.app").exists()
        assert not (apps / ".AI 投递经理.app.installing").exists()
    finally:
        os.close(lock_fd)

    assert install_macos_app(tmp_path / "missing-source", destination=apps, platform="darwin")["reason"] == "venv_missing"
    assert rollback_macos_app(apps)["reason"] == "rollback_unavailable"


def test_macos_app_transaction_lock_rejects_symlink(tmp_path):
    apps = tmp_path / "Applications"
    apps.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("untouched", encoding="utf-8")
    (apps / ".AI 投递经理.app.transaction.lock").symlink_to(outside)
    assert install_macos_app(tmp_path / "missing-source", destination=apps, platform="darwin")["reason"] == "update_lock_unavailable"
    assert rollback_macos_app(apps)["reason"] == "update_lock_unavailable"
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_macos_consumer_app_installs_idempotently(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"

    first = install_macos_app(repo, destination=apps, platform="darwin")
    app = apps / "AI 投递经理.app"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    info_path = app / "Contents" / "Info.plist"

    assert first["ok"] is True
    assert first["replaced"] is False
    assert app.is_dir()
    release = app / "Contents" / "Resources" / "release"
    assert verify_source_candidate(release)
    assert (release / "executor" / "consumer_entry.py").read_text() == "VERSION = 'fixture'\n"
    assert executable.stat().st_mode & stat.S_IXUSR
    launcher = executable.read_text(encoding="utf-8")
    assert str(repo.resolve()) not in launcher
    assert 'RUNTIME_ROOT="$(cd "$(dirname "$0")/../Resources/runtime" && pwd -P)"' in launcher
    assert 'PYTHON="$RUNTIME_ROOT/bin/python"' in launcher
    assert 'cd "$RELEASE_ROOT"' in launcher
    runtime = app / "Contents" / "Resources" / "runtime"
    assert verify_runtime_candidate(runtime, release)
    assert "export PYTHONPATH" not in launcher
    assert "export PYTHONDONTWRITEBYTECODE" not in launcher
    assert '"$PYTHON" -I -B -c ' in launcher
    assert '"$RELEASE_ROOT" launch' in launcher
    assert "submit" not in launcher.casefold()

    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleDisplayName"] == "AI 投递经理"
    assert info["CFBundlePackageType"] == "APPL"

    rogue = app / "Contents" / "old-file.txt"
    rogue.write_text("old")
    (repo / "executor" / "consumer_entry.py").write_text(
        "VERSION = 'candidate'\n", encoding="utf-8"
    )
    assert (release / "executor" / "consumer_entry.py").read_text() == "VERSION = 'fixture'\n"
    second = install_macos_app(repo, destination=apps, platform="darwin")
    assert second["ok"] is True
    assert second["replaced"] is True
    assert not rogue.exists()
    rollback = apps / ".AI 投递经理.app.previous"
    assert second["rollback_path"] == str(rollback)
    assert (rollback / "Contents" / "old-file.txt").read_text() == "old"
    assert (rollback / "Contents" / "Resources" / "release" / "executor" / "consumer_entry.py").read_text() == "VERSION = 'fixture'\n"
    assert (app / "Contents" / "Resources" / "release" / "executor" / "consumer_entry.py").read_text() == "VERSION = 'candidate'\n"
    third = install_macos_app(repo, destination=apps, platform="darwin")
    assert third["ok"] is False
    assert third["reason"] == "rollback_pending"
    assert app.is_dir()
    assert rollback.is_dir()
    restored = rollback_macos_app(apps)
    assert restored["ok"] is True
    assert restored["restored"] is True
    assert rogue.read_text() == "old"
    assert (app / "Contents" / "Resources" / "release" / "executor" / "consumer_entry.py").read_text() == "VERSION = 'fixture'\n"
    assert (apps / ".AI 投递经理.app.failed").is_dir()
    assert not rollback.exists()



def test_packaged_launcher_executes_owned_source_despite_poisoned_python_environment(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    candidate_cli = repo / "executor" / "autonomy" / "cli.py"
    candidate_cli.write_text(FAKE_CANDIDATE_CLI + """
if __name__ == '__main__' and sys.argv[-1] == 'launch':
    import os
    Path(os.environ['JAE_TEST_STARTUP_REPORT']).write_text(json.dumps({
        'source': str(Path(__file__).resolve().parents[2]),
        'argv': sys.argv[1:], 'isolated': sys.flags.isolated,
        'no_user_site': sys.flags.no_user_site,
        'dont_write_bytecode': sys.dont_write_bytecode,
        'path': sys.path,
    }), encoding='utf-8')
""", encoding="utf-8")
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    source = app / "Contents" / "Resources" / "release"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    repo.rename(tmp_path / "development-checkout-removed")
    poison = tmp_path / "ambient"
    poison.mkdir()
    canary = tmp_path / "ambient-code-executed"
    code = f"from pathlib import Path;Path({str(canary)!r}).write_text('unsafe');raise RuntimeError('ambient import')\n"
    (poison / "sitecustomize.py").write_text(code)
    (poison / "executor").mkdir()
    (poison / "executor" / "__init__.py").write_text(code)
    report = tmp_path / "startup.json"
    home = tmp_path / "launcher-home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "PYTHONHOME": str(tmp_path / "missing-base"),
           "PYTHONPATH": str(poison), "PYTHONUSERBASE": str(poison),
           "JAE_TEST_STARTUP_REPORT": str(report)}
    shell = "/bin/zsh" if sys.platform == "darwin" else "/bin/bash"
    launched = subprocess.run([shell, str(executable)], cwd=poison, env=env,
                              capture_output=True, text=True, timeout=15)
    log = home / "Library" / "Logs" / "AI投递经理" / "launcher.log"
    assert launched.returncode == 0, log.read_text() if log.exists() else launched.stderr
    actual = json.loads(report.read_text())
    assert actual["source"] == str(source.resolve())
    assert actual["argv"] == ["launch"]
    assert actual["isolated"] == 1 and actual["no_user_site"] == 1
    assert actual["dont_write_bytecode"] is True
    assert str(poison) not in actual["path"]
    assert not canary.exists()
    assert not list(source.rglob("__pycache__"))
    assert verify_source_candidate(source)


def test_historical_packaged_launcher_can_upgrade_but_cannot_claim_isolated_rollback(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    old = consumer._packaged_launcher_v2()
    executable.write_text(old, encoding="utf-8")
    assert consumer._trusted_bundle(app)
    assert not consumer._isolated_bundle_startup(app)
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    current = executable.read_bytes()
    previous = apps / ".AI 投递经理.app.previous"
    old_executable = previous / "Contents" / "MacOS" / "AIApplicationManager"
    assert old_executable.read_text() == old
    refused = rollback_macos_app(apps)
    assert refused["reason"] == "rollback_startup_isolation_unsupported"
    assert executable.read_bytes() == current
    assert old_executable.read_text() == old
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_failed_upgrade_preserves_historical_packaged_app_without_healthy_claim(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    old = consumer._packaged_launcher_v2()
    executable.write_text(old, encoding="utf-8")
    real_start = consumer._candidate_starts
    attempts = 0
    def fail_after_staging(python, source):
        nonlocal attempts
        attempts += 1
        return real_start(python, source) if attempts == 1 else False
    monkeypatch.setattr(consumer, "_candidate_starts", fail_after_staging)
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["reason"] == "post_activation_recovery_required"
    assert result["ok"] is False
    assert executable.read_text() == old
    assert consumer._trusted_bundle(app)
    assert (apps / ".AI 投递经理.app.failed").is_dir()
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_packaged_app_runtime_survives_checkout_removal_and_rejects_tamper(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    release = app / "Contents" / "Resources" / "release"
    runtime = app / "Contents" / "Resources" / "runtime"
    assert verify_runtime_candidate(runtime, release)

    repo.rename(tmp_path / "development-checkout-removed")
    assert _candidate_starts(runtime / "bin" / "python", release)
    assert verify_runtime_candidate(runtime, release)

    python_copy = runtime / "bin" / "python"
    with python_copy.open("ab") as handle:
        handle.write(b"synthetic-tamper")
    assert not verify_runtime_candidate(runtime, release)
    assert rollback_macos_app(apps)["reason"] == "rollback_unavailable"


def test_macos_post_activation_health_failure_restores_previous_bundle(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    marker = app / "Contents" / "known-good.txt"
    marker.write_text("preserve", encoding="utf-8")
    real_start = consumer._candidate_starts
    attempts = 0

    def fail_only_after_activation(runtime_python, source):
        nonlocal attempts
        attempts += 1
        return real_start(runtime_python, source) if attempts != 2 else False

    monkeypatch.setattr(consumer, "_candidate_starts", fail_only_after_activation)
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert attempts == 3
    assert result["ok"] is False
    assert result["reason"] == "post_activation_unhealthy"
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert (apps / ".AI 投递经理.app.failed").is_dir()
    assert not (apps / ".AI 投递经理.app.previous").exists()
    assert verify_source_candidate(app / "Contents" / "Resources" / "release")



def test_failed_candidate_does_not_claim_healthy_rollback_when_restored_app_fails(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    marker = app / "Contents" / "known-good.txt"
    marker.write_text("preserve", encoding="utf-8")
    calls = 0
    real_start = consumer._candidate_starts

    def fail_after_staging(runtime_python, source):
        nonlocal calls
        calls += 1
        return real_start(runtime_python, source) if calls == 1 else False

    monkeypatch.setattr(consumer, "_candidate_starts", fail_after_staging)
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert calls == 3
    assert result["ok"] is False
    assert result["reason"] == "post_activation_recovery_required"
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert result["failed_candidate_path"] == str(apps / ".AI 投递经理.app.failed")
    assert (apps / ".AI 投递经理.app.failed").is_dir()
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_first_install_post_activation_failure_keeps_candidate_for_diagnosis(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    attempts = 0
    real_start = consumer._candidate_starts

    def fail_only_after_activation(runtime_python, source):
        nonlocal attempts
        attempts += 1
        return real_start(runtime_python, source) if attempts == 1 else False

    monkeypatch.setattr(consumer, "_candidate_starts", fail_only_after_activation)
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert attempts == 2
    assert result["reason"] == "post_activation_unhealthy"
    assert result["ok"] is False
    assert not (apps / "AI 投递经理.app").exists()
    assert (apps / ".AI 投递经理.app.failed").is_dir()
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_macos_candidate_rejects_dependency_drift_without_replacing_old_app(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    first = install_macos_app(repo, destination=apps, platform="darwin")
    assert first["ok"] is True
    app = apps / "AI 投递经理.app"
    old_release = app / "Contents" / "Resources" / "release"
    old_digest = (old_release / "release-source-manifest.json").read_text()
    (repo / "requirements.txt").write_text("pydantic==0.0.0\n", encoding="utf-8")

    rejected = install_macos_app(repo, destination=apps, platform="darwin")
    assert rejected["ok"] is False
    assert rejected["reason"] == "candidate_start_failed"
    assert (old_release / "release-source-manifest.json").read_text() == old_digest
    assert verify_source_candidate(old_release)
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_macos_consumer_app_rollback_failure_preserves_current(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    previous = apps / ".AI 投递经理.app.previous"
    original_rename = Path.rename

    def fail_previous(self, target):
        if self == previous:
            raise OSError("synthetic rollback failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_previous)
    result = rollback_macos_app(apps)
    assert result["ok"] is False
    assert result["reason"] == "rollback_activation_failed"
    assert app.is_dir()
    assert previous.is_dir()
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_macos_consumer_app_activation_failure_restores_known_good(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    marker = app / "Contents" / "known-good.txt"
    marker.write_text("keep", encoding="utf-8")
    original_rename = Path.rename

    def fail_candidate(self, target):
        if self.name.endswith(".app.installing"):
            raise OSError("synthetic activation failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_candidate)
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["ok"] is False
    assert result["reason"] == "activation_failed"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_macos_consumer_app_rejects_untrusted_existing_bundle(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    info_path = app / "Contents" / "Info.plist"
    original = info_path.read_bytes()
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleIdentifier"] = "com.example.untrusted"
    with info_path.open("wb") as handle:
        plistlib.dump(info, handle)
    tampered = info_path.read_bytes()

    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["ok"] is False
    assert result["reason"] == "untrusted_app_path"
    assert info_path.read_bytes() == tampered
    assert not (apps / ".AI 投递经理.app.previous").exists()
    assert not (apps / ".AI 投递经理.app.installing").exists()

    info_path.write_bytes(original)
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    previous = apps / ".AI 投递经理.app.previous"
    previous_info = previous / "Contents" / "Info.plist"
    with previous_info.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleIdentifier"] = "com.example.untrusted"
    with previous_info.open("wb") as handle:
        plistlib.dump(info, handle)
    result = rollback_macos_app(apps)
    assert result["ok"] is False
    assert result["reason"] == "rollback_unavailable"
    assert app.is_dir()
    assert previous.is_dir()
    assert not (apps / ".AI 投递经理.app.failed").exists()




def test_macos_consumer_app_upgrades_but_refuses_legacy_write_rollback(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    app = apps / "AI 投递经理.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "AIApplicationManager"
    repo_q = shlex.quote(str(repo))
    old_launcher = f"""#!/bin/zsh
set -u
REPO_ROOT={repo_q}
PYTHON="$REPO_ROOT/.venv/bin/python"
LOG_DIR="$HOME/Library/Logs/AI投递经理"
LOG_FILE="$LOG_DIR/launcher.log"
/bin/mkdir -p "$LOG_DIR"

if [[ ! -x "$PYTHON" ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理缺少运行环境。请重新安装本地入口。" buttons {{"好"}} default button "好" with icon caution'
  exit 1
fi

cd "$REPO_ROOT" || exit 1
"$PYTHON" -m executor.autonomy.cli launch >>"$LOG_FILE" 2>&1
STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  /usr/bin/osascript -e 'display dialog "AI 投递经理没有成功就绪。现有任务不会被提交或丢失。请在 ChatGPT 中检查启动状态。" buttons {{"好"}} default button "好" with icon caution'
fi
exit $STATUS
"""
    executable.write_text(old_launcher, encoding="utf-8")
    executable.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({
            "CFBundleIdentifier": "com.local.job-application-executor.ai-application-manager",
            "CFBundleExecutable": "AIApplicationManager",
        }, handle)

    executable.write_text(old_launcher + "echo tampered\\n", encoding="utf-8")
    refused = install_macos_app(repo, destination=apps, platform="darwin")
    assert refused["reason"] == "untrusted_app_path"
    executable.write_text(old_launcher, encoding="utf-8")
    installed = install_macos_app(repo, destination=apps, platform="darwin")
    assert installed["ok"] is True
    assert installed["replaced"] is True
    previous = apps / ".AI 投递经理.app.previous"
    assert (previous / "Contents" / "MacOS" / "AIApplicationManager").read_text() == old_launcher
    assert verify_source_candidate(app / "Contents" / "Resources" / "release")

    active_launcher = executable.read_bytes()
    restored = rollback_macos_app(apps)
    assert restored["ok"] is False
    assert restored["reason"] == "legacy_rollback_unsupported"
    assert executable.read_bytes() == active_launcher
    assert (previous / "Contents" / "MacOS" / "AIApplicationManager").read_text() == old_launcher
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_macos_consumer_app_preserves_tampered_release_on_install_and_rollback(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    current_cli = app / "Contents" / "Resources" / "release" / "executor" / "autonomy" / "cli.py"
    current_cli.write_text("VERSION = 'tampered'\n", encoding="utf-8")

    refused = install_macos_app(repo, destination=apps, platform="darwin")
    assert refused["ok"] is False
    assert refused["reason"] == "untrusted_app_path"
    assert current_cli.read_text() == "VERSION = 'tampered'\n"
    assert not (apps / ".AI 投递经理.app.previous").exists()

    # A retained rollback copy must pass the same source-integrity gate.
    current_cli.write_text(FAKE_CANDIDATE_CLI, encoding="utf-8")
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    previous = apps / ".AI 投递经理.app.previous"
    previous_cli = previous / "Contents" / "Resources" / "release" / "executor" / "autonomy" / "cli.py"
    previous_cli.write_text("VERSION = 'tampered'\n", encoding="utf-8")
    rollback = rollback_macos_app(apps)
    assert rollback["ok"] is False
    assert rollback["reason"] == "rollback_unavailable"
    assert previous_cli.read_text() == "VERSION = 'tampered'\n"
    assert app.is_dir()



def test_macos_consumer_app_rejects_tampered_packaged_launcher(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    launcher = app / "Contents" / "MacOS" / "AIApplicationManager"
    original = launcher.read_text(encoding="utf-8")
    launcher.write_text(original + "echo tampered\\n", encoding="utf-8")

    refused = install_macos_app(repo, destination=apps, platform="darwin")
    assert refused["reason"] == "untrusted_app_path"
    assert launcher.read_text(encoding="utf-8") == original + "echo tampered\\n"
    assert not (apps / ".AI 投递经理.app.previous").exists()

    launcher.write_text(original, encoding="utf-8")
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    previous = apps / ".AI 投递经理.app.previous"
    previous_launcher = previous / "Contents" / "MacOS" / "AIApplicationManager"
    previous_launcher.write_text(original + "echo tampered\\n", encoding="utf-8")
    refused_rollback = rollback_macos_app(apps)
    assert refused_rollback["reason"] == "rollback_unavailable"
    assert app.is_dir()
    assert previous.is_dir()
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_macos_consumer_app_rejects_symlinked_release_directory(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    resources = apps / "AI 投递经理.app" / "Contents" / "Resources"
    release = resources / "release"
    release.rename(resources / "release-original")
    release.symlink_to("release-original", target_is_directory=True)
    assert not verify_source_candidate(release)

    refused = install_macos_app(repo, destination=apps, platform="darwin")
    assert refused["ok"] is False
    assert refused["reason"] == "untrusted_app_path"
    assert release.is_symlink()
    assert (resources / "release-original").is_dir()
    assert not (apps / ".AI 投递经理.app.previous").exists()



def test_macos_consumer_app_preserves_unknown_pending_staging(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    staging = apps / ".AI 投递经理.app.installing"
    staging.mkdir()
    sentinel = staging / "do-not-delete.txt"
    sentinel.write_text("keep", encoding="utf-8")

    refused = install_macos_app(repo, destination=apps, platform="darwin")
    assert refused["ok"] is False
    assert refused["reason"] == "staging_pending"
    assert sentinel.read_text() == "keep"
    assert app.is_dir()
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_macos_consumer_app_rejects_invalid_staging_before_replacement(
    tmp_path, monkeypatch
):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    marker = app / "Contents" / "known-good.txt"
    marker.write_text("preserve", encoding="utf-8")

    def corrupt_candidate(_info, handle, *, sort_keys):
        handle.write(b"invalid-plist")

    monkeypatch.setattr(plistlib, "dump", corrupt_candidate)
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["ok"] is False
    assert result["reason"] == "candidate_invalid"
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert not (apps / ".AI 投递经理.app.previous").exists()
    assert not (apps / ".AI 投递经理.app.installing").exists()


def test_macos_consumer_app_rejects_unstartable_candidate_before_activation(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    previous_version = app / "Contents" / "Resources" / "release" / "executor" / "autonomy" / "cli.py"
    assert previous_version.read_text() == FAKE_CANDIDATE_CLI

    (repo / "executor" / "autonomy" / "cli.py").write_text(
        "import deliberately_missing_release_dependency\n", encoding="utf-8"
    )
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["ok"] is False
    assert result["reason"] == "candidate_start_failed"
    assert previous_version.read_text() == FAKE_CANDIDATE_CLI
    assert not (apps / ".AI 投递经理.app.previous").exists()
    assert not (apps / ".AI 投递经理.app.installing").exists()



def test_macos_candidate_requires_live_health_and_matching_loaded_source(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    release = app / "Contents" / "Resources" / "release"
    known_good = (release / "release-source-manifest.json").read_bytes()

    candidate_cli = repo / "executor" / "autonomy" / "cli.py"
    candidate_cli.write_text("VERSION = 'import_only'\n", encoding="utf-8")
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["reason"] == "candidate_start_failed"
    assert (release / "release-source-manifest.json").read_bytes() == known_good
    assert not (apps / ".AI 投递经理.app.previous").exists()

    candidate_cli.write_text(
        FAKE_CANDIDATE_CLI.replace(
            "digest = source_manifest(Path(__file__).resolve().parents[2])['source_sha256']",
            "digest = '0' * 64",
        ),
        encoding="utf-8",
    )
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["reason"] == "candidate_start_failed"
    assert (release / "release-source-manifest.json").read_bytes() == known_good
    assert verify_source_candidate(release)
    assert not (apps / ".AI 投递经理.app.previous").exists()


def test_macos_consumer_app_refuses_missing_virtualenv(tmp_path):
    result = install_macos_app(
        tmp_path / "missing-repo",
        destination=tmp_path / "Applications",
        platform="darwin",
    )
    assert result == {
        "ok": False,
        "reason": "venv_missing",
        "message": "没有找到项目虚拟环境，请先完成本地依赖安装。",
    }


def test_dashboard_uses_consumer_facing_status_language():
    assert "进行中" in DASHBOARD_HTML
    assert "等你确认" in DASHBOARD_HTML
    assert "正在等待验证码" in DASHBOARD_HTML
    assert "需要安全验证" in DASHBOARD_HTML
    assert "已就绪 · 最终提交由你确认" in DASHBOARD_HTML
    assert "/ui/api/readiness" in DASHBOARD_HTML
    assert '<form id="newtask"' in DASHBOARD_HTML
    assert '添加岗位请使用左侧表单' in DASHBOARD_HTML
    assert "查看诊断" in DASHBOARD_HTML
    assert "复制报告" in DASHBOARD_HTML
    assert "更新状态" in DASHBOARD_HTML
    assert "/ui/api/diagnostics" in DASHBOARD_HTML
    assert "/ui/api/update-status" in DASHBOARD_HTML
    assert "不会安装版本、重启服务或恢复任务" in DASHBOARD_HTML


def test_macos_rollback_refuses_unhealthy_retained_app_before_moving_current(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    previous = apps / ".AI 投递经理.app.previous"
    current_manifest = (app / "Contents" / "Resources" / "release" / "release-source-manifest.json").read_bytes()
    monkeypatch.setattr(consumer, "_candidate_starts", lambda *_: False)

    result = rollback_macos_app(apps)

    assert result["ok"] is False
    assert result["reason"] == "rollback_unhealthy"
    assert (app / "Contents" / "Resources" / "release" / "release-source-manifest.json").read_bytes() == current_manifest
    assert previous.is_dir()
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_macos_rollback_restores_current_if_old_app_fails_after_activation(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    marker = app / "Contents" / "current.txt"
    marker.write_text("keep", encoding="utf-8")
    real_start = consumer._candidate_starts
    attempts = 0

    def fail_after_move(runtime_python, source):
        nonlocal attempts
        attempts += 1
        return False if attempts == 2 else real_start(runtime_python, source)

    monkeypatch.setattr(consumer, "_candidate_starts", fail_after_move)
    result = rollback_macos_app(apps)

    assert attempts == 3
    assert result["ok"] is False
    assert result["reason"] == "rollback_post_activation_unhealthy"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert (apps / ".AI 投递经理.app.previous").is_dir()
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_macos_rollback_refuses_incompatible_task_journal_without_moving_either_app(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility

    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    previous = apps / ".AI 投递经理.app.previous"
    current_manifest = (app / "Contents" / "Resources" / "release" / "release-source-manifest.json").read_bytes()
    state_root = tmp_path / "synthetic-state"
    calls = []

    def incompatible(runtime_python, source, state):
        calls.append((runtime_python, source, state))
        return False

    monkeypatch.setattr(state_compatibility, "task_state_candidate_compatible", incompatible)
    result = rollback_macos_app(apps, task_state_root=state_root)

    assert result["ok"] is False
    assert result["reason"] == "rollback_state_incompatible"
    assert calls == [(previous / "Contents" / "Resources" / "runtime" / "bin" / "python",
                      previous / "Contents" / "Resources" / "release", state_root)]
    assert (app / "Contents" / "Resources" / "release" / "release-source-manifest.json").read_bytes() == current_manifest
    assert previous.is_dir()
    assert not (apps / ".AI 投递经理.app.failed").exists()


def test_macos_rollback_refuses_a_live_task_state_owner(tmp_path):
    from executor.autonomy.worker import ProcessLock

    apps = tmp_path / "Applications"
    apps.mkdir()
    state = tmp_path / "synthetic-state"
    with ProcessLock(state / "worker.lock"):
        result = rollback_macos_app(apps, task_state_root=state)
    assert result["ok"] is False
    assert result["reason"] == "task_state_in_use"
    assert not (apps / "AI 投递经理.app").exists()


def test_macos_install_refuses_live_task_state_before_staging(tmp_path):
    from executor.autonomy.worker import ProcessLock

    apps = tmp_path / "Applications"
    state = tmp_path / "synthetic-state"
    with ProcessLock(state / "worker.lock"):
        result = install_macos_app(
            tmp_path / "missing-source", destination=apps,
            platform="darwin", task_state_root=state)
    assert result["reason"] == "task_state_in_use"
    assert not (apps / "AI 投递经理.app").exists()
    assert not (apps / ".AI 投递经理.app.installing").exists()


@pytest.mark.parametrize("name", [
    "tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm", "worker.lock", "service.json",
])
def test_macos_install_refuses_aliased_task_state_without_touching_it(tmp_path, name):
    apps = tmp_path / "Applications"
    state = tmp_path / "synthetic-state"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("synthetic untouched")
    (state / name).symlink_to(outside)
    result = install_macos_app(
        tmp_path / "missing-source", destination=apps,
        platform="darwin", task_state_root=state)
    assert result["reason"] == "task_state_unavailable"
    assert outside.read_text() == "synthetic untouched"
    assert not (apps / ".AI 投递经理.app.installing").exists()


@pytest.mark.parametrize("destructive", [False, True, "schema", "answers", "post_health"])
def test_macos_update_proves_journal_compatibility_before_activation(
    tmp_path, monkeypatch, destructive
):
    import shutil
    import sqlite3
    from contextlib import closing
    from executor.autonomy.worker import ProcessLock

    repo = tmp_path / "Job-Application-Executor"
    repo.mkdir()
    actual = Path(__file__).resolve().parents[1]
    shutil.copytree(actual / "executor", repo / "executor",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(actual / "requirements.txt", repo / "requirements.txt")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    apps = tmp_path / "Applications"
    state = tmp_path / "synthetic-state"
    assert install_macos_app(
        repo, destination=apps, platform="darwin", task_state_root=state)["ok"]
    app = apps / "AI 投递经理.app"
    previous = apps / ".AI 投递经理.app.previous"
    release = app / "Contents" / "Resources" / "release"
    before_manifest = (release / "release-source-manifest.json").read_bytes()
    marker = app / "Contents" / "known-good.txt"
    marker.write_text("preserve")

    # Keep a real committed WAL open. The installed candidate must see this
    # task while its health process continues to use an empty scratch journal.
    with closing(sqlite3.connect(state / "tasks.sqlite3")) as db:
        db.executescript("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                spec TEXT NOT NULL, stage TEXT NOT NULL, checkpoint TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL,
                next_run REAL NOT NULL DEFAULT 0, blocker TEXT,
                details TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, at REAL,
                kind TEXT NOT NULL, stage TEXT NOT NULL);
        """)
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE UNIQUE INDEX task_authority_guard ON tasks(idempotency_key,checkpoint) WHERE stage='BLOCKED'")
        db.execute(
            "INSERT INTO tasks(task_id,idempotency_key,spec,stage,checkpoint,blocker,created,updated) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("synthetic-task", "synthetic-key", '{"synthetic":true}', "BLOCKED",
             "FORM_FILLED", "unknown_outcome", 1, 2))
        db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(?,?,?,?)",
                   ("synthetic-task", 2, "unknown_outcome", "BLOCKED"))
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        key_path = state / "task-answers.key"
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        db.execute("""CREATE TABLE task_answer_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL, field_key TEXT NOT NULL, ciphertext BLOB NOT NULL,
            answer_version INTEGER NOT NULL, source TEXT NOT NULL,
            task_revision INTEGER NOT NULL, created REAL NOT NULL,
            reuse_requested INTEGER NOT NULL DEFAULT 0,
            reuse_applied INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""INSERT INTO task_answer_events
            (task_id,field_key,ciphertext,answer_version,source,task_revision,created)
            VALUES(?,?,?,?,?,?,?)""", ("synthetic-task", "family.primary.role",
                Fernet(key).encrypt(json.dumps("PRIVATE_ACTIVATION_ANSWER_CANARY").encode()),
                1, "user_explicit_task", 0, 2))
        db.commit()
        answer_before = db.execute("SELECT * FROM task_answer_events").fetchall()
        before = (db.execute("SELECT * FROM tasks").fetchall(),
                  db.execute("SELECT * FROM events").fetchall())
        assert (state / "tasks.sqlite3-wal").stat().st_size > 0

        if destructive == "answers":
            candidate_answers = repo / "executor" / "facts" / "answers.py"
            with candidate_answers.open("a", encoding="utf-8") as handle:
                handle.write("\nTaskAnswerStore.load = lambda self, task_id: {}\n")
        elif destructive in {True, "schema"}:
            candidate_queue = repo / "executor" / "autonomy" / "queue.py"
            with candidate_queue.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n_original_init = TaskQueue.__init__\n"
                    "def _destructive_init(self, root=RUNTIME, **kwargs):\n"
                    "    _original_init(self, root, **kwargs)\n"
                    "    with sqlite3.connect(Path(root)/'tasks.sqlite3') as db:\n"
                    + ("        db.execute(\"DROP INDEX IF EXISTS task_authority_guard\")\n"
                     if destructive == "schema" else
                     "        db.execute(\"UPDATE tasks SET blocker=NULL,stage='DISCOVERED'\")\n")
                    + "TaskQueue.__init__ = _destructive_init\n")

        starts = []
        real_start = consumer._candidate_starts

        def checked_start(runtime_python, source):
            # Both staging and final-path health must stay inside the fence.
            with pytest.raises(RuntimeError, match="another local worker"):
                with ProcessLock(state / "worker.lock"):
                    pytest.fail("updater lost the daemon lock")
            with (state / "migration.lock").open("a+") as initializer:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(initializer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            starts.append(source)
            if destructive == "post_health" and len(starts) == 2:
                return False
            return real_start(runtime_python, source)

        monkeypatch.setattr(consumer, "_candidate_starts", checked_start)
        result = install_macos_app(
            repo, destination=apps, platform="darwin", task_state_root=state)
        assert (db.execute("SELECT * FROM tasks").fetchall(),
                db.execute("SELECT * FROM events").fetchall()) == before
        assert "revision" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == answer_before
        assert key_path.read_bytes() == key
        assert key_path.stat().st_mode & 0o077 == 0
        assert b"PRIVATE_ACTIVATION_ANSWER_CANARY" not in (state / "tasks.sqlite3-wal").read_bytes()
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
        assert not (state / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
        from executor.autonomy.state_compatibility import verify_task_state_backup
        backup = result["task_state_backup"]
        capsule = Path(backup["path"])
        assert capsule.parent == apps
        assert capsule.stat().st_mode & 0o077 == 0
        assert verify_task_state_backup(capsule, backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert backup["receipt"]["legacy_writer_retirement"] == "NOT_CERTIFIED"
        assert (capsule / "task-answers.key").read_bytes() == key
        with closing(sqlite3.connect(capsule / "tasks.sqlite3")) as snapshot:
            assert (snapshot.execute("SELECT * FROM tasks").fetchall(),
                    snapshot.execute("SELECT * FROM events").fetchall()) == before
            assert snapshot.execute("SELECT * FROM task_answer_events").fetchall() == answer_before
        assert "PRIVATE_ACTIVATION_ANSWER_CANARY" not in json.dumps(result)
        if destructive == "post_health":
            assert result["reason"] == "post_activation_unhealthy"
            assert len(starts) == 3
            assert marker.read_text() == "preserve"
            assert (release / "release-source-manifest.json").read_bytes() == before_manifest
            assert not previous.exists()
            assert (apps / ".AI 投递经理.app.failed").is_dir()
        elif destructive:
            assert result["reason"] == "candidate_state_incompatible"
            assert len(starts) == 1
            assert marker.read_text() == "preserve"
            assert (release / "release-source-manifest.json").read_bytes() == before_manifest
            assert not previous.exists()
        else:
            assert result["ok"] is True
            assert result["replaced"] is True
            assert len(starts) == 2
            assert (previous / "Contents" / "known-good.txt").read_text() == "preserve"
            assert (previous / "Contents" / "Resources" / "release" /
                    "release-source-manifest.json").read_bytes() == before_manifest
        assert not (apps / ".AI 投递经理.app.installing").exists()
    # The transaction releases the fence after success or refusal.
    with ProcessLock(state / "worker.lock"):
        pass


def test_candidate_with_open_dependency_graph_preserves_installed_app(tmp_path):
    from executor.autonomy.release import read_release_identity

    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    assert install_macos_app(repo, destination=apps, platform="darwin")["ok"]
    app = apps / "AI 投递经理.app"
    installed_source = app / "Contents" / "Resources" / "release"
    known_identity = read_release_identity(installed_source)
    assert known_identity["status"] == "verified"

    # The current interpreter can import Pydantic and all its dependencies.
    # Omitting their pins must nevertheless refuse this intact source candidate.
    (repo / "requirements.txt").write_text(
        f"pydantic=={version('pydantic')}\n", encoding="utf-8"
    )
    refused = install_macos_app(repo, destination=apps, platform="darwin")
    assert refused["ok"] is False
    assert refused["reason"] == "candidate_start_failed"
    assert read_release_identity(installed_source) == known_identity
    assert not list(apps.glob("*.staging.app"))

@pytest.mark.parametrize("record,reason", [
    ("live", "task_state_in_use"), ("malformed", "task_state_unavailable"),
])
def test_app_install_and_rollback_refuse_unlocked_legacy_daemon_before_any_activation(
    tmp_path, monkeypatch, record, reason
):
    from contextlib import closing
    from test_task_state_compatibility_v1 import legacy_state, authority

    apps = tmp_path / "Applications"
    apps.mkdir()
    app = apps / "AI 投递经理.app"
    previous = apps / ".AI 投递经理.app.previous"
    app.mkdir()
    previous.mkdir()
    (app / "known-good").write_text("current")
    (previous / "retained").write_text("previous")
    state = tmp_path / "synthetic-state"
    with closing(legacy_state(state)) as db:
        before = authority(db)
        registry = state / "service.json"
        registry.write_text(json.dumps({"pid": os.getpid()}) if record == "live" else "invalid")
        before_record = registry.read_bytes()

        def forbidden_start(*args, **kwargs):
            pytest.fail("a live or ambiguous old writer must refuse before candidate startup")

        monkeypatch.setattr(consumer, "_candidate_starts", forbidden_start)
        installed = install_macos_app(
            tmp_path / "missing-candidate", destination=apps, platform="darwin",
            task_state_root=state)
        rolled_back = rollback_macos_app(apps, task_state_root=state)
        assert installed["reason"] == reason
        assert rolled_back["reason"] == reason
        assert authority(db) == before
        assert registry.read_bytes() == before_record
    assert (app / "known-good").read_text() == "current"
    assert (previous / "retained").read_text() == "previous"
    assert not (apps / ".AI 投递经理.app.installing").exists()
    assert not (apps / ".AI 投递经理.app.failed").exists()


def _legacy_app(app, repo):
    """Actual exact historical launcher, with its independent repository path."""
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "AIApplicationManager"
    executable.write_text(consumer._legacy_launcher(repo), encoding="utf-8")
    executable.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({
            "CFBundleIdentifier": consumer.BUNDLE_ID,
            "CFBundleExecutable": "AIApplicationManager",
        }, handle)
    return executable


@pytest.mark.parametrize("location", ["candidate-repo", "old-launcher-repo", "old-packaged-release"])
def test_new_default_state_cannot_strand_legacy_wal_authority(tmp_path, monkeypatch, location):
    from contextlib import closing
    from test_task_state_compatibility_v1 import legacy_state, authority

    repo = tmp_path / "candidate" / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    app = apps / (consumer.APP_NAME + ".app")
    old_repo = tmp_path / "historical" / "Job-Application-Executor"
    executable = _legacy_app(app, old_repo)
    original_launcher = executable.read_bytes()
    roots = {
        "candidate-repo": repo / "runtime" / "autonomy",
        "old-launcher-repo": old_repo / "runtime" / "autonomy",
        "old-packaged-release": app / "Contents" / "Resources" / "release" / "runtime" / "autonomy",
    }
    state = roots[location]
    state.parent.mkdir(parents=True, exist_ok=True)
    with closing(legacy_state(state)) as db:
        before = authority(db)
        assert (state / "tasks.sqlite3-wal").stat().st_size > 0
        secret = state / "task-answers.key"
        secret.write_bytes(b"synthetic-private-answer-key-never-export")
        secret.chmod(0o600)
        def forbidden_start(*args, **kwargs):
            pytest.fail("state split must refuse before any candidate service starts")
        monkeypatch.setattr(consumer, "_candidate_starts", forbidden_start)
        result = install_macos_app(repo, destination=apps, platform="darwin")
        # A runtime appended inside an immutable packaged source also makes
        # that existing bundle untrusted. Preserve the established reason and
        # assert the independent finite-path state detector still sees it.
        if location == "old-packaged-release":
            assert result["reason"] == "untrusted_app_path"
            assert consumer._legacy_state_migration_needed(repo, app, tmp_path / "new")
        else:
            assert result["reason"] == "legacy_state_migration_required"
        assert authority(db) == before
        assert secret.read_bytes() == b"synthetic-private-answer-key-never-export"
        assert "synthetic-private" not in json.dumps(result)
    assert executable.read_bytes() == original_launcher
    assert not (apps / ("." + consumer.APP_NAME + ".app.previous")).exists()
    assert not (apps / ("." + consumer.APP_NAME + ".app.installing")).exists()


@pytest.mark.parametrize("artifact", ["task-answers.key", "auth.token", "unknown-private-state"])
def test_first_packaged_install_retains_untransferred_private_state(tmp_path, monkeypatch, artifact):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    state = repo / "runtime" / "autonomy"
    state.mkdir(parents=True)
    item = state / artifact
    item.write_bytes(b"synthetic-private-canary")
    item.chmod(0o600)
    monkeypatch.setattr(consumer, "_candidate_starts", lambda *_: pytest.fail("must not start"))
    apps = tmp_path / "Applications"
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["reason"] == "legacy_state_migration_required"
    assert item.read_bytes() == b"synthetic-private-canary"
    assert str(state) not in json.dumps(result)
    assert "synthetic-private-canary" not in json.dumps(result)
    assert not (apps / (consumer.APP_NAME + ".app")).exists()


def test_legacy_path_alias_refuses_without_following_or_copying_private_state(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "task-answers.key").write_bytes(b"synthetic-unrelated-key")
    (repo / "runtime").symlink_to(outside, target_is_directory=True)
    apps = tmp_path / "Applications"
    monkeypatch.setattr(consumer, "_candidate_starts", lambda *_: pytest.fail("must not start"))
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["reason"] == "legacy_state_unavailable"
    assert (outside / "task-answers.key").read_bytes() == b"synthetic-unrelated-key"
    assert not (apps / (consumer.APP_NAME + ".app")).exists()


def test_historical_launcher_parent_alias_cannot_certify_empty_legacy_authority(tmp_path, monkeypatch):
    repo = tmp_path / "candidate" / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    real_parent = tmp_path / "real-historical"
    legacy_root = real_parent / "Job-Application-Executor" / "runtime" / "autonomy"
    legacy_root.mkdir(parents=True)
    (legacy_root / "worker.lock").touch()
    canary = real_parent / "private-canary"
    canary.write_bytes(b"synthetic-unrelated-private-bytes")
    alias_parent = tmp_path / "historical-alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    old_repo = alias_parent / "Job-Application-Executor"
    apps = tmp_path / "Applications"
    app = apps / (consumer.APP_NAME + ".app")
    launcher = _legacy_app(app, old_repo)
    original_launcher = launcher.read_bytes()
    with pytest.raises(ValueError, match="legacy_state_path_invalid"):
        consumer._legacy_state_migration_needed(repo, app, tmp_path / "selected-state")
    monkeypatch.setattr(consumer, "_candidate_starts",
                        lambda *_: pytest.fail("ambiguous old authority must refuse before startup"))
    result = install_macos_app(repo, destination=apps, platform="darwin",
                               task_state_root=tmp_path / "selected-state")
    assert result["reason"] == "legacy_state_unavailable"
    assert canary.read_bytes() == b"synthetic-unrelated-private-bytes"
    assert launcher.read_bytes() == original_launcher
    assert (legacy_root / "worker.lock").is_file()
    assert not (apps / ("." + consumer.APP_NAME + ".app.installing")).exists()
    assert not (apps / ("." + consumer.APP_NAME + ".app.previous")).exists()
    assert "private-canary" not in json.dumps(result)


def test_legacy_state_detection_accepts_explicit_same_authority_and_empty_locks(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    state = repo / "runtime" / "autonomy"
    state.mkdir(parents=True)
    (state / "tasks.sqlite3-wal").write_bytes(b"synthetic-wal")
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    assert not consumer._legacy_state_migration_needed(repo, app, state)
    assert consumer._legacy_state_migration_needed(repo, app, tmp_path / "new")
    (state / "tasks.sqlite3-wal").unlink()
    (state / "worker.lock").touch()
    (state / "migration.lock").touch()
    assert not consumer._legacy_state_migration_needed(repo, app, tmp_path / "new")


@pytest.mark.parametrize("kind", ["hardlink", "fifo", "directory"])
def test_app_install_and_rollback_refuse_nonordinary_transaction_lock_before_staging(
    tmp_path, monkeypatch, kind
):
    apps = tmp_path / "Applications"
    apps.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("synthetic app lock payload")
    outside.chmod(0o644)
    lock = apps / ".AI 投递经理.app.transaction.lock"
    if kind == "hardlink":
        os.link(outside, lock)
    elif kind == "fifo":
        os.mkfifo(lock, 0o644)
    else:
        lock.mkdir()
    metadata = lock.stat()
    monkeypatch.setattr(consumer, "_install_macos_app_unlocked",
                        lambda *args, **kwargs: pytest.fail("invalid lock must not stage an app"))
    monkeypatch.setattr(consumer, "_rollback_macos_app_unlocked",
                        lambda *args, **kwargs: pytest.fail("invalid lock must not activate rollback"))
    assert install_macos_app(tmp_path / "source", destination=apps, platform="darwin")["reason"] == "update_lock_unavailable"
    assert rollback_macos_app(apps)["reason"] == "update_lock_unavailable"
    assert list(apps.iterdir()) == [lock]
    assert lock.stat().st_mode == metadata.st_mode
    assert outside.read_text() == "synthetic app lock payload"
    assert outside.stat().st_mode & 0o777 == 0o644


def test_app_transactions_refuse_queue_initializer_before_candidate_start(tmp_path, monkeypatch):
    import fcntl
    from contextlib import closing
    from test_task_state_compatibility_v1 import legacy_state, authority

    apps = tmp_path / "Applications"
    apps.mkdir()
    state = tmp_path / "state"
    monkeypatch.setattr(consumer, "_install_macos_app_unlocked",
                        lambda *args, **kwargs: pytest.fail("initializer must fence install"))
    monkeypatch.setattr(consumer, "_rollback_macos_app_unlocked",
                        lambda *args, **kwargs: pytest.fail("initializer must fence rollback"))
    with closing(legacy_state(state)) as db:
        before = authority(db)
        with (state / "migration.lock").open("a+") as initializer:
            fcntl.flock(initializer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert install_macos_app(tmp_path / "source", destination=apps, platform="darwin",
                                     task_state_root=state)["reason"] == "task_state_in_use"
            assert rollback_macos_app(apps, task_state_root=state)["reason"] == "task_state_in_use"
        assert authority(db) == before
        assert not (apps / "AI 投递经理.app").exists()
        from executor.autonomy.state_compatibility import task_state_guard
        with task_state_guard(state):
            pass


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("defect", ["publication", "capsule_drift", "candidate_rejection"])
def test_app_transaction_keeps_versions_and_authority_when_durable_preflight_refuses(
    tmp_path, monkeypatch, action, defect
):
    from contextlib import closing
    from cryptography.fernet import Fernet
    from executor.autonomy import state_compatibility
    from executor.autonomy.worker import ProcessLock
    from test_task_state_compatibility_v1 import legacy_state, authority, encrypted_answers

    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    empty = tmp_path / "empty-build-state"
    assert install_macos_app(repo, destination=apps, platform="darwin", task_state_root=empty)["ok"]
    if action == "rollback":
        (repo / "executor" / "consumer_entry.py").write_text("VERSION='second'\n")
        assert install_macos_app(repo, destination=apps, platform="darwin", task_state_root=empty)["ok"]
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    manifest = app / "Contents" / "Resources" / "release" / "release-source-manifest.json"
    current_manifest = manifest.read_bytes()
    previous_manifest = (previous / "Contents" / "Resources" / "release" /
                         "release-source-manifest.json").read_bytes() if previous.exists() else None
    state = tmp_path / "synthetic-private-state"
    with closing(legacy_state(state)) as db:
        key = encrypted_answers(state, db)
        before = authority(db)
        answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        real_stage = state_compatibility._stage_task_state_backup_locked
        seen = []
        def stage(root, destination):
            with pytest.raises(RuntimeError, match="another local worker"):
                with ProcessLock(state / "worker.lock"):
                    pytest.fail("backup must share the install/rollback writer fence")
            with (state / "migration.lock").open("a+") as initializer:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(initializer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(destination)
            receipt = real_stage(root, destination)
            if defect == "capsule_drift":
                (destination / "task-answers.key").write_bytes(Fernet.generate_key())
            return receipt
        monkeypatch.setattr(state_compatibility, "_stage_task_state_backup_locked", stage)
        if defect == "publication":
            native_link = state_compatibility.os.link
            def failed_publish(source, target, *args, **kwargs):
                if Path(source).name == ".backup-manifest.tmp":
                    (Path(target).parent / "unexpected").write_text("UNKNOWN_BACKUP_CANARY")
                    raise OSError("synthetic receipt publication failure")
                return native_link(source, target, *args, **kwargs)
            monkeypatch.setattr(state_compatibility.os, "link", failed_publish)
        def incompatible(*args):
            if defect != "candidate_rejection":
                pytest.fail("invalid/unpublished capsule cannot reach candidate schema/decoder")
            return False
        monkeypatch.setattr(state_compatibility, "task_state_candidate_compatible", incompatible)
        if action == "install":
            result = install_macos_app(repo, destination=apps, platform="darwin", task_state_root=state)
            assert result["reason"] == "candidate_state_incompatible"
        else:
            result = rollback_macos_app(apps, task_state_root=state)
            assert result["reason"] == "rollback_state_incompatible"
        assert result["ok"] is False
        assert len(seen) == 1
        assert manifest.read_bytes() == current_manifest
        if previous_manifest is not None:
            assert (previous / "Contents" / "Resources" / "release" /
                    "release-source-manifest.json").read_bytes() == previous_manifest
        else:
            assert not previous.exists()
        assert not (apps / ("." + consumer.APP_NAME + ".app.installing")).exists()
        assert not (apps / ("." + consumer.APP_NAME + ".app.failed")).exists()
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == answers
        assert (state / "task-answers.key").read_bytes() == key
        assert "PRIVATE_LATEST_ANSWER_CANARY" not in json.dumps(result)
        if defect == "publication":
            assert "task_state_backup" not in result
            assert sorted(p.name for p in seen[0].iterdir()) == ["unexpected"]
            assert (seen[0] / "unexpected").read_text() == "UNKNOWN_BACKUP_CANARY"
        else:
            backup = result["task_state_backup"]
            assert Path(backup["path"]) == seen[0]
            assert state_compatibility.verify_task_state_backup(
                seen[0], backup["receipt"]) is (defect == "candidate_rejection")
    with ProcessLock(state / "worker.lock"):
        pass


def test_real_packaged_rollback_retains_a_bound_backup_without_restoring_old_task_values(
    tmp_path, monkeypatch
):
    import shutil
    import sqlite3
    from contextlib import closing
    from executor.autonomy import state_compatibility
    from executor.autonomy.worker import ProcessLock
    from test_task_state_compatibility_v1 import legacy_state, authority, encrypted_answers

    actual = Path(__file__).resolve().parents[1]
    repo = tmp_path / "Job-Application-Executor"
    repo.mkdir()
    shutil.copytree(actual / "executor", repo / "executor",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(actual / "requirements.txt", repo / "requirements.txt")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    apps = tmp_path / "Applications"
    empty = tmp_path / "empty-build-state"
    assert install_macos_app(repo, destination=apps, platform="darwin", task_state_root=empty)["ok"]
    first = (apps / (consumer.APP_NAME + ".app") / "Contents" / "Resources" /
             "release" / "release-source-manifest.json").read_bytes()
    with (repo / "executor" / "__init__.py").open("a") as file:
        file.write("\n# Synthetic second release identity.\n")
    assert install_macos_app(repo, destination=apps, platform="darwin", task_state_root=empty)["ok"]
    state = tmp_path / "synthetic-private-state"
    with closing(legacy_state(state)) as db:
        key = encrypted_answers(state, db)
        before = authority(db)
        answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        (state / "profile.json").write_text("PRIVATE_ROLLBACK_PROFILE_CANARY")
        (state / "auth.token").write_text("PRIVATE_ROLLBACK_TOKEN_CANARY")
        result = rollback_macos_app(apps, task_state_root=state)
        assert result["ok"] is True
        assert result["restored"] is True
        backup = result["task_state_backup"]
        capsule = Path(backup["path"])
        assert state_compatibility.verify_task_state_backup(capsule, backup["receipt"])
        assert sorted(p.name for p in capsule.iterdir()) == [
            "backup-manifest.json", "task-answers.key", "tasks.sqlite3"]
        assert (capsule / "task-answers.key").read_bytes() == key
        with closing(sqlite3.connect(capsule / "tasks.sqlite3")) as snapshot:
            assert authority(snapshot) == before
            assert snapshot.execute("SELECT * FROM task_answer_events").fetchall() == answers
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == answers
        assert (state / "task-answers.key").read_bytes() == key
        assert (state / "profile.json").read_text() == "PRIVATE_ROLLBACK_PROFILE_CANARY"
        assert (state / "auth.token").read_text() == "PRIVATE_ROLLBACK_TOKEN_CANARY"
        assert "PRIVATE_" not in json.dumps(result)
        assert (Path(result["app_path"]) / "Contents" / "Resources" / "release" /
                "release-source-manifest.json").read_bytes() == first
        assert Path(result["failed_candidate_path"]).is_dir()
        assert not (apps / ("." + consumer.APP_NAME + ".app.previous")).exists()
        assert not (state / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
    with ProcessLock(state / "worker.lock"):
        pass


def test_app_presenter_consumes_actual_one_use_ui_admission_without_browser(
        tmp_path, monkeypatch):
    import http.cookiejar
    import threading
    from executor.autonomy.manager import ManagerController
    from executor.autonomy.queue import TaskQueue
    from executor.autonomy.supervisor import Supervisor, create_server, local_token
    from executor.autonomy.worker import Worker
    from executor.autonomy.consumer_presentation import ConsumerSurface

    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")
    runtime = tmp_path / "runtime"
    queue = TaskQueue(runtime)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    class NoProvider:
        available = False
        def decide(self, *_args):
            pytest.fail("UI presentation must not invoke a provider")
    manager = ManagerController(queue, worker, provider=NoProvider(),
                                settings={"deepseek": {"enabled": False}})
    supervisor = Supervisor(queue, worker=worker, manager=manager,
                            token=local_token(runtime))
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(cli.webbrowser, "open",
        lambda *_args, **_kwargs: pytest.fail("app host must not open an external browser"))
    before = (queue.tasks(), queue.recent_events(1000))
    seen = []
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    def app_host(surface):
        assert isinstance(surface, ConsumerSurface)
        assert surface.surface == "dashboard"
        assert surface.allows_navigation(surface.url)
        with opener.open(surface.url, timeout=10) as response:
            assert response.status == 200
            assert response.url == f"http://127.0.0.1:{server.server_port}/ui"
            assert "AI 投递经理" in response.read().decode()
        seen.append(surface)
        return True
    try:
        first = cli.open_ui(runtime, server.server_port, presenter=app_host)
        assert first == {"ok": True, "opened": True}
        assert any("HttpOnly" in cookie._rest for cookie in jar)
        with pytest.raises(urllib.error.HTTPError) as reused:
            opener.open(seen[0].url, timeout=10)
        assert reused.value.code == 401
        second = cli.open_ui(runtime, server.server_port, presenter=app_host)
        assert second == first
        assert len(seen) == 2
        assert seen[0].url != seen[1].url
        assert not seen[0].allows_navigation("https://private.example.test/")
        assert not seen[0].allows_navigation(f"http://127.0.0.1:{server.server_port}/v1/tasks")
        assert before == (queue.tasks(), queue.recent_events(1000))
        report = json.dumps([first, second, *(s.safe_summary() for s in seen)])
        for surface in seen:
            from urllib.parse import parse_qs, urlsplit
            ticket = parse_qs(urlsplit(surface.url).query)["ticket"][0]
            assert ticket not in report
            assert ticket not in repr(surface)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("outcome", ["refused", "truthy_mapping", "private_exception"])
def test_app_presentation_failure_never_falls_back_or_reports_credentials(monkeypatch, outcome):
    from executor.autonomy.consumer_presentation import ConsumerSurface, present_surface
    surface = ConsumerSurface.dashboard(9344, "PRIVATE_UI_TICKET_CANARY")
    calls = []
    monkeypatch.setattr(cli.webbrowser, "open",
        lambda *_args, **_kwargs: pytest.fail("failed app host must not fall back to browser"))
    def app_host(candidate):
        calls.append(candidate)
        if outcome == "private_exception":
            raise RuntimeError(candidate.url)
        return {"opened": False} if outcome == "truthy_mapping" else False
    assert present_surface(surface, presenter=app_host) is False
    assert calls == [surface]
    assert "PRIVATE_UI_TICKET_CANARY" not in repr(surface)
    assert "PRIVATE_UI_TICKET_CANARY" not in json.dumps(surface.safe_summary())


@pytest.mark.parametrize("port", [True, 0, 65536, "9344"])
def test_invalid_app_presentation_port_refuses_before_ticket_or_service_actions(
        tmp_path, monkeypatch, port):
    monkeypatch.setattr(cli, "request", lambda *_args: pytest.fail("invalid port must not mint ticket"))
    monkeypatch.setattr(cli, "lifecycle", lambda *_args: pytest.fail("invalid port must not start service"))
    monkeypatch.setattr(cli, "ensure_chrome", lambda: pytest.fail("invalid port must not start Chrome"))
    with pytest.raises(ValueError, match="invalid consumer presentation"):
        cli.open_ui(tmp_path, port, presenter=lambda _surface: True)
    with pytest.raises(ValueError, match="invalid consumer presentation"):
        cli.launch_consumer(tmp_path, port, presenter=lambda _surface: True)
    with pytest.raises(ValueError, match="invalid consumer presentation"):
        bootstrap.open_bootstrap(tmp_path, port, presenter=lambda _surface: True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:9344/ui-login?ticket=canary",
    "http://localhost:9344/ui-login?ticket=canary",
    "http://127.0.0.1.example.test:9344/ui-login?ticket=canary",
    "http://user:password@127.0.0.1:9344/ui-login?ticket=canary",
    "http://127.0.0.1:9344/ui-login?ticket=canary#private",
    "http://127.0.0.1:9344/ui-login?ticket=first&ticket=second",
    "http://127.0.0.1:9344/ui-login?ticket=canary&next=https://example.test",
    "http://127.0.0.1:9344/ui-login?ticket=",
    "http://127.0.0.1:9344/ui-login?ticket=%0Aprivate",
    "http://127.0.0.1:9344/v1/tasks?ticket=canary",
    "file:///tmp/private",
])
def test_app_surface_rejects_unowned_or_ambiguous_routes_without_echoing(url):
    from executor.autonomy.consumer_presentation import ConsumerSurface
    with pytest.raises(ValueError) as blocked:
        ConsumerSurface.from_url("dashboard", url)
    assert str(blocked.value) == "invalid consumer presentation"
    assert url not in str(blocked.value)


def test_app_surface_escapes_opaque_ticket_and_limits_recovery_handoff():
    from executor.autonomy.consumer_presentation import ConsumerSurface
    from urllib.parse import parse_qs, urlsplit
    ticket = "PRIVATE_OPAQUE&redirect=https://example.test/?x=1"
    dashboard = ConsumerSurface.dashboard(9344, ticket)
    assert parse_qs(urlsplit(dashboard.url).query) == {"ticket": [ticket]}
    recovery = ConsumerSurface.from_url(
        "bootstrap", "http://127.0.0.1:19444/?token=PRIVATE_BOOTSTRAP_CANARY",
        service_port=9344)
    assert recovery.allows_navigation(recovery.url)
    assert recovery.allows_navigation("http://127.0.0.1:19444/retry?token=PRIVATE_BOOTSTRAP_CANARY")
    assert recovery.allows_navigation(dashboard.url)
    assert recovery.allows_navigation("http://127.0.0.1:9344/ui")
    assert not recovery.allows_navigation("http://127.0.0.1:19445/ui")
    assert not recovery.allows_navigation("http://127.0.0.1:9344/retry")
    assert not recovery.allows_navigation("http://127.0.0.1:19444/ui")
    assert not recovery.allows_navigation("javascript:alert(1)")
    assert not recovery.allows_navigation("http://127.0.0.1:9344/ui#private")
    assert "PRIVATE" not in repr(recovery)
    assert "PRIVATE" not in json.dumps(recovery.safe_summary())


def test_consumer_launch_routes_through_app_presenter_even_when_model_is_missing(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(cli, "lifecycle", lambda *_args: {"ok": True})
    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **_kwargs: {
        "ready_for_live_e2e": False, "remediation": ["configure_deepseek_key"],
        "final_click_actor": "user", "submit_capability": False})
    monkeypatch.setattr(cli, "request", lambda *_args: {"ticket": "PRIVATE_MISSING_MODEL_TICKET"})
    monkeypatch.setattr(cli.webbrowser, "open",
        lambda *_args, **_kwargs: pytest.fail("missing model must not force external browser"))
    seen = []
    result = cli.launch_consumer(tmp_path, 9344,
                                presenter=lambda surface: seen.append(surface) or True)
    assert result["ok"] is True
    assert result["opened"] is True
    assert result["ready_for_live_e2e"] is False
    assert result["submit_capability"] is False
    assert "DeepSeek" in result["message"]
    assert len(seen) == 1
    assert seen[0].surface == "dashboard"
    assert "PRIVATE_MISSING_MODEL_TICKET" not in json.dumps(result)


def test_actual_independent_bootstrap_can_be_presented_inside_app_and_reused(
        tmp_path, monkeypatch):
    runtime = tmp_path / "app-bootstrap"
    runtime.mkdir()
    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        service_port = reservation.getsockname()[1]
    monkeypatch.setattr(cli.webbrowser, "open",
        lambda *_args, **_kwargs: pytest.fail("app recovery must not open external browser"))
    children = []
    real_popen = subprocess.Popen
    def record_child(command, **kwargs):
        child = real_popen(command, **kwargs)
        if "-I" in command and any("executor.autonomy.cli" in str(part) for part in command):
            children.append(child)
        return child
    monkeypatch.setattr(subprocess, "Popen", record_child)
    seen = []
    lifecycle_calls = []
    actual_lifecycle = cli.lifecycle
    def failed_service(action, _root, _port):
        lifecycle_calls.append(action)
        return {"ok": False, "reason": "service_start_failed"}
    monkeypatch.setattr(cli, "lifecycle", failed_service)
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **_kwargs: {
        "ready_for_live_e2e": False, "remediation": ["start_supervisor"],
        "final_click_actor": "user", "submit_capability": False})
    def app_host(surface):
        assert surface.surface == "bootstrap"
        with urllib.request.urlopen(surface.url, timeout=10) as response:
            assert response.status == 200
            assert "现有任务没有被修改" in response.read().decode()
        seen.append(surface)
        return True
    try:
        first = cli.launch_consumer(runtime, service_port, presenter=app_host)
        second = cli.launch_consumer(runtime, service_port, presenter=app_host)
        assert first == second
        assert first["ok"] is True and first["opened"] is True
        assert first["bootstrap_reason"] == "service_start_failed"
        assert first["ready_for_live_e2e"] is False
        assert first["submit_capability"] is False
        assert lifecycle_calls == ["start", "health", "start", "health"]
        assert len(children) == 1
        assert len(seen) == 2
        assert seen[0].url == seen[1].url
        assert not actual_lifecycle("health", runtime, service_port)["ok"]
        assert not (runtime / "service.json").exists()
        assert not (runtime / "tasks.sqlite3").exists()
        record = json.loads((runtime / "bootstrap.json").read_text())
        assert runtime.joinpath("bootstrap.json").stat().st_mode & 0o077 == 0
        assert record["token"] not in json.dumps([first, second, seen[0].safe_summary()])
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize("scenario", [
    "missing_manifest", "source_tamper", "runtime_tamper", "runtime_alias",
    "stale_restart_refused", "stale_after_restart", "loaded_identity_missing",
    "verified_takeover", "late_healthy", "source_drift",
])
def test_actual_recovery_retry_revalidates_packaged_identity_before_ui_admission(
        tmp_path, monkeypatch, scenario):
    import threading
    from executor.autonomy.queue import TaskQueue

    source = tmp_path / "AI 投递经理.app" / "Contents" / "Resources" / "release"
    source.mkdir(parents=True)
    _minimal_source(source)
    manifest = release.source_manifest(source)
    receipt = source / release.MANIFEST_NAME
    receipt.write_text(json.dumps(manifest))
    expected = manifest["source_sha256"]
    module = source / "executor" / "autonomy" / "cli.py"
    monkeypatch.setattr(cli, "__file__", str(module))
    assert release.read_release_identity(source)["source_sha256"] == expected

    if scenario == "missing_manifest":
        receipt.unlink()
    elif scenario == "source_tamper":
        with module.open("a") as handle:
            handle.write("\n# changed installed source\n")
    elif scenario == "runtime_tamper":
        owned_runtime = source.parent / "runtime"
        python = owned_runtime / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"synthetic-owned-interpreter")
        python.chmod(0o700)
        runtime_manifest = release.runtime_manifest(owned_runtime, source)
        (owned_runtime / release.RUNTIME_MANIFEST_NAME).write_text(json.dumps(runtime_manifest))
        assert release.verify_runtime_candidate(owned_runtime, source)
        with python.open("ab") as handle:
            handle.write(b"changed")
    elif scenario == "runtime_alias":
        outside = tmp_path / "aliased-runtime"
        outside.mkdir()
        (source.parent / "runtime").symlink_to(outside, target_is_directory=True)

    state = tmp_path / "recovery-state"
    queue = TaskQueue(state)
    before = (queue.tasks(), queue.recent_events(1000))
    (state / "private-recovery-canary.txt").write_text("PRIVATE_RECOVERY_CANARY")
    calls = []
    issued = []
    def lifecycle(action, _root, port):
        assert _root == state.resolve() and port == 9344
        calls.append(action)
        if action == "start":
            if scenario == "source_drift":
                with module.open("a") as handle:
                    handle.write("\n# source changed during start\n")
            return {"ok": scenario != "late_healthy", "reason": "health_timeout"}
        if action == "restart":
            return {"ok": scenario != "stale_restart_refused",
                    "reason": "worker_stopping_at_safe_checkpoint"}
        assert action == "health"
        if scenario == "loaded_identity_missing":
            return {"ok": True}
        loaded = expected
        if scenario in {"stale_restart_refused", "stale_after_restart", "verified_takeover"}:
            loaded = expected if scenario == "verified_takeover" and "restart" in calls else "old-release"
        return {"ok": True, "loaded_source_sha256": loaded}
    def issue(_root, port, path, data):
        assert _root == state.resolve() and port == 9344
        assert path == "/v1/ui-ticket" and data == {}
        issued.append(True)
        return {"ticket": "PRIVATE_RECOVERY_TICKET_CANARY"}
    monkeypatch.setattr(cli, "lifecycle", lifecycle)
    monkeypatch.setattr(cli, "request", issue)
    monkeypatch.setattr(bootstrap, "_version", lambda: expected[:12])

    created = threading.Event()
    servers = []
    actual_server = bootstrap.LoopbackHTTPServer
    def record_server(*args, **kwargs):
        server = actual_server(*args, **kwargs)
        servers.append(server)
        created.set()
        return server
    # Observe the actual bound server; state-path admission can precede binding.
    # The real HTTP handshake below still verifies credential publication.
    monkeypatch.setattr(bootstrap, "LoopbackHTTPServer", record_server)
    thread = threading.Thread(target=bootstrap.serve_bootstrap,
        args=(state, 9344, "release_unverified"), daemon=True)
    thread.start()
    try:
        assert created.wait(timeout=5)
        # A real GET executes after atomic credential publication. The server
        # starts serving only after the private state file has been replaced.
        server = servers[0]
        base = f"http://127.0.0.1:{server.server_port}"
        # Do not race the state file: an unauthenticated request is itself a
        # deterministic server readiness handshake, and must remain refused.
        with pytest.raises(urllib.error.HTTPError) as unauthenticated:
            urllib.request.urlopen(base + "/", timeout=5)
        assert unauthenticated.value.code == 403
        record = json.loads((state / "bootstrap.json").read_text())
        url = base + "/retry?token=" + record["token"]
        req = urllib.request.Request(url, data=b"", method="POST",
                                     headers={"Origin": base})
        opener = urllib.request.build_opener(
            type("NoRecoveryRedirect", (urllib.request.HTTPRedirectHandler,),
                 {"redirect_request": lambda self, *args: None})())
        with pytest.raises(urllib.error.HTTPError) as result:
            opener.open(req, timeout=5)
        response = result.value
        allowed = scenario in {"verified_takeover", "late_healthy"}
        assert response.code == (303 if allowed else 503)
        assert issued == ([True] if allowed else [])
        if allowed:
            assert response.headers["Location"] == (
                "http://127.0.0.1:9344/ui-login?ticket=PRIVATE_RECOVERY_TICKET_CANARY")
        else:
            assert "Location" not in response.headers
            body = response.read().decode()
            assert "PRIVATE_RECOVERY_TICKET_CANARY" not in body
            assert "PRIVATE_RECOVERY_CANARY" not in body
            assert "不会提交申请" in body
            assert "现有任务没有被修改" in body
        if scenario in {"missing_manifest", "source_tamper", "runtime_tamper", "runtime_alias"}:
            assert calls == []
        elif scenario in {"stale_restart_refused", "stale_after_restart",
                          "loaded_identity_missing", "verified_takeover"}:
            assert calls == ["start", "health", "restart", "health"]
        else:
            assert calls == ["start", "health"]
        assert before == (queue.tasks(), queue.recent_events(1000))
        assert (state / "private-recovery-canary.txt").read_text() == "PRIVATE_RECOVERY_CANARY"
        assert (state / "bootstrap.json").exists() or allowed
    finally:
        if servers:
            servers[0].shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("value", [True, False, None, 1, "true", {"private": "PRIVATE_CHECK_CANARY"}])
def test_recovery_preparation_only_accepts_fixed_boolean_evidence(monkeypatch, value):
    keys = [item[0] for item in bootstrap._PREPARATION_ITEMS]
    calls = []
    def collect(*, supervisor_running):
        calls.append(supervisor_running)
        return {"ready_for_live_e2e": True,
                "checks": {**{key: value for key in keys},
                           "private_path": "/PRIVATE_PROFILE_CANARY"},
                "remediation": ["PRIVATE_REMEDIATION_CANARY"],
                "provider_error": "PRIVATE_KEY_CANARY"}
    monkeypatch.setattr(preflight, "collect_live_preflight", collect)
    result = bootstrap._preparation_snapshot()
    assert calls == [False]
    assert set(result["checks"]) == set(keys)
    assert result["checks"]["supervisor_running"] is False
    for key in keys[:-1]:
        assert result["checks"][key] is (value if type(value) is bool else None)
    assert result["ready_for_live_e2e"] is False
    assert result["external_connection_verified"] is False
    assert result["settings_mutation_available"] is False
    assert result["submit_capability"] is False
    assert "PRIVATE" not in json.dumps(result)
    markup = bootstrap._preparation_markup(result)
    assert "PRIVATE" not in markup
    assert markup.count('data-check="') == 8


@pytest.mark.parametrize("failure", ["collector_error", "missing_dependency", "malformed_result"])
def test_recovery_preparation_remains_visible_without_business_preflight(monkeypatch, failure):
    import builtins

    if failure == "missing_dependency":
        actual_import = builtins.__import__
        def missing(name, *args, **kwargs):
            if name == "preflight":
                raise ImportError("PRIVATE_DEPENDENCY_PATH_CANARY")
            return actual_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", missing)
    elif failure == "collector_error":
        def failed(**_kwargs):
            raise ValueError("PRIVATE_PROFILE_ERROR_CANARY")
        monkeypatch.setattr(preflight, "collect_live_preflight", failed)
    else:
        monkeypatch.setattr(preflight, "collect_live_preflight",
                            lambda **_kwargs: ["PRIVATE_MALFORMED_CANARY"])
    monkeypatch.setattr(bootstrap, "_version", lambda: "unknown")
    monkeypatch.setattr(bootstrap, "read_release_identity", lambda _root: {"status": "unknown"})
    page = bootstrap._page("service_start_failed", "opaque-recovery-token")
    assert "打开应用前的准备" in page
    assert "未核验" in page
    assert "重试并打开面板" in page
    assert "现有任务没有被修改" in page
    assert "PRIVATE" not in page


def test_recovery_preparation_refuses_contradictory_profile_readiness(monkeypatch):
    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **_kwargs: {
        "checks": {"profile_configured": False, "profile_exists": True,
                   "profile_loadable": True, "supervisor_running": True}})
    result = bootstrap._preparation_snapshot()
    assert result["checks"]["profile_loadable"] is None
    assert result["checks"]["supervisor_running"] is False
    assert result["ready_for_live_e2e"] is False


@pytest.mark.parametrize("profile_case", ["absent", "missing", "unsafe", "valid"])
def test_actual_recovery_preparation_reads_existing_config_without_changing_it(
        tmp_path, monkeypatch, profile_case):
    from executor import settings
    from executor.autonomy import manager

    config = tmp_path / "private-settings.json"
    profile = tmp_path / "private-profile.json"
    payload = {"deepseek": {"enabled": False}, "private_unknown": "PRIVATE_CONFIG_CANARY"}
    if profile_case != "absent":
        payload["profile_path"] = str(profile)
    if profile_case == "unsafe":
        profile.write_text('{"password":"PRIVATE_UNSAFE_PROFILE_CANARY"}')
    elif profile_case == "valid":
        profile.write_text("{}")
    config.write_text(json.dumps(payload))
    config.chmod(0o600)
    if profile.exists():
        profile.chmod(0o600)
    baseline = {p: (p.read_bytes(), p.stat().st_mode) for p in tmp_path.iterdir()}
    monkeypatch.setattr(settings, "PATH", config)
    monkeypatch.setattr(settings, "save_settings",
        lambda *_args: pytest.fail("preparation may not save settings"))
    monkeypatch.setattr(preflight.browser, "browser_mode", lambda: "isolated")
    monkeypatch.setattr(preflight.browser, "CHROME", str(tmp_path / "missing-chrome"))
    monkeypatch.setattr(preflight.browser, "owned_cdp_session", lambda: False)
    monkeypatch.setattr(preflight, "DeepSeekManagerProvider",
        lambda _settings: type("UnavailableExistingProvider", (), {"available": False})())
    monkeypatch.setattr(manager.DeepSeekManagerProvider, "decide",
        lambda *_args: pytest.fail("preparation must not call model"))
    monkeypatch.setattr(cli, "lifecycle",
        lambda *_args: pytest.fail("read-only preparation must not start service"))
    monkeypatch.setattr(cli, "request",
        lambda *_args: pytest.fail("read-only preparation must not request ticket"))
    snapshot = bootstrap._preparation_snapshot()
    checks = snapshot["checks"]
    assert checks["profile_configured"] is (profile_case != "absent")
    assert checks["profile_exists"] is (profile_case in {"unsafe", "valid"})
    assert checks["profile_loadable"] is (profile_case == "valid")
    assert checks["deepseek_available"] is False
    assert snapshot["ready_for_live_e2e"] is False
    assert set(tmp_path.iterdir()) == set(baseline)
    for path, (data, mode) in baseline.items():
        assert path.read_bytes() == data
        assert path.stat().st_mode == mode
    assert str(profile) not in json.dumps(snapshot)
    assert "PRIVATE" not in json.dumps(snapshot)


def test_actual_recovery_preparation_http_auth_refresh_and_private_state_preservation(
        tmp_path, monkeypatch):
    import threading
    from executor.autonomy.queue import TaskQueue

    state = tmp_path / "preparation-state"
    queue = TaskQueue(state)
    baseline_logical = (queue.tasks(), queue.recent_events(1000))
    # Capture the complete private byte oracle after all baseline SELECTs.
    baseline = {path: (path.read_bytes(), path.stat().st_mode)
                for path in state.iterdir() if path.is_file()}
    observations = {"profile_configured": False, "profile_exists": False,
                    "profile_loadable": False, "deepseek_available": False}
    reads = []
    def collect(*, supervisor_running):
        assert supervisor_running is False
        reads.append(True)
        return {"checks": {**observations, "private_path": "PRIVATE_HTTP_CONFIG_CANARY"}}
    monkeypatch.setattr(preflight, "collect_live_preflight", collect)
    monkeypatch.setattr(bootstrap, "_version", lambda: "unknown")
    monkeypatch.setattr(bootstrap, "read_release_identity", lambda _root: {"status": "unknown"})
    monkeypatch.setattr(cli, "_start_consumer_service",
        lambda *_args: pytest.fail("view/refresh may not start service"))
    monkeypatch.setattr(cli, "request",
        lambda *_args: pytest.fail("view/refresh may not issue UI ticket"))
    created = threading.Event()
    servers = []
    actual_server = bootstrap.LoopbackHTTPServer
    def record_server(*args, **kwargs):
        server = actual_server(*args, **kwargs)
        servers.append(server)
        created.set()
        return server
    monkeypatch.setattr(bootstrap, "LoopbackHTTPServer", record_server)
    thread = threading.Thread(target=bootstrap.serve_bootstrap,
        args=(state, 9344, "service_start_failed"), daemon=True)
    thread.start()
    try:
        assert created.wait(timeout=5)
        base = "http://127.0.0.1:" + str(servers[0].server_port)
        with pytest.raises(urllib.error.HTTPError) as unauthenticated:
            urllib.request.urlopen(base + "/", timeout=5)
        assert unauthenticated.value.code == 403
        assert reads == []
        token = json.loads((state / "bootstrap.json").read_text())["token"]
        url = base + "/?token=" + token
        with urllib.request.urlopen(url, timeout=5) as response:
            first = response.read().decode()
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "no-referrer"
        assert 'data-check="profile_configured"' in first
        assert "个人资料尚未配置" in first
        assert "PRIVATE_HTTP_CONFIG_CANARY" not in first
        observations.update(profile_configured=True, profile_exists=True, profile_loadable=True)
        with urllib.request.urlopen(url, timeout=5) as response:
            second = response.read().decode()
        assert '资料可安全读取</strong> · <span class="check-state">可用' in second
        assert "配置可用不代表可以投递" in second
        assert reads == [True, True]
        req = urllib.request.Request(url, headers={"Host": "foreign.test"})
        with pytest.raises(urllib.error.HTTPError) as foreign:
            urllib.request.urlopen(req, timeout=5)
        assert foreign.value.code == 403
        assert reads == [True, True]
        for path, (data, mode) in baseline.items():
            assert path.read_bytes() == data
            assert path.stat().st_mode == mode
        assert baseline_logical == (queue.tasks(), queue.recent_events(1000))
        assert not (state / "service.json").exists()
    finally:
        if servers:
            servers[0].shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("clipboard", ["success", "rejected", "unavailable", "late_success", "late_failure",
                                    "late_rapid_success", "late_programmatic_failure"])
def test_recovery_preparation_browser_keeps_copy_uncertainty_and_closed_page_fenced(
        tmp_path, monkeypatch, clipboard):
    from playwright.sync_api import expect, sync_playwright

    monkeypatch.setattr(preflight, "collect_live_preflight", lambda **_kwargs: {
        "checks": {"profile_configured": False, "deepseek_available": False,
                   "chrome_installed": False},
        "private_exception": "PRIVATE_BROWSER_CONFIG_CANARY"})
    candidate, _runtime, source_identity, runtime_identity = _cold_recovery_payload(tmp_path)
    monkeypatch.setattr(bootstrap, "__file__", str(candidate / "executor" / "autonomy" / "bootstrap.py"))
    monkeypatch.setattr(bootstrap, "_version", lambda: "unknown")
    monkeypatch.setattr(bootstrap, "read_release_identity", lambda _root: {"status": "unknown"})
    page_html = bootstrap._page("service_start_failed", "PRIVATE_RECOVERY_TOKEN")
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 420, "height": 780})
        try:
            def route_request(route):
                requests.append((route.request.method, route.request.url))
                assert route.request.url in {"http://preparation-ui.test/", "http://preparation-ui.test/favicon.ico"}
                if route.request.url.endswith("/favicon.ico"):
                    route.fulfill(status=204, body="")
                else:
                    route.fulfill(status=200, content_type="text/html", body=page_html)
            page.route("**/*", route_request)
            page.goto("http://preparation-ui.test/")
            assert page.locator("#payload-checks li").count() == 3
            assert page.locator("#payload-checks").inner_text().count("已核对") == 2
            assert "当前解释器来自应用 · 未通过" in page.locator("#payload-checks").inner_text()
            assert "不代表业务依赖已成功加载" in page.locator('section[aria-labelledby="payload-title"]').inner_text()
            assert page.locator("#preparation-checks li").count() == 8
            assert "个人资料尚未配置" in page.locator("#preparation-checks").inner_text()
            assert "PRIVATE" not in page.locator("#preparation-checks").inner_text()
            page.evaluate("""mode => {
              window.__copied=[];
              Object.defineProperty(navigator,'clipboard',{configurable:true,value:
                mode==='unavailable'?undefined:{writeText: text=>{
                  window.__copied.push(text);
                  if(mode==='rejected')return Promise.reject(new Error('PRIVATE_CLIPBOARD_ERROR'));
                  if(mode.startsWith('late_'))return new Promise((resolve,reject)=>{
                    window.__finishCopy=()=>mode.endsWith('success')?resolve():reject(new Error('PRIVATE_LATE_ERROR'));
                  });
                  return Promise.resolve();
                }}
              });
            }""", clipboard)
            page.get_by_text("查看安全诊断", exact=True).click()
            report = json.loads(page.locator("#safe-diagnostics").inner_text())
            assert report["source_identity_basis"] == "current_payload_integrity"
            assert report["packaged_release"] == {
                "source_verified_now": True, "source_sha256": source_identity["source_sha256"],
                "runtime_verified_now": True, "runtime_sha256": runtime_identity["runtime_sha256"],
                "requirements_sha256": runtime_identity["requirements_sha256"],
                "interpreter_owned": False, "verification_scope": "payload_integrity_only",
                "signed_distribution_certified": False,
            }
            assert report["preparation"]["ready_for_live_e2e"] is False
            assert report["submit_capability"] is False
            assert "PRIVATE" not in json.dumps(report)
            assert page.evaluate("window.__copied.length") == 0
            page.get_by_role("button", name="复制诊断", exact=True).click()
            if clipboard.startswith("late_"):
                expect(page.locator("#copy-result")).to_have_text("正在复制…")
                if clipboard == "late_rapid_success":
                    page.evaluate("""() => {
                      const details=document.getElementById('diagnostics-details');
                      details.open=false;details.open=true;
                    }""")
                elif clipboard == "late_programmatic_failure":
                    page.evaluate("document.getElementById('diagnostics-details').open=false")
                else:
                    page.get_by_text("查看安全诊断", exact=True).click()
                expect(page.locator("#copy-result")).to_have_text("")
                page.evaluate("window.__finishCopy()")
                if clipboard == "late_programmatic_failure":
                    page.evaluate("document.getElementById('diagnostics-details').open=true")
                elif clipboard != "late_rapid_success":
                    page.get_by_text("查看安全诊断", exact=True).click()
                expect(page.locator("#copy-result")).to_have_text("")
                expect(page.locator("#manual-diagnostics")).to_be_hidden()
                assert page.locator("#manual-diagnostics").input_value() == ""
            elif clipboard == "success":
                expect(page.locator("#copy-result")).to_have_text("已复制安全诊断")
                assert json.loads(page.evaluate("window.__copied[0]")) == report
                expect(page.locator("#manual-diagnostics")).to_be_hidden()
            else:
                expect(page.locator("#copy-result")).to_contain_text("复制结果未确认")
                fallback = page.get_by_role("textbox", name="手动复制安全诊断")
                expect(fallback).to_be_visible()
                expect(fallback).to_be_focused()
                assert json.loads(fallback.input_value()) == report
                assert fallback.get_attribute("readonly") is not None
                page.get_by_text("查看安全诊断", exact=True).click()
                expect(page.locator("#manual-diagnostics")).to_be_hidden()
                assert page.locator("#manual-diagnostics").input_value() == ""
            assert page.evaluate("window.__copied.length") == (0 if clipboard == "unavailable" else 1)
            assert all(method == "GET" for method, _ in requests)
            assert requests.count(("GET", "http://preparation-ui.test/")) == 1
            assert all(url in {"http://preparation-ui.test/", "http://preparation-ui.test/favicon.ico"}
                       for _method, url in requests)
            assert len(page.context.pages) == 1
        finally:
            browser.close()


# Cold-entry regressions exercise production CLI/bootstrap in a fresh isolated
# interpreter. Business imports must never be a prerequisite for recovery.
DEPENDENCY_IMPORT_PROBE = r"""
import importlib.abc
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
blocked = json.loads(sys.argv[2])
attempts = []
class RefuseBusiness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            attempts.append(fullname)
            raise ImportError('PRIVATE_DEPENDENCY_IMPORT_CANARY')
sys.meta_path.insert(0, RefuseBusiness())
from executor.autonomy import cli, bootstrap, runtime_paths
assert not attempts, 'cold recovery import touched business modules'
assert cli.local_token is runtime_paths.local_token
assert bootstrap.private_dir is runtime_paths.private_dir
snapshot = bootstrap._preparation_snapshot()
assert snapshot['ready_for_live_e2e'] is False
assert snapshot['checks']['supervisor_running'] is False
assert all(value is None for key, value in snapshot['checks'].items()
           if key != 'supervisor_running')
assert 'PRIVATE' not in json.dumps(snapshot)
assert 'executor.autonomy.queue' not in sys.modules
assert 'executor.autonomy.worker' not in sys.modules
assert 'executor.autonomy.supervisor' not in sys.modules
print(json.dumps({'ok': True, 'ready': False, 'submit': False}))
"""


@pytest.mark.parametrize("blocked", [
    ["pydantic"], ["playwright"],
    ["executor.browser", "executor.autonomy.queue",
     "executor.autonomy.worker", "executor.autonomy.supervisor"],
    ["executor.autonomy.preflight", "executor.autonomy.manager"],
])
def test_actual_cold_recovery_import_survives_missing_business_dependencies(blocked):
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", DEPENDENCY_IMPORT_PROBE,
         str(Path(cli.__file__).resolve().parents[2]), json.dumps(blocked)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "ready": False, "submit": False}
    assert "PRIVATE" not in result.stdout


def test_runtime_path_and_token_contract_remain_shared_and_fail_closed(
        tmp_path, monkeypatch):
    from executor.autonomy import queue, runtime_paths, supervisor
    monkeypatch.delenv("APPLICATION_EXECUTOR_LOCAL_TOKEN", raising=False)
    assert queue.private_dir is bootstrap.private_dir is runtime_paths.private_dir
    assert queue.default_runtime(tmp_path / "dev-source") == runtime_paths.default_runtime(tmp_path / "dev-source")
    # A basename alone is not an installed app. Keep the production admission
    # predicate and use the complete actual bundle layout for this oracle.
    ordinary_release = tmp_path / "release"
    assert release.is_packaged_source(ordinary_release) is False
    assert runtime_paths.default_runtime(ordinary_release) == ordinary_release / "runtime" / "autonomy"
    packaged_release = tmp_path / "AIApplicationManager.app" / "Contents" / "Resources" / "release"
    packaged_release.mkdir(parents=True)
    assert release.is_packaged_source(packaged_release) is True
    assert runtime_paths.default_runtime(packaged_release, home=tmp_path / "synthetic-home") == (
        tmp_path / "synthetic-home" / "Library" / "Application Support" / "AI投递经理" / "autonomy")
    assert cli.local_token is supervisor.local_token is runtime_paths.local_token
    root = runtime_paths.private_dir(tmp_path / "owned")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    first = runtime_paths.local_token(root)
    token_path = root / "auth.token"
    assert len(first) >= 32
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    before = token_path.read_bytes()
    assert runtime_paths.local_token(root) == first
    assert token_path.read_bytes() == before
    token_path.chmod(0o644)
    with pytest.raises(ValueError, match="private token permissions required"):
        runtime_paths.local_token(root)
    assert token_path.read_bytes() == before
    token_path.unlink()
    target = tmp_path / "foreign-token"
    target.write_text("PRIVATE_FOREIGN_TOKEN_CANARY" * 3)
    target.chmod(0o600)
    token_path.symlink_to(target)
    with pytest.raises(ValueError, match="private token permissions required"):
        runtime_paths.local_token(root)
    assert target.read_text() == "PRIVATE_FOREIGN_TOKEN_CANARY" * 3


@pytest.mark.parametrize("fault", ["import", "collector"])
def test_launch_dependency_recovery_never_admits_service_browser_or_ticket(
        tmp_path, monkeypatch, fault):
    import builtins
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if fault == "import" and name == "preflight":
            raise ImportError("PRIVATE_DEPENDENCY_CANARY")
        return original_import(name, *args, **kwargs)
    if fault == "import":
        monkeypatch.setattr(builtins, "__import__", guarded_import)
    else:
        monkeypatch.setattr(preflight, "collect_live_preflight",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("PRIVATE_COLLECTOR_CANARY")))
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")
    actions = []
    monkeypatch.setattr(cli, "lifecycle",
        lambda action, *_args: actions.append(action) or {"ok": False})
    monkeypatch.setattr(cli, "ensure_chrome",
        lambda: pytest.fail("dependency recovery must not start Chrome"))
    monkeypatch.setattr(cli, "open_ui",
        lambda *_args, **_kwargs: pytest.fail("dependency recovery must not mint UI ticket"))
    recovered = []
    def recovery(root, port, reason, *, presenter=None):
        recovered.append((root, port, reason, presenter))
        return {"ok": True, "opened": True}
    monkeypatch.setattr(bootstrap, "open_bootstrap", recovery)
    presenter = lambda _surface: True
    result = cli.launch_consumer(tmp_path, 9344, presenter=presenter)
    assert recovered == [(tmp_path, 9344, "business_dependencies_unavailable", presenter)]
    assert actions == ([] if fault == "import" else ["start", "health"])
    assert result["ok"] is True and result["opened"] is True
    assert result["ready_for_live_e2e"] is False
    assert result["submit_capability"] is False and result["final_click_actor"] == "user"
    assert result["checks"] == {}
    assert "PRIVATE" not in json.dumps(result)


BROKEN_BUSINESS_LAUNCH_PROBE = r"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
sys.path.insert(0, sys.argv[1])
runtime = Path(sys.argv[2])
service_port = int(sys.argv[3])
children = []
actual_popen = subprocess.Popen
def record_child(command, **kwargs):
    child = actual_popen(command, **kwargs)
    if '-I' in command and 'bootstrap-serve' in command:
        children.append((command, child))
    return child
subprocess.Popen = record_child
seen = []
def owned_presenter(surface):
    assert surface.surface == 'bootstrap'
    parsed = urllib.parse.urlsplit(surface.url)
    assert parsed.hostname == '127.0.0.1'
    with urllib.request.urlopen(surface.url, timeout=5) as response:
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['Referrer-Policy'] == 'no-referrer'
        text = response.read().decode()
    assert '业务运行依赖暂不可用' in text
    assert 'PRIVATE' not in text
    assert '未核验' in text
    # The task API and missing token remain denied on this real HTTP server.
    for url, code in [(surface.url.split('?')[0], 403),
                      (surface.url.replace('/?token=', '/v1/tasks?token='), 404)]:
        try:
            urllib.request.urlopen(url, timeout=5)
            raise AssertionError('unauthorized route accepted')
        except urllib.error.HTTPError as denied:
            assert denied.code == code
    retry_url = surface.url.replace('/?token=', '/retry?token=')
    try:
        urllib.request.urlopen(urllib.request.Request(retry_url, data=b''), timeout=5)
        raise AssertionError('missing Origin accepted')
    except urllib.error.HTTPError as denied:
        assert denied.code == 403
    seen.append(True)
    return True
webbrowser.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError('external browser fallback forbidden'))
try:
    from executor.autonomy import cli
    result = cli.launch_consumer(runtime, service_port, presenter=owned_presenter)
    assert result['ok'] is True and result['opened'] is True
    assert result['ready_for_live_e2e'] is False
    assert result['submit_capability'] is False
    assert result['bootstrap_reason'] == 'business_dependencies_unavailable'
    assert seen == [True]
    assert len(children) == 1
    command, child = children[0]
    assert '-I' in command and '-B' in command and 'bootstrap-serve' in command
    assert 'serve' not in command
    assert not (runtime / 'service.json').exists()
    assert not (runtime / 'auth.token').exists()
    assert 'executor.autonomy.queue' not in sys.modules
    assert 'executor.autonomy.worker' not in sys.modules
    assert 'PRIVATE' not in json.dumps(result)
    print(json.dumps({'ok': True, 'opened': True, 'ready': False,
                      'submit': False, 'owned_children': len(children)}))
finally:
    for _command, child in children:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
"""


@pytest.mark.parametrize("broken_module, fault", [
    ("browser.py", "ImportError"), ("autonomy/queue.py", "ImportError"),
    ("browser.py", "OSError"), ("autonomy/queue.py", "RuntimeError"),
])
def test_actual_launch_and_bootstrap_child_survive_broken_business_source_readonly(
        tmp_path, broken_module, fault):
    import shutil
    from executor.autonomy.queue import TaskQueue, TaskSpec
    source = tmp_path / "copied-owned-source"
    source.mkdir()
    original = Path(cli.__file__).resolve().parents[2]
    shutil.copytree(original / "executor", source / "executor",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Preserve the complete production tree. Inject one business import fault;
    # the actual cold CLI and actual spawned bootstrap modules are unmodified.
    (source / "executor" / broken_module).write_text(
        "raise " + fault + "('PRIVATE_BROKEN_DEPENDENCY_CANARY')\n", encoding="utf-8")
    for relative in ("autonomy/cli.py", "autonomy/bootstrap.py", "autonomy/runtime_paths.py"):
        assert (source / "executor" / relative).read_bytes() == (
            original / "executor" / relative).read_bytes()
    runtime = tmp_path / "private-journal"
    queue = TaskQueue(runtime)
    for index in range(3):
        queue.enqueue(TaskSpec(
            company="Synthetic Consumer " + str(index),
            role="Synthetic Role " + str(index),
            target_url="https://careers.synthetic.test/jobs/" + str(index + 100),
            profile_ref="PRIVATE_PROFILE_CANARY_" + str(index),
            attachment_refs={"resume": "PRIVATE_RESUME_CANARY_" + str(index)},
            live_authorized=False))
    assert len(queue.tasks()) == 3
    assert queue.recent_events(1000)
    before_logical = (queue.tasks(), queue.recent_events(1000))
    key = runtime / "synthetic-private.key"
    key.write_bytes(b"PRIVATE_JOURNAL_KEY_CANARY")
    key.chmod(0o600)
    preserved = [runtime / name for name in (
        "tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm", "synthetic-private.key")]
    before_bytes = {p.name: p.read_bytes() if p.exists() else None for p in preserved}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        service_port = reservation.getsockname()[1]
    env = dict(os.environ, APPLICATION_EXECUTOR_BROWSER_MODE="isolated")
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", BROKEN_BUSINESS_LAUNCH_PROBE,
         str(source), str(runtime), str(service_port)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "ok": True, "opened": True, "ready": False,
        "submit": False, "owned_children": 1}
    assert "PRIVATE" not in completed.stdout
    assert {p.name: p.read_bytes() if p.exists() else None for p in preserved} == before_bytes
    assert (queue.tasks(), queue.recent_events(1000)) == before_logical
    assert not (runtime / "service.json").exists()
    assert not (runtime / "auth.token").exists()


def test_unverified_release_is_refused_before_any_business_import(
        tmp_path, monkeypatch):
    import builtins
    actual_import = builtins.__import__
    def refuse_business(name, *args, **kwargs):
        if name in {"consumer", "preflight", "browser"}:
            pytest.fail("unverified release must not import business runtime")
        return actual_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", refuse_business)
    monkeypatch.setattr(cli, "_consumer_release_identity",
        lambda: {"packaged": True, "expected": ""})
    seen = []
    monkeypatch.setattr(bootstrap, "open_bootstrap",
        lambda root, port, reason: seen.append(reason) or {"ok": True, "opened": True})
    monkeypatch.setattr(cli, "lifecycle",
        lambda *_args: pytest.fail("unverified runtime must not start"))
    monkeypatch.setattr(cli, "open_ui",
        lambda *_args: pytest.fail("unverified runtime must not mint a ticket"))
    result = cli.launch_consumer(tmp_path, 9344)
    assert seen == ["release_unverified"]
    assert result["bootstrap_reason"] == "release_unverified"
    assert result["ready_for_live_e2e"] is False
    assert result["submit_capability"] is False


TRANSACTION_DEPENDENCY_PROBE = r"""
import importlib.abc
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
home, apps, source = map(Path, sys.argv[2:5])
action, busy = sys.argv[5], sys.argv[6] == 'busy'
Path.home = classmethod(lambda cls: home)
attempts = []
class RefuseBusiness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in (
                'pydantic', 'playwright', 'executor.browser',
                'executor.autonomy.queue', 'executor.autonomy.worker',
                'executor.autonomy.supervisor')):
            attempts.append(fullname)
            raise ImportError('PRIVATE_TRANSACTION_DEPENDENCY_CANARY')
sys.meta_path.insert(0, RefuseBusiness())
from executor.autonomy import consumer
if action == 'install':
    result = consumer.install_macos_app(source, destination=apps, platform='darwin')
else:
    result = consumer.rollback_macos_app(apps)
expected = 'task_state_in_use' if busy else (
    'venv_missing' if action == 'install' else 'rollback_unavailable')
assert result['ok'] is False and result['reason'] == expected
assert not attempts, 'transaction admission imported business dependencies'
assert 'PRIVATE' not in json.dumps(result)
assert 'executor.autonomy.queue' not in sys.modules
assert 'executor.autonomy.worker' not in sys.modules
print(json.dumps({'ok': False, 'reason': result['reason']}))
"""


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("busy", [False, True])
def test_cold_app_transaction_admission_remains_available_without_business_imports(
        tmp_path, action, busy):
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.runtime_paths import default_runtime
    from executor.autonomy.state_compatibility import _private_lock_fd
    home = tmp_path / "synthetic-transaction-home"
    apps = tmp_path / "Applications"
    apps.mkdir()
    source = tmp_path / "missing-development-runtime"
    source.mkdir()
    state = default_runtime(
        apps / (consumer.APP_NAME + ".app") / "Contents" / "Resources" / "release",
        home=home)
    queue = TaskQueue(state)
    for index in range(3):
        queue.enqueue(TaskSpec(
            company="Synthetic Transaction " + str(index), role="Synthetic Role",
            target_url="https://careers.synthetic.test/jobs/" + str(index),
            profile_ref="PRIVATE_TRANSACTION_PROFILE_" + str(index),
            attachment_refs={"resume": "PRIVATE_TRANSACTION_RESUME_" + str(index)},
            live_authorized=False))
    before_logical = (queue.tasks(), queue.recent_events(1000))
    assert len(before_logical[0]) == 3 and before_logical[1]
    key = state / "synthetic-private.key"
    key.write_bytes(b"PRIVATE_TRANSACTION_KEY_CANARY")
    key.chmod(0o600)
    for suffix in (".app", ".app.previous", ".app.failed"):
        bundle = apps / ((consumer.APP_NAME if suffix == ".app" else "." + consumer.APP_NAME) + suffix)
        bundle.mkdir()
        (bundle / "preserved-marker").write_bytes(b"PRIVATE_EXISTING_BUNDLE_CANARY")
    bundle_bytes = {str(path.relative_to(apps)): path.read_bytes()
                    for path in apps.rglob("preserved-marker")}
    protected = [state / name for name in (
        "tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm", "synthetic-private.key")]
    before_bytes = {p.name: p.read_bytes() if p.exists() else None for p in protected}
    lease = _private_lock_fd(state / "worker.lock") if busy else None
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", TRANSACTION_DEPENDENCY_PROBE,
             str(Path(cli.__file__).resolve().parents[2]), str(home), str(apps),
             str(source), action, "busy" if busy else "idle"],
            capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "ok": False, "reason": "task_state_in_use" if busy else (
                "venv_missing" if action == "install" else "rollback_unavailable")}
        assert "PRIVATE" not in result.stdout
    finally:
        if lease is not None:
            os.close(lease)
    assert {p.name: p.read_bytes() if p.exists() else None for p in protected} == before_bytes
    assert (queue.tasks(), queue.recent_events(1000)) == before_logical
    assert {str(path.relative_to(apps)): path.read_bytes()
            for path in apps.rglob("preserved-marker")} == bundle_bytes
    assert not (apps / ("." + consumer.APP_NAME + ".app.installing")).exists()
    # The child released both app/state guards; no force unlock or PID signalling.
    descriptor = _private_lock_fd(apps / ("." + consumer.APP_NAME + ".app.transaction.lock"))
    os.close(descriptor)
    descriptor = _private_lock_fd(state / "worker.lock")
    os.close(descriptor)


def test_transaction_default_state_fixture_never_falls_back_to_ambient_home(
        tmp_path, monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy import state_compatibility
    apps = tmp_path / "isolated-app-guard"
    apps.mkdir()
    observed = []
    @contextmanager
    def observe_guard(root):
        observed.append(root)
        yield
    def refuse_ambient_home(_cls):
        pytest.fail("transaction test escaped its synthetic home")
    monkeypatch.setattr(Path, "home", classmethod(refuse_ambient_home))
    monkeypatch.setattr(state_compatibility, "task_state_guard", observe_guard)
    install = consumer.install_macos_app(tmp_path / "no-runtime",
        destination=apps, platform="darwin")
    rollback = consumer.rollback_macos_app(apps)
    assert install["ok"] is False and install["reason"] == "venv_missing"
    assert rollback["ok"] is False and rollback["reason"] == "rollback_unavailable"
    expected = (tmp_path / "synthetic-home" / "Library" / "Application Support"
                / "AI投递经理" / "autonomy")
    assert observed == [expected, expected]
    assert not expected.exists()

# JCR08: installed preferences use the same persistent owned runtime as tasks;
# they must not require/read a checkout or replace a private authority through links.
def test_packaged_settings_path_is_stable_across_versions_without_legacy_fallback(tmp_path):
    from executor import settings
    home = tmp_path / "synthetic-home"
    first = tmp_path / "first" / "AI投递经理.app" / "Contents" / "Resources" / "release"
    second = tmp_path / "second" / "AI投递经理.app" / "Contents" / "Resources" / "release"
    expected = home / "Library" / "Application Support" / "AI投递经理" / "autonomy" / "settings.json"
    assert settings.default_settings_path(first, home=home) == expected
    assert settings.default_settings_path(second, home=home) == expected
    ordinary = tmp_path / "ordinary-source"
    assert settings.default_settings_path(ordinary, home=home) == (
        home / "Job-Application-Executor" / "config" / "settings.json")
    ordinary.mkdir()
    (ordinary / release.MANIFEST_NAME).write_text("{}")
    assert settings.default_settings_path(ordinary, home=home) == expected


def test_actual_packaged_settings_cold_import_uses_owned_home_and_no_business_dependencies(tmp_path):
    root = tmp_path / "AI投递经理.app" / "Contents" / "Resources" / "release"
    package = root / "executor"
    autonomy = package / "autonomy"
    autonomy.mkdir(parents=True)
    actual = Path(__file__).resolve().parents[1] / "executor"
    for relative in ("__init__.py", "settings.py", "autonomy/__init__.py",
                     "autonomy/runtime_paths.py", "autonomy/release.py"):
        (package / relative).write_bytes((actual / relative).read_bytes())
    home = tmp_path / "synthetic-home"
    legacy = home / "Job-Application-Executor" / "config" / "settings.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"profile_path":"PRIVATE_LEGACY_SETTINGS_CANARY"}')
    legacy.chmod(0o600)
    before = legacy.read_bytes(), stat.S_IMODE(legacy.stat().st_mode)
    program = """
import importlib.abc, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
class RejectBusiness(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *_args):
        if name.split('.')[0] in {'pydantic', 'playwright'} or name in {
            'executor.autonomy.queue', 'executor.autonomy.worker',
            'executor.autonomy.manager', 'executor.autonomy.supervisor'}:
            raise AssertionError('business dependency must not be imported')
sys.meta_path.insert(0, RejectBusiness())
from executor import settings
assert settings.PACKAGED is True
assert settings.PATH == Path.home() / 'Library' / 'Application Support' / 'AI投递经理' / 'autonomy' / 'settings.json'
if sys.argv[2] == 'first':
    assert settings.load_settings()['profile_path'] is None
else:
    assert settings.load_settings()['profile_path'] == 'PRIVATE_OWNED_SETTINGS_CANARY'
    assert settings.load_settings()['unknown_owned_preference'] == '完整 👩🏽‍💻' * 1000
payload = {'profile_path': 'PRIVATE_OWNED_SETTINGS_CANARY', 'deepseek': {'enabled': False},
           'unknown_owned_preference': '完整 👩🏽‍💻' * 1000}
settings.save_settings(payload)
assert settings.load_settings() == payload
assert settings.PATH.stat().st_mode & 0o777 == 0o600
print('OWNED_SETTINGS_PASS')
"""
    second = tmp_path / "next" / "AI投递经理.app" / "Contents" / "Resources" / "release"
    for relative in ("__init__.py", "settings.py", "autonomy/__init__.py",
                     "autonomy/runtime_paths.py", "autonomy/release.py"):
        destination = second / "executor" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((actual / relative).read_bytes())
    for source_root, phase in ((root, "first"), (second, "upgrade"), (root, "rollback")):
        result = subprocess.run([sys.executable, "-I", "-B", "-c", program,
                                 str(source_root), phase],
                                env={**os.environ, "HOME": str(home)}, capture_output=True,
                                text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OWNED_SETTINGS_PASS"
        assert "PRIVATE_" not in result.stdout + result.stderr
    assert (legacy.read_bytes(), stat.S_IMODE(legacy.stat().st_mode)) == before


def test_settings_atomic_roundtrip_preserves_complete_values_and_private_modes(tmp_path, monkeypatch):
    from executor import settings
    target = tmp_path / "owned" / "nested" / "settings.json"
    monkeypatch.setattr(settings, "PATH", target)
    monkeypatch.setattr(settings, "PACKAGED", True)
    assert settings.load_settings() == {
        "resume_path": None, "profile_path": None, "auth_wait_seconds": 900}
    payload = {"profile_path": "PRIVATE_PROFILE_CANARY",
               "unknown_owned_preference": {"text": "完整 👩🏽‍💻\n" * 1000},
               "deepseek": {"enabled": False, "keychain_service": "existing-service"}}
    settings.save_settings(payload)
    first = target.read_bytes()
    assert settings.load_settings() == payload
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    settings.save_settings({**payload, "auth_wait_seconds": 300})
    assert target.read_bytes() != first
    assert settings.load_settings()["unknown_owned_preference"] == payload["unknown_owned_preference"]
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("fault", ["file_sync", "atomic_replace"])
def test_settings_write_fault_preserves_previous_complete_authority(tmp_path, monkeypatch, fault):
    from executor import settings
    target = tmp_path / "settings.json"
    target.write_text('{"profile_path":"PRIVATE_PREVIOUS_CANARY"}')
    target.chmod(0o600)
    before = target.read_bytes(), target.stat().st_mode
    monkeypatch.setattr(settings, "PATH", target)
    def refuse(*_args, **_kwargs):
        raise OSError("PRIVATE_WRITE_ERROR_CANARY")
    monkeypatch.setattr(settings.os, "fsync" if fault == "file_sync" else "replace", refuse)
    with pytest.raises(ValueError, match="^settings_unavailable$") as error:
        settings.save_settings({"profile_path": "PRIVATE_NEW_CANARY"})
    assert "PRIVATE_" not in str(error.value)
    assert (target.read_bytes(), target.stat().st_mode) == before
    assert list(tmp_path.iterdir()) == [target]
    assert settings.load_settings() == {"profile_path": "PRIVATE_PREVIOUS_CANARY"}


@pytest.mark.parametrize("shape", ["malformed", "array", "duplicate", "nonfinite", "directory", "symlink", "hardlink"])
def test_settings_refuses_ambiguous_or_aliased_read_authority_without_value_echo(tmp_path, monkeypatch, shape):
    from executor import settings
    target = tmp_path / "settings.json"
    original = tmp_path / "PRIVATE_OTHER_SETTINGS_CANARY"
    original.write_text('{"profile_path":"PRIVATE_OTHER_VALUE_CANARY"}')
    original.chmod(0o600)
    before = original.read_bytes(), original.stat().st_mode
    payloads = {
        "malformed": '{"profile_path":"PRIVATE_PARSE_CANARY"',
        "array": '["PRIVATE_ARRAY_CANARY"]',
        "duplicate": '{"profile_path":"first","profile_path":"PRIVATE_DUPLICATE_CANARY"}',
        "nonfinite": '{"wait":NaN,"private":"PRIVATE_NAN_CANARY"}',
    }
    if shape in payloads:
        target.write_text(payloads[shape])
        target.chmod(0o600)
    elif shape == "directory":
        target.mkdir()
    elif shape == "symlink":
        target.symlink_to(original)
    else:
        os.link(original, target)
    monkeypatch.setattr(settings, "PATH", target)
    with pytest.raises(ValueError, match="^settings_unavailable$") as error:
        settings.load_settings()
    assert "PRIVATE_" not in str(error.value)
    assert (original.read_bytes(), original.stat().st_mode) == before


@pytest.mark.parametrize("alias", ["ancestor", "leaf", "hardlink", "directory"])
def test_settings_save_refuses_alias_and_keeps_outside_authority_unchanged(tmp_path, monkeypatch, alias):
    from executor import settings
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "settings.json"
    original.write_text('{"profile_path":"PRIVATE_OUTSIDE_CANARY"}')
    original.chmod(0o600)
    if alias == "ancestor":
        parent = tmp_path / "alias"
        parent.symlink_to(outside, target_is_directory=True)
        target = parent / "settings.json"
    else:
        parent = tmp_path / "owned"
        parent.mkdir(mode=0o700)
        target = parent / "settings.json"
        if alias == "leaf":
            target.symlink_to(original)
        elif alias == "hardlink":
            os.link(original, target)
        else:
            target.mkdir()
    before = original.read_bytes(), original.stat().st_mode
    monkeypatch.setattr(settings, "PATH", target)
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        settings.save_settings({"profile_path": "PRIVATE_REPLACEMENT_CANARY"})
    assert (original.read_bytes(), original.stat().st_mode) == before
    assert not list(parent.glob(".settings-*.tmp"))


def test_packaged_settings_refuses_public_permissions_and_does_not_chmod_private_input(tmp_path, monkeypatch):
    from executor import settings
    parent = tmp_path / "owned"
    parent.mkdir(mode=0o700)
    target = parent / "settings.json"
    target.write_text('{"profile_path":"PRIVATE_PERMISSIONS_CANARY"}')
    target.chmod(0o644)
    monkeypatch.setattr(settings, "PATH", target)
    monkeypatch.setattr(settings, "PACKAGED", True)
    before = target.read_bytes(), target.stat().st_mode
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        settings.load_settings()
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        settings.save_settings({"profile_path": None})
    assert (target.read_bytes(), target.stat().st_mode) == before
    target.chmod(0o600)
    parent.chmod(0o755)
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        settings.load_settings()
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


@pytest.mark.parametrize("payload", [[], {"wait": float("nan")}, {"value": object()}, {1: "first", "1": "duplicate"}])
def test_settings_invalid_save_does_not_create_a_second_authority(tmp_path, monkeypatch, payload):
    from executor import settings
    target = tmp_path / "absent" / "settings.json"
    monkeypatch.setattr(settings, "PATH", target)
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        settings.save_settings(payload)
    assert not target.parent.exists()


def _profile_settings(tmp_path, monkeypatch):
    from executor import settings
    from executor.autonomy import profile_setup
    parent = tmp_path / "owned-profile-settings"
    parent.mkdir(mode=0o700)
    monkeypatch.setattr(settings, "PATH", parent / "settings.json")
    monkeypatch.setattr(settings, "PACKAGED", True)
    original = {"profile_path": None, "deepseek": {"enabled": False, "keychain_service": "original"},
                "unknown_preference": {"text": "完整 👩🏽‍💻\\n" * 1000}}
    settings.save_settings(original)
    return settings, profile_setup, original


def test_profile_setup_complete_separate_versions_and_future_task_authority(tmp_path, monkeypatch):
    settings, setup, original = _profile_settings(tmp_path, monkeypatch)
    profile = {"fields": {"identity.full_name": {"value": "PRIVATE_NAME", "user_confirmed": False}},
               "research": [{"description": "完整研究 👩🏽‍💻\n" * 1000}],
               "unrecognized_evidence": {"original": "<img src=x onerror=alert(1)>"}}
    before = setup.profile_setup_state()
    selected = setup.select_profile(json.dumps(profile, ensure_ascii=False), before["settings_version"])
    first = Path(selected["profile_path"])
    assert first.parent == settings.PATH.parent
    assert json.loads(first.read_text()) == profile
    assert first.stat().st_mode & 0o777 == 0o600
    assert selected == {**original, "profile_path": str(first)}
    old_bytes = first.read_bytes()
    updated_profile = {**profile, "future_only": "PRIVATE_NEW_VALUE"}
    selected = setup.select_profile(json.dumps(updated_profile), settings.settings_version(selected))
    second = Path(selected["profile_path"])
    assert first != second and first.read_bytes() == old_bytes
    assert json.loads(second.read_text()) == updated_profile
    # A task retaining first's profile_ref still reads its complete original data.
    from executor.profile import load_profile
    assert load_profile(first) == profile
    before = {p.name: p.read_bytes() for p in first.parent.iterdir()}
    third = setup.select_profile(json.dumps(updated_profile), settings.settings_version(selected))
    assert Path(third["profile_path"]) not in (first, second)
    assert first.read_bytes() == before[first.name]
    assert second.read_bytes() == before[second.name]
    assert json.loads(Path(third["profile_path"]).read_text()) == updated_profile
    public = json.dumps(setup.profile_setup_state())
    assert "PRIVATE_" not in public and str(first.parent) not in public
    assert setup.profile_setup_state()["submit_capability"] is False


@pytest.mark.parametrize("text", [
    '[]', '{}', '{"x":NaN}', '{"x":1,"x":2}', '{"x":{"y":1,"y":2}}',
    '{"password":"PRIVATE_PASSWORD"}', '{"rows":[{"otp":"PRIVATE_OTP"}]}',
    '{"fields":{"auth.token":"PRIVATE_TOKEN"}}', '{" API_KEY ":"PRIVATE_KEY"}',
    '{"x":"PRIVATE_PARSE"', '{"value":"' + 'x' * (256 * 1024) + '"}',
    '{"x":"\\ud800"}',
])
def test_profile_setup_rejects_complete_invalid_file_before_any_write(tmp_path, monkeypatch, text):
    settings, setup, _ = _profile_settings(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()}
    with pytest.raises(ValueError, match="^invalid_profile$"):
        setup.select_profile(text, setup.profile_setup_state()["settings_version"])
    assert {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()} == before


def test_profile_setup_stale_window_never_overwrites_preferences_or_creates_version(tmp_path, monkeypatch):
    settings, setup, original = _profile_settings(tmp_path, monkeypatch)
    version = setup.profile_setup_state()["settings_version"]
    settings.save_settings({**original, "changed_in_other_window": "PRIVATE_OTHER"})
    before = {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()}
    with pytest.raises(settings.SettingsConflict, match="^settings_changed$"):
        setup.select_profile('{"name":"PRIVATE_STALE"}', version)
    assert {p.name: p.read_bytes() for p in settings.PATH.parent.iterdir()} == before


def test_profile_setup_concurrent_select_has_one_complete_winner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    settings, setup, original = _profile_settings(tmp_path, monkeypatch)
    version = setup.profile_setup_state()["settings_version"]
    barrier = threading.Barrier(2)

    def select(value):
        barrier.wait(timeout=5)
        try:
            return setup.select_profile(json.dumps({"name": value}), version)
        except settings.SettingsConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(select, ["PRIVATE_A", "PRIVATE_B"]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert settings.load_settings() == winners[0]
    profiles = list(settings.PATH.parent.glob("profile-*.json"))
    assert profiles == [Path(winners[0]["profile_path"])]
    assert json.loads(profiles[0].read_text())["name"] in {"PRIVATE_A", "PRIVATE_B"}
    assert settings.load_settings()["unknown_preference"] == original["unknown_preference"]


@pytest.mark.parametrize("fault", ["profile_sync", "settings_replace"])
def test_profile_setup_publication_fault_never_activates_partial_profile(tmp_path, monkeypatch, fault):
    settings, setup, _ = _profile_settings(tmp_path, monkeypatch)
    before = settings.PATH.read_bytes()

    def refuse(*_args, **_kwargs):
        raise OSError("PRIVATE_FAULT_VALUE")

    monkeypatch.setattr(settings.os, "fsync" if fault == "profile_sync" else "replace", refuse)
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        setup.select_profile('{"name":"PRIVATE_NEW"}', setup.profile_setup_state()["settings_version"])
    assert settings.PATH.read_bytes() == before
    assert settings.load_settings()["profile_path"] is None
    profiles = list(settings.PATH.parent.glob("profile-*.json"))
    if fault == "profile_sync":
        assert profiles == []
    else:
        assert len(profiles) == 1 and json.loads(profiles[0].read_text()) == {"name": "PRIVATE_NEW"}
    assert not list(settings.PATH.parent.glob(".settings-*.tmp"))


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "directory", "public", "corrupt"])
def test_profile_setup_refuses_version_name_collision_without_overwriting(tmp_path, monkeypatch, alias):
    settings, setup, _ = _profile_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(setup.secrets, "token_hex", lambda *_: "a" * 32)
    encoded = setup._profile_bytes('{"name":"PRIVATE_SYNTHETIC"}')
    target = settings.PATH.parent / ("profile-" + "a" * 32 + ".json")
    outside = tmp_path / "PRIVATE_OUTSIDE"
    outside.write_bytes(encoded)
    outside.chmod(0o600)
    if alias == "symlink":
        target.symlink_to(outside)
    elif alias == "hardlink":
        os.link(outside, target)
    elif alias == "directory":
        target.mkdir()
    else:
        target.write_bytes(encoded if alias == "public" else b'{"corrupt":"PRIVATE"}')
        target.chmod(0o644 if alias == "public" else 0o600)
    before = settings.PATH.read_bytes(), outside.read_bytes(), target.lstat().st_mode
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        setup.select_profile('{"name":"PRIVATE_SYNTHETIC"}', setup.profile_setup_state()["settings_version"])
    assert (settings.PATH.read_bytes(), outside.read_bytes(), target.lstat().st_mode) == before


def test_profile_setup_refuses_public_parent_without_chmod(tmp_path, monkeypatch):
    settings, setup, _ = _profile_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "PACKAGED", False)
    settings.PATH.parent.chmod(0o755)
    before = settings.PATH.read_bytes()
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        setup.select_profile('{"name":"PRIVATE"}', setup.profile_setup_state()["settings_version"])
    assert settings.PATH.read_bytes() == before
    assert settings.PATH.parent.stat().st_mode & 0o777 == 0o755


# These owning cold-entry cases copy every production Python source and the full
# dependency lock. The runtime is deliberately never executable by diagnostics:
# its real hashes are payload evidence, not proof that business SDKs loaded.
def _cold_recovery_payload(tmp_path):
    candidate = tmp_path / "Recovery Manager.app" / "Contents" / "Resources" / "release"
    source_identity = release.copy_source_candidate(Path(cli.__file__).resolve().parents[2], candidate)
    runtime = candidate.parent / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    (runtime / "bin" / "python").chmod(0o700)
    (runtime / "dependency-canary.py").write_text("VALUE = 'owned synthetic payload'\n", encoding="utf-8")
    runtime_identity = release.runtime_manifest(runtime, candidate)
    (runtime / release.RUNTIME_MANIFEST_NAME).write_text(json.dumps(runtime_identity), encoding="utf-8")
    return candidate, runtime, source_identity, runtime_identity


COLD_PAYLOAD_PROBE = r"""
import importlib.abc
import json
import os
import socket
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path
candidate = Path(sys.argv[1])
private = Path(sys.argv[2]).resolve()
expected = json.loads(sys.argv[3])
sys.path.insert(0, str(candidate))
attempts = []
blocked = ('pydantic', 'playwright', 'cryptography', 'executor.browser',
           'executor.resolver', 'executor.settings', 'executor.profile',
           'executor.autonomy.diagnostics', 'executor.autonomy.preflight',
           'executor.autonomy.manager', 'executor.autonomy.queue',
           'executor.autonomy.worker', 'executor.autonomy.supervisor')
class RefuseBusiness(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            attempts.append(fullname)
            raise ImportError('PRIVATE_DEPENDENCY_PAYLOAD_CANARY')
sys.meta_path.insert(0, RefuseBusiness())
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).absolute()
        assert not path.is_relative_to(private), 'recovery may not read applicant files'
    assert event not in ('subprocess.Popen', 'os.system', 'socket.connect',
                         'socket.bind'), 'payload diagnostics must not start external work'
sys.addaudithook(audit)
from executor.autonomy import bootstrap, runtime_provenance
assert attempts == [], 'cold payload import must not require business dependencies'
assert bootstrap.packaged_provenance is runtime_provenance.packaged_provenance
def forbidden(*args, **kwargs):
    raise AssertionError('payload diagnostics must never execute, browse or connect')
subprocess.run = subprocess.Popen = os.system = forbidden
urllib.request.urlopen = webbrowser.open = socket.getfqdn = forbidden
version = bootstrap._version()
report = bootstrap._safe_diagnostics('business_dependencies_unavailable', version)
payload = report['packaged_release']
assert payload == expected
assert report['source_identity_basis'] == 'current_payload_integrity'
assert report['loaded_source_verified'] is expected['source_verified_now']
assert report['loaded_source_sha256'] == expected['source_sha256']
assert report['loaded_version'] == (expected['source_sha256'][:12] or 'unknown')
assert report['submit_capability'] is False and report['final_click_actor'] == 'user'
assert report['applicant_values_in_report'] is False
page = bootstrap._page('business_dependencies_unavailable', 'opaque-token')
assert '应用运行环境' in page and '重试并打开面板' in page
assert '不代表业务依赖已成功加载、服务可用或发布签名已认证' in page
assert 'PRIVATE_' not in page and str(private) not in page and str(candidate) not in page
assert attempts == ['executor.autonomy.preflight']
for name in blocked:
    assert name not in sys.modules
assert payload['verification_scope'] == 'payload_integrity_only'
assert payload['signed_distribution_certified'] is False
print(json.dumps({'ok': True, 'source': payload['source_verified_now'],
                  'runtime': payload['runtime_verified_now'], 'submit': False}))
"""


@pytest.mark.parametrize("damage", [
    "none", "source_changed", "source_missing", "runtime_changed", "runtime_missing",
    "source_fifo", "runtime_fifo", "source_directory", "runtime_directory", "runtime_alias",
])
def test_actual_cold_recovery_payload_survives_missing_sdks_and_rejects_drift(
        tmp_path, damage):
    candidate, runtime, source_identity, runtime_identity = _cold_recovery_payload(tmp_path)
    private = tmp_path / "PRIVATE_APPLICANT_AUTHORITY"
    private.mkdir(mode=0o700)
    for name, text in {
        "profile.json": json.dumps({"complete_research": "PRIVATE_FULL_资料\n" * 1000}),
        "settings.json": json.dumps({"private_preference": "PRIVATE_FULL_偏好\n" * 1000}),
        "tasks.json": json.dumps({"tasks": [{"task": i, "original": "PRIVATE_TASK"} for i in range(213)]}),
        "events.json": json.dumps({"events": [{"sequence": i, "original": "PRIVATE_EVENT"} for i in range(1000)]}),
    }.items():
        (private / name).write_text(text, encoding="utf-8")
        (private / name).chmod(0o600)
    if damage == "source_changed":
        module = candidate / "executor" / "autonomy" / "consumer_presentation.py"
        module.write_text(module.read_text() + "\n# PRIVATE_SOURCE_DRIFT_CANARY\n", encoding="utf-8")
    elif damage == "runtime_changed":
        (runtime / "dependency-canary.py").write_text("PRIVATE_RUNTIME_DRIFT_CANARY", encoding="utf-8")
    elif damage in {"source_missing", "source_fifo", "source_directory"}:
        manifest = candidate / release.MANIFEST_NAME
        manifest.unlink()
        if damage == "source_fifo":
            os.mkfifo(manifest, 0o600)
        elif damage == "source_directory":
            manifest.mkdir()
    elif damage in {"runtime_missing", "runtime_fifo", "runtime_directory"}:
        manifest = runtime / release.RUNTIME_MANIFEST_NAME
        manifest.unlink()
        if damage == "runtime_fifo":
            os.mkfifo(manifest, 0o600)
        elif damage == "runtime_directory":
            manifest.mkdir()
    elif damage == "runtime_alias":
        actual = runtime.with_name("PRIVATE_RUNTIME_ALIAS")
        runtime.rename(actual)
        runtime.symlink_to(actual, target_is_directory=True)
    source_ok = not damage.startswith("source_")
    runtime_ok = damage == "none"
    expected = {
        "source_verified_now": source_ok,
        "source_sha256": source_identity["source_sha256"] if source_ok else "",
        "runtime_verified_now": runtime_ok,
        "runtime_sha256": runtime_identity["runtime_sha256"] if runtime_ok else "",
        "requirements_sha256": runtime_identity["requirements_sha256"] if runtime_ok else "",
        "interpreter_owned": False, "verification_scope": "payload_integrity_only",
        "signed_distribution_certified": False,
    }
    def snapshot():
        return {str(path.relative_to(tmp_path)):
                (path.lstat().st_mode, path.readlink().as_posix() if path.is_symlink()
                 else path.read_bytes() if path.is_file() else None)
                for path in tmp_path.rglob("*")}
    before = snapshot()
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", COLD_PAYLOAD_PROBE,
         str(candidate), str(private), json.dumps(expected)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "ok": True, "source": source_ok, "runtime": runtime_ok, "submit": False}
    assert "PRIVATE_" not in result.stdout
    assert snapshot() == before

def _identity_transaction_fixture(tmp_path, *, rollback):
    import shutil
    from executor.autonomy.state_compatibility import task_state_guard

    actual = Path(__file__).resolve().parents[1]
    repo = tmp_path / "Job-Application-Executor"
    repo.mkdir()
    shutil.copytree(actual / "executor", repo / "executor",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(actual / "requirements.txt", repo / "requirements.txt")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    apps = tmp_path / "Applications"
    empty = tmp_path / "empty-build-state"
    assert install_macos_app(repo, destination=apps, platform="darwin",
                             task_state_root=empty)["ok"]
    with (repo / "executor" / "__init__.py").open("a") as handle:
        handle.write("\n# Distinct complete next transaction candidate.\n")
    if rollback:
        assert install_macos_app(repo, destination=apps, platform="darwin",
                                 task_state_root=empty)["ok"]
    from test_task_state_compatibility_v1 import legacy_state, encrypted_answers
    state = tmp_path / "private-task-authority"
    db = legacy_state(state)
    key = encrypted_answers(state, db)
    with task_state_guard(state):
        pass
    (state / "profile.json").write_text(json.dumps({
        "full": "\n".join(f"{i}: PRIVATE_IDENTITY_PROFILE_中文_<literal>"
                          for i in range(1000))}, ensure_ascii=False))
    (state / "preferences.json").write_text(json.dumps({
        "full": ["PRIVATE_IDENTITY_SETTINGS_" + str(i) for i in range(1000)]}))
    return repo, apps, state, db, key


def _identity_file_inventory(root):
    # Exact bytes and modes, including complete source/runtime payloads and WAL.
    return {path.relative_to(root).as_posix():
            (stat.S_IMODE(path.stat().st_mode), path.read_bytes())
            for path in root.rglob("*") if path.is_file()}


def _reseal_changed_source(bundle):
    source = bundle / "Contents" / "Resources" / "release"
    with (source / "executor" / "__init__.py").open("a") as handle:
        handle.write("\n# PRIVATE_RESEALED_DIFFERENT_RELEASE\n")
    (source / "release-source-manifest.json").write_text(
        json.dumps(release.source_manifest(source), sort_keys=True) + "\n")
    # The attack is another internally consistent release, not merely corrupt
    # bytes. A fresh integrity/health pass must not authorize a different SHA.
    assert verify_source_candidate(source)
    assert consumer._trusted_bundle(bundle)


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("phase", ["health", "journal"])
@pytest.mark.parametrize("changed_role", ["candidate", "current"])
def test_release_transaction_refuses_resealed_identity_after_health_or_journal(
    tmp_path, monkeypatch, action, phase, changed_role
):
    from contextlib import closing
    from test_task_state_compatibility_v1 import legacy_state, authority, encrypted_answers
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    candidate = apps / ("." + consumer.APP_NAME + (
        ".app.previous" if action == "rollback" else ".app.installing"))
    original_inode = app.stat().st_ino
    previous_inode = candidate.stat().st_ino if action == "rollback" else None
    changed = []
    after_mutation = {}
    actual_start = consumer._candidate_starts
    actual_prepare = consumer._prepare_task_state_release

    def mutate():
        target = candidate if changed_role == "candidate" else app
        _reseal_changed_source(target)
        changed.append(target)
        after_mutation["current"] = _identity_file_inventory(app)
        after_mutation["candidate"] = _identity_file_inventory(candidate)

    def start(python, source):
        result = actual_start(python, source)
        assert result
        if phase == "health" and not changed:
            mutate()
        return result

    def prepare(*args):
        result = actual_prepare(*args)
        assert result
        if phase == "journal":
            mutate()
        return result

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        monkeypatch.setattr(consumer, "_prepare_task_state_release", prepare)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert len(changed) == 1
        assert result["ok"] is False
        assert result["reason"] == ("rollback_identity_changed" if action == "rollback"
                                    else "activation_identity_changed")
        # Both payloads remain exactly where they were, including the changed
        # candidate. No cleanup deletes evidence or replays a private task.
        assert app.stat().st_ino == original_inode
        if previous_inode is not None:
            assert candidate.stat().st_ino == previous_inode
        assert _identity_file_inventory(app) == after_mutation["current"]
        assert _identity_file_inventory(candidate) == after_mutation["candidate"]
        assert not (apps / ("." + consumer.APP_NAME + ".app.failed")).exists()
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("changed_payload", ["source", "launcher"])
def test_release_transaction_never_certifies_payload_changed_during_final_health(
    tmp_path, monkeypatch, action, changed_payload
):
    repo, apps, state, db, key = _identity_transaction_fixture(tmp_path, rollback=action == "rollback")
    from contextlib import closing
    with closing(db):
        app = apps / (consumer.APP_NAME + ".app")
        current_before = _identity_file_inventory(app)
        current_inode = app.stat().st_ino
        candidate = apps / ("." + consumer.APP_NAME + (
            ".app.previous" if action == "rollback" else ".app.installing"))
        actual_start = consumer._candidate_starts
        starts = []
        changed_identity = {}

        def start(python, source):
            result = actual_start(python, source)
            assert result
            starts.append(source)
            if len(starts) == 2:
                active = source.parents[2]
                assert active == app
                if changed_payload == "source":
                    _reseal_changed_source(active)
                else:
                    launcher = active / "Contents" / "MacOS" / "AIApplicationManager"
                    launcher.write_text("PRIVATE_CHANGED_LAUNCHER\n")
                changed_identity.update(_identity_file_inventory(active))
            return result

        monkeypatch.setattr(consumer, "_candidate_starts", start)
        from test_task_state_compatibility_v1 import authority
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert result["ok"] is False
        assert result["reason"] == ("rollback_post_activation_unhealthy" if action == "rollback"
                                    else "post_activation_unhealthy")
        assert len(starts) == 3
        assert app.stat().st_ino == current_inode
        assert _identity_file_inventory(app) == current_before
        retained = (candidate if action == "rollback"
                    else apps / ("." + consumer.APP_NAME + ".app.failed"))
        assert _identity_file_inventory(retained) == changed_identity
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()


@pytest.fixture
def retirement_private_state(tmp_path):
    """Actual migrated WAL and typed encrypted answers; never a live account."""
    from contextlib import closing
    from test_task_state_compatibility_v1 import legacy_state, encrypted_answers
    from executor.autonomy.queue import TaskQueue, TaskSpec
    root = tmp_path / "retirement-private-authority"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        queue = TaskQueue(root)
        for name, value in {
            "profile.json": {"complete": "\n".join(
                f"{i}: PRIVATE_RETIRE_PROFILE_中文_<literal>" for i in range(1000))},
            "preferences.json": {"complete": [
                f"{i}: PRIVATE_RETIRE_PREF_中文_<literal>" for i in range(1000)]},
        }.items():
            path = root / name
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            path.chmod(0o600)
        # A real daemon must read a complete production TaskSpec even for a
        # BLOCKED/UNKNOWN task. Preserve the legacy authority, not a malformed
        # minimal spec that crashes the worker before the retirement handshake.
        spec = TaskSpec(company="Synthetic retirement", role="Synthetic role",
                        target_url="https://careers.synthetic.test/jobs/retirement",
                        profile_ref=str(root / "profile.json"), live_authorized=False)
        db.execute("UPDATE tasks SET spec=? WHERE task_id=?",
                   (spec.model_dump_json(), "synthetic-task"))
        db.execute("INSERT INTO run_attempts VALUES(?,?,?,?,?,?)",
                   ("synthetic-unknown-attempt", "synthetic-task", "synthetic-old-owner",
                    "UNKNOWN_OUTCOME", 1, 2))
        db.execute("INSERT INTO field_actions VALUES(?,?,?,?,?,?)",
                   ("synthetic-unknown-field", "synthetic-unknown-attempt", "0" * 64,
                    "UNKNOWN_OUTCOME", 1, 2))
        db.commit()
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        yield root, queue, db, key


def _retirement_authority(root, db):
    from test_task_state_compatibility_v1 import authority
    return (authority(db), db.execute("SELECT * FROM task_answer_events").fetchall(),
            db.execute("SELECT * FROM run_attempts").fetchall(),
            db.execute("SELECT * FROM field_actions").fetchall(),
            tuple((name, (root / name).read_bytes(),
                   stat.S_IMODE((root / name).stat().st_mode))
                  for name in ("profile.json", "preferences.json", "task-answers.key")))


def _retirement_inventory(root):
    # Nonblocking inventory of all ordinary bytes, modes and aliases; never
    # read a FIFO/device or follow an ambiguous registry into another root.
    result = {}
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        value = (path.readlink().as_posix() if stat.S_ISLNK(mode)
                 else path.read_bytes() if stat.S_ISREG(mode) else None)
        result[path.relative_to(root).as_posix()] = (mode, value)
    return result


def _assert_retirement_inventory(root, expected):
    actual = _retirement_inventory(root)
    # Compare every complete raw byte and mode. Avoid pytest rendering entire
    # applicant/WAL/ciphertext values on failure; only changed relative paths
    # belong in the bounded synthetic diagnosis.
    identical = actual == expected
    changed = sorted(name for name in set(actual) | set(expected)
                     if actual.get(name) != expected.get(name))
    assert identical, "complete raw inventory changed: " + ", ".join(changed)


def _assert_retirement_authority(root, db, expected):
    identical = _retirement_authority(root, db) == expected
    assert identical, "complete task/events/UNKNOWN attempts/encrypted answers/key/profile/preferences changed"


def _retirement_registry(root, record=None):
    record = record if record is not None else {
        "pid": os.getpid(), "port": 9344, "instance": "A" * 43}
    path = root / "service.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    path.chmod(0o600)
    return path, record


@pytest.mark.parametrize("defect", [
    "legacy", "bool_pid", "float_pid", "string_port", "bool_port",
    "extra_key", "short_instance", "unicode_instance", "invalid_instance",
    "malformed", "list", "oversized", "public", "symlink", "hardlink",
    "fifo", "directory", "wrong_port", "bool_requested_port",
])
def test_service_retirement_refuses_unowned_registry_without_pid_signals(
    retirement_private_state, tmp_path, monkeypatch, defect
):
    root, queue, db, key = retirement_private_state
    registry, record = _retirement_registry(root)
    requested_port = 9344
    if defect == "legacy":
        record.pop("instance")
    elif defect == "bool_pid":
        record["pid"] = True
    elif defect == "float_pid":
        record["pid"] = float(record["pid"])
    elif defect == "string_port":
        record["port"] = "9344"
    elif defect == "bool_port":
        record["port"] = True
    elif defect == "extra_key":
        record["hint"] = "PRIVATE_REGISTRY_HINT"
    elif defect == "short_instance":
        record["instance"] = "A"
    elif defect == "unicode_instance":
        record["instance"] = "私" * 43
    elif defect == "invalid_instance":
        record["instance"] = "/" * 43
    elif defect == "wrong_port":
        requested_port = 9345
    elif defect == "bool_requested_port":
        requested_port = True
    registry.write_text(json.dumps(record), encoding="utf-8")
    if defect == "malformed":
        registry.write_text("{PRIVATE_MALFORMED")
    elif defect == "list":
        registry.write_text(json.dumps([record]))
    elif defect == "oversized":
        registry.write_text(" " * 65537 + json.dumps(record))
    elif defect == "public":
        registry.chmod(0o644)
    elif defect == "symlink":
        outside = tmp_path / "outside-service.json"
        outside.write_bytes(registry.read_bytes())
        registry.unlink()
        registry.symlink_to(outside)
    elif defect == "hardlink":
        os.link(registry, tmp_path / "outside-service.json")
    elif defect == "fifo":
        registry.unlink()
        os.mkfifo(registry, 0o600)
    elif defect == "directory":
        registry.unlink()
        registry.mkdir(mode=0o700)
    # SQL inspection may update SQLite SHM read marks. Complete the logical
    # oracle first; no fixture SELECT may occur between the two raw snapshots.
    authority_before = _retirement_authority(root, db)
    before = _retirement_inventory(tmp_path)

    def forbidden(*_args, **_kwargs):
        pytest.fail("unowned registry must neither inspect/signal a PID nor contact a service")
    monkeypatch.setattr(cli, "request", forbidden)
    monkeypatch.setattr(cli.os, "kill", forbidden)
    monkeypatch.setattr(cli.subprocess, "run", forbidden)
    result = cli.lifecycle("stop", root, requested_port)
    assert result == {"ok": False, "reason": "service_identity_unverified"}
    _assert_retirement_inventory(tmp_path, before)
    _assert_retirement_authority(root, db, authority_before)
    assert (root / "task-answers.key").read_bytes() == key
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("defect", [
    "legacy", "bool_pid", "float_pid", "string_port", "wrong_port",
    "wrong_instance", "extra_key", "registry_replaced",
])
def test_service_retirement_requires_exact_typed_authenticated_readback(
    retirement_private_state, monkeypatch, defect
):
    root, queue, db, key = retirement_private_state
    registry, record = _retirement_registry(root)
    observed = dict(record)
    if defect == "legacy":
        observed.pop("instance")
    elif defect == "bool_pid":
        observed["pid"] = True
    elif defect == "float_pid":
        observed["pid"] = float(record["pid"])
    elif defect == "string_port":
        observed["port"] = str(record["port"])
    elif defect == "wrong_port":
        observed["port"] += 1
    elif defect == "wrong_instance":
        observed["instance"] = "B" * 43
    elif defect == "extra_key":
        observed["private_hint"] = "PRIVATE_READBACK_HINT"
    calls = []
    replacement = {**record, "instance": "C" * 43}
    def readback(_root, port, path, data=None):
        calls.append((port, path, data))
        assert path == "/v1/service-identity" and data is None
        if defect == "registry_replaced":
            _retirement_registry(root, replacement)
        return observed
    monkeypatch.setattr(cli, "request", readback)
    monkeypatch.setattr(cli.os, "kill",
                        lambda *_: pytest.fail("readback never authorizes a PID signal"))
    before = _retirement_authority(root, db)
    result = cli.lifecycle("stop", root, record["port"])
    assert result == {"ok": False, "reason": "service_identity_unverified"}
    assert calls == [(record["port"], "/v1/service-identity", None)]
    assert json.loads(registry.read_text()) == (
        replacement if defect == "registry_replaced" else record)
    _assert_retirement_authority(root, db, before)
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("reply", [
    {"ok": True}, {"ok": "true"}, {"ok": False, "reason": "PRIVATE_UNTRUSTED"},
    {"ok": False, "reason": ["PRIVATE_UNTRUSTED"]},
])
def test_service_retirement_ack_never_adopts_replacement_or_private_failure(
    retirement_private_state, monkeypatch, reply
):
    root, queue, db, key = retirement_private_state
    registry, record = _retirement_registry(root)
    replacement = {**record, "instance": "B" * 43}
    calls = []
    def request(_root, port, path, data=None):
        calls.append(path)
        if path == "/v1/service-identity":
            return record
        assert path == "/v1/service-stop" and data == record
        _retirement_registry(root, replacement)
        return reply
    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(cli.os, "kill",
                        lambda *_: pytest.fail("replacement must never be signalled"))
    before = _retirement_authority(root, db)
    result = cli.lifecycle("stop", root, record["port"])
    assert result == {"ok": False, "reason": (
        "service_identity_changed" if reply["ok"] is True else "service_stop_refused")}
    assert calls == ["/v1/service-identity", "/v1/service-stop"]
    assert json.loads(registry.read_text()) == replacement
    _assert_retirement_authority(root, db, before)
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("fence,reason", [
    ("active", "worker_active"), ("lease", "worker_active"),
    ("runnable", "runnable_task_pending"), ("retry", "runnable_task_pending"),
    ("otp_waiting", "otp_in_flight"), ("otp_ambiguous", "otp_in_flight"),
    ("paused_otp", "otp_in_flight"), ("update", "update_in_progress"),
])
def test_service_retirement_preserves_existing_task_otp_and_update_fences(
    retirement_private_state, fence, reason
):
    from executor.autonomy.supervisor import Supervisor, create_server
    root, queue, db, key = retirement_private_state
    supervisor = Supervisor(queue)
    server = create_server(supervisor, port=0)
    try:
        if fence == "active":
            supervisor.worker.active = "synthetic-task"
        elif fence == "lease":
            db.execute("UPDATE tasks SET owner='synthetic-owner',lease_until=?", (queue.clock() + 60,))
        elif fence == "runnable":
            db.execute("UPDATE tasks SET stage='DISCOVERED',blocker=NULL")
        elif fence == "retry":
            db.execute("UPDATE tasks SET stage='ERROR',blocker='retry_pending'")
        elif fence.startswith("otp_"):
            db.execute("UPDATE tasks SET stage='NEEDS_USER_ACTION',blocker=?", (fence,))
        elif fence == "paused_otp":
            db.execute("UPDATE tasks SET stage='BLOCKED',blocker='user_paused_from_otp_waiting'")
        else:
            (root / "update.json").write_text(json.dumps({"status": "updating"}))
            supervisor.update_state = lambda: {"status": "updating"}
        db.commit()
        before = _retirement_authority(root, db)
        result = supervisor.dispatch("POST", "/v1/service-stop", supervisor.service_identity())
        assert result == {"ok": False, "reason": reason}
        assert not supervisor.worker.stop_event.is_set()
        _assert_retirement_authority(root, db, before)
        assert "PRIVATE_" not in json.dumps(result)
    finally:
        server.server_close()


def test_exact_idle_service_retirement_fences_late_admission_and_is_idempotent(
    retirement_private_state
):
    from executor.autonomy.supervisor import Supervisor, create_server
    root, queue, db, key = retirement_private_state
    supervisor = Supervisor(queue)
    server = create_server(supervisor, port=0)
    try:
        before = _retirement_authority(root, db)
        health = supervisor.dispatch("GET", "/health", {})
        identity = supervisor.dispatch("GET", "/v1/service-identity", {})
        for changed in ({**identity, "pid": True}, {**identity, "pid": float(identity["pid"])},
                        {**identity, "port": str(identity["port"])},
                        {**identity, "instance": "私" * 43}, {**identity, "extra": True}):
            with pytest.raises(ValueError):
                supervisor.dispatch("POST", "/v1/service-stop", changed)
        assert supervisor.dispatch("POST", "/v1/service-stop", {
            **identity, "instance": "B" * 43}) == {
                "ok": False, "reason": "service_identity_changed"}
        assert not supervisor.worker.stop_event.is_set()
        expected = {"ok": True, "stopping": True}
        assert supervisor.dispatch("POST", "/v1/service-stop", identity) == expected
        assert supervisor.dispatch("POST", "/v1/service-stop", identity) == expected
        assert supervisor.worker.stop_event.is_set()
        with pytest.raises(RuntimeError, match="update in progress"):
            supervisor.dispatch("POST", "/v1/tasks", {"private": "PRIVATE_LATE_TASK"})
        with pytest.raises(RuntimeError, match="update in progress"):
            supervisor.run_local_fact("synthetic-task", "family.primary.role", "PRIVATE_LATE_FACT", 1)
        with pytest.raises(RuntimeError, match="update in progress"):
            supervisor.run_local_command(None)
        assert supervisor.dispatch("GET", "/health", {}) == health
        assert identity["instance"] not in json.dumps(health)
        assert health["final_click_actor"] == "user"
        _assert_retirement_authority(root, db, before)
        assert (root / "task-answers.key").read_bytes() == key
    finally:
        server.server_close()


@pytest.mark.parametrize("live", [False, True])
def test_cold_restart_does_not_adopt_a_live_service_without_its_registry(
    retirement_private_state, monkeypatch, live
):
    root, queue, db, key = retirement_private_state
    calls = []
    def request(_root, port, path, data=None):
        calls.append(path)
        assert path == "/health"
        if live:
            return {"ok": True, "final_click_actor": "user"}
        raise urllib.error.URLError("synthetic offline")
    def refuse_spawn(*_args, **_kwargs):
        raise RuntimeError("synthetic cold start reached ordinary isolated launcher")
    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(cli.subprocess, "Popen", refuse_spawn)
    before = _retirement_authority(root, db)
    if live:
        assert cli.lifecycle("restart", root, 9344) == {
            "ok": False, "reason": "service_record_missing"}
        assert calls == ["/health"]
    else:
        with pytest.raises(RuntimeError, match="cold start reached"):
            cli.lifecycle("restart", root, 9344)
        assert calls == ["/health", "/health"]
    _assert_retirement_authority(root, db, before)
    assert not (root / "service.json").exists()


def test_http_service_retirement_sends_ack_before_worker_stops(
    retirement_private_state, monkeypatch
):
    """The exact owned service must not close its response before acknowledging stop."""
    import threading
    from executor.autonomy.supervisor import Supervisor, create_server

    root, queue, db, key = retirement_private_state
    supervisor = Supervisor(queue)
    server = create_server(supervisor, port=0)
    handler = server.RequestHandlerClass
    response_written = threading.Event()
    original_send = handler._send_json
    original_stop = supervisor.worker.stop_event.set

    def observe_response(self, status, result, *, extra_headers=None):
        value = original_send(self, status, result, extra_headers=extra_headers)
        if self.path == "/v1/service-stop" and status == 200:
            response_written.set()
        return value

    def guarded_worker_stop():
        assert response_written.is_set(), "worker exited before stop acknowledgement"
        original_stop()

    monkeypatch.setattr(handler, "_send_json", observe_response)
    monkeypatch.setattr(supervisor.worker.stop_event, "set", guarded_worker_stop)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        before = _retirement_authority(root, db)
        identity = supervisor.service_identity()
        assert cli.request(root, server.server_port, "/v1/service-stop", identity) == {
            "ok": True, "stopping": True}
        assert response_written.is_set()
        assert supervisor.worker.stop_event.is_set()
        assert supervisor.mutation_fenced()
        _assert_retirement_authority(root, db, before)
        assert (root / "task-answers.key").read_bytes() == key
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("replace_registry", [False, True])
def test_actual_isolated_service_retires_without_pid_signals_or_private_replay(
    retirement_private_state, tmp_path, monkeypatch, replace_registry
):
    root, queue, db, key = retirement_private_state
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated",
           "HOME": str(tmp_path / "synthetic-service-home")}
    command = cli.isolated_cli_command("--runtime", str(root), "--port", str(port), "serve")
    log = tmp_path / "isolated-service.log"
    process = None
    try:
        with log.open("wb") as stream:
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=stream, start_new_session=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert process.poll() is None, "isolated service exited before readiness"
            if (root / "service.json").exists():
                try:
                    identity = cli.request(root, port, "/v1/service-identity")
                    health = cli.request(root, port, "/health")
                    if health.get("ok"):
                        break
                except (OSError, urllib.error.URLError):
                    pass
            time.sleep(.05)
        else:
            pytest.fail("isolated service readiness handshake did not complete")
        assert type(identity["pid"]) is int and identity["pid"] == process.pid
        assert type(identity["port"]) is int and identity["port"] == port
        assert json.loads((root / "service.json").read_text()) == identity
        assert (root / "service.json").stat().st_mode & 0o077 == 0
        assert health["final_click_actor"] == "user"
        assert identity["instance"] not in json.dumps(health)
        before = _retirement_authority(root, db)
        for headers, status in [
            ({}, 401),
            ({"Authorization": "Bearer " + cli.local_token(root),
              "Origin": f"http://127.0.0.1:{port}"}, 403),
            ({"Authorization": "Bearer " + cli.local_token(root), "Host": "localhost"}, 403),
        ]:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/service-stop",
                data=json.dumps(identity).encode(), headers={
                    "Content-Type": "application/json", **headers}, method="POST")
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(req, timeout=10)
            assert refused.value.code == status
        assert cli.request(root, port, "/v1/service-stop", {
            **identity, "instance": "B" * 43}) == {
                "ok": False, "reason": "service_identity_changed"}
        assert cli.request(root, port, "/health")["ok"] is True
        _assert_retirement_authority(root, db, before)
        with monkeypatch.context() as stop_context:
            stop_context.setattr(cli.os, "kill",
                                 lambda *_: pytest.fail("retirement must not signal a PID"))
            stop_context.setattr(cli.subprocess, "run",
                                 lambda *_args, **_kwargs: pytest.fail("retirement must not inspect ps"))
            if replace_registry:
                replacement = {**identity, "instance": "C" * 43}
                _retirement_registry(root, replacement)
                assert cli.request(root, port, "/v1/service-stop", identity) == {
                    "ok": True, "stopping": True}
            else:
                assert cli.lifecycle("stop", root, port) == {"ok": True, "running": False}
        assert process.wait(timeout=10) == 0
        if replace_registry:
            assert json.loads((root / "service.json").read_text()) == replacement
        else:
            assert not (root / "service.json").exists()
        _assert_retirement_authority(root, db, before)
        assert (root / "task-answers.key").read_bytes() == key
        assert b"PRIVATE_" not in log.read_bytes()
        assert identity["instance"].encode() not in log.read_bytes()
        with pytest.raises(urllib.error.URLError):
            cli.request(root, port, "/health")
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_service_start_refuses_live_legacy_writer_before_journal_initialization(
    retirement_private_state, monkeypatch
):
    from executor.autonomy import queue as queue_module
    from executor.autonomy.worker import ProcessLock
    root, queue, db, key = retirement_private_state
    registry, record = _retirement_registry(root, {"pid": os.getpid(), "port": 9344})
    before = _retirement_authority(root, db)
    registry_before = registry.read_bytes()
    probes = []
    def probe(pid, sig):
        probes.append((pid, sig))
        assert pid == record["pid"] and sig == 0
    monkeypatch.setattr(cli.os, "kill", probe)
    monkeypatch.setattr(queue_module, "TaskQueue",
                        lambda *_: pytest.fail("live legacy writer forbids queue initialization"))
    with pytest.raises(BlockingIOError, match="task_state_in_use"):
        cli.serve(root, 9344)
    assert probes == [(record["pid"], 0)]
    assert registry.read_bytes() == registry_before
    _assert_retirement_authority(root, db, before)
    with ProcessLock(root / "worker.lock"):
        pass  # Failed takeover releases only its own ordinary fence.

# Recovery ownership is independent of business imports/task-state writers.
@pytest.mark.parametrize("defect", [
    "legacy", "bool_pid", "float_pid", "string_port", "bool_port",
    "bool_service_port", "wrong_service_port", "extra_key", "short_token",
    "unicode_token", "invalid_token", "malformed", "list", "oversized",
    "public", "symlink", "hardlink", "fifo", "directory",
])
def test_bootstrap_refuses_ambiguous_registry_without_process_or_business_actions(
    retirement_private_state, tmp_path, monkeypatch, defect
):
    root, queue, db, key = retirement_private_state
    record = {"pid": os.getpid(), "port": 19344, "service_port": 9344, "token": "A" * 43}
    registry = root / "bootstrap.json"
    if defect == "legacy":
        record.pop("service_port")
    elif defect == "bool_pid":
        record["pid"] = True
    elif defect == "float_pid":
        record["pid"] = float(record["pid"])
    elif defect == "string_port":
        record["port"] = "19344"
    elif defect == "bool_port":
        record["port"] = True
    elif defect == "bool_service_port":
        record["service_port"] = True
    elif defect == "wrong_service_port":
        record["service_port"] = 9345
    elif defect == "extra_key":
        record["private"] = "PRIVATE_KEY"
    elif defect == "short_token":
        record["token"] = "short"
    elif defect == "unicode_token":
        record["token"] = "私" * 43
    elif defect == "invalid_token":
        record["token"] = "/" * 43
    registry.write_text(json.dumps(record), encoding="utf-8")
    registry.chmod(0o600)
    if defect == "malformed":
        registry.write_text("PRIVATE_MALFORMED")
    elif defect == "list":
        registry.write_text("[]")
    elif defect == "oversized":
        registry.write_text(" " * 65537)
    elif defect == "public":
        registry.chmod(0o644)
    elif defect in {"symlink", "hardlink", "fifo", "directory"}:
        registry.unlink()
        outside = tmp_path / "PRIVATE_OUTSIDE_REGISTRY"
        outside.write_text(json.dumps(record), encoding="utf-8")
        outside.chmod(0o600)
        if defect == "symlink":
            registry.symlink_to(outside)
        elif defect == "hardlink":
            os.link(outside, registry)
        elif defect == "fifo":
            os.mkfifo(registry, 0o600)
        else:
            registry.mkdir(mode=0o700)
    authority = _retirement_authority(root, db)
    before = _retirement_inventory(tmp_path)
    def forbidden(*_args, **_kwargs):
        pytest.fail("ambiguous recovery cannot inspect PID, contact a service or launch business")
    monkeypatch.setattr(bootstrap.subprocess, "run", forbidden)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", forbidden)
    monkeypatch.setattr(bootstrap.os, "kill", forbidden)
    monkeypatch.setattr(bootstrap.urllib.request, "build_opener", forbidden)
    result = bootstrap.open_bootstrap(root, 9344, presenter=forbidden)
    expected = "bootstrap_service_mismatch" if defect == "wrong_service_port" else "bootstrap_state_invalid"
    assert result == {"ok": False, "opened": False, "reason": expected}
    _assert_retirement_inventory(tmp_path, before)
    _assert_retirement_authority(root, db, authority)
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("defect", ["symlink", "hardlink", "fifo", "directory", "held"])
def test_bootstrap_owner_lease_refuses_second_writer_without_deleting_state(
    retirement_private_state, tmp_path, monkeypatch, defect
):
    root, queue, db, key = retirement_private_state
    lock = root / "bootstrap-service.lock"
    outside = tmp_path / "PRIVATE_RECOVERY_LOCK"
    outside.write_bytes(b"PRIVATE_LOCK_CANARY")
    outside.chmod(0o600)
    held = None
    if defect == "symlink":
        lock.symlink_to(outside)
    elif defect == "hardlink":
        os.link(outside, lock)
    elif defect == "fifo":
        os.mkfifo(lock, 0o600)
    elif defect == "directory":
        lock.mkdir(mode=0o700)
    else:
        held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    authority = _retirement_authority(root, db)
    before = _retirement_inventory(tmp_path)
    monkeypatch.setattr(bootstrap.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("no second recovery writer"))
    try:
        result = bootstrap.open_bootstrap(root, 9344,
            presenter=lambda *_: pytest.fail("unverified owner cannot open a page"))
        assert result == {"ok": False, "opened": False, "reason":
                          "bootstrap_owner_busy" if defect == "held" else "bootstrap_state_invalid"}
        _assert_retirement_inventory(tmp_path, before)
        _assert_retirement_authority(root, db, authority)
    finally:
        if held is not None:
            os.close(held)


@pytest.mark.parametrize("readback", ["exact", "legacy", "bool_pid", "wrong_token",
                                      "wrong_service", "private", "oversized", "replacement"])
def test_bootstrap_reuse_requires_exact_bounded_authenticated_identity(
    tmp_path, monkeypatch, readback
):
    root = tmp_path / "recovery"
    root.mkdir(mode=0o700)
    state = root / "bootstrap.json"
    record = {"pid": os.getpid(), "port": 19344, "service_port": 9344, "token": "A" * 43}
    state.write_text(json.dumps(record))
    state.chmod(0o600)
    observed = dict(record)
    if readback == "legacy":
        observed.pop("service_port")
    elif readback == "bool_pid":
        observed["pid"] = True
    elif readback == "wrong_token":
        observed["token"] = "B" * 43
    elif readback == "wrong_service":
        observed["service_port"] = 9345
    elif readback == "private":
        observed = {"error": "PRIVATE_SERVER_DETAIL"}
    replacement = {**record, "token": "B" * 43}
    calls = []
    class Reply:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self, bound):
            assert bound == 65537
            if readback == "replacement":
                state.write_text(json.dumps(replacement))
                state.chmod(0o600)
            return (b" " * 65537 if readback == "oversized"
                    else json.dumps(observed).encode())
    class Opener:
        def open(self, url, timeout):
            calls.append((url, timeout))
            return Reply()
    def opener(proxy, handler):
        assert proxy.proxies == {}
        assert handler.redirect_request(None, None, None, None, None) is None
        return Opener()
    monkeypatch.setattr(bootstrap.urllib.request, "build_opener", opener)
    monkeypatch.setattr(bootstrap.subprocess, "run",
                        lambda *_args, **_kwargs: pytest.fail("PID argv is not identity"))
    actual = bootstrap._bootstrap_current(state, 9344)
    assert actual == ({"pid": record["pid"],
                       "url": "http://127.0.0.1:19344/?token=" + record["token"]}
                      if readback == "exact" else None)
    assert calls == [("http://127.0.0.1:19344/identity?token=" + record["token"], 2)]
    assert json.loads(state.read_text()) == (replacement if readback == "replacement" else record)


def test_actual_bootstrap_identity_is_readonly_and_preserves_replaced_registry(
    retirement_private_state, monkeypatch
):
    import threading
    root, queue, db, key = retirement_private_state
    authority = _retirement_authority(root, db)
    monkeypatch.setattr(bootstrap, "_page",
                        lambda *_: pytest.fail("identity must not import/inspect business configuration"))
    monkeypatch.setattr(cli, "_start_consumer_service",
                        lambda *_: pytest.fail("identity must not start task service"))
    ready = threading.Event()
    servers = []
    actual_server = bootstrap.LoopbackHTTPServer
    def observe_server(*args, **kwargs):
        server = actual_server(*args, **kwargs)
        servers.append(server)
        return server
    monkeypatch.setattr(bootstrap, "LoopbackHTTPServer", observe_server)
    # The unauthenticated response below is the real readiness handshake.
    thread = threading.Thread(target=bootstrap.serve_bootstrap,
                              args=(root, 9344), daemon=True)
    original_dump = bootstrap.json.dump
    def published(value, handle, *args, **kwargs):
        result = original_dump(value, handle, *args, **kwargs)
        ready.set()
        return result
    monkeypatch.setattr(bootstrap.json, "dump", published)
    thread.start()
    try:
        assert ready.wait(timeout=5)
        server = servers[0]
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(base + "/identity", timeout=5)
        assert refused.value.code == 403
        state = root / "bootstrap.json"
        record = json.loads(state.read_text())
        assert bootstrap._bootstrap_identity_valid(record)
        assert record["service_port"] == 9344
        before = _retirement_inventory(root)
        url = base + "/identity?token=" + record["token"]
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.headers["Content-Type"].startswith("application/json")
            assert response.headers["Cache-Control"] == "no-store"
            assert json.load(response) == record
        assert bootstrap._bootstrap_current(state, 9344) == {
            "pid": record["pid"], "url": base + "/?token=" + record["token"]}
        assert bootstrap._bootstrap_current(state, 9345) is None
        for headers, query in [({"Origin": "https://external.invalid"}, ""),
                               ({"Host": "localhost:" + str(server.server_port)}, ""),
                               ({}, "&token=" + record["token"])]:
            request = urllib.request.Request(url + query, headers=headers)
            with pytest.raises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(request, timeout=5)
            assert denied.value.code == 403
        _assert_retirement_inventory(root, before)
        _assert_retirement_authority(root, db, authority)
        with pytest.raises(BlockingIOError):
            with bootstrap._bootstrap_guard(root):
                pytest.fail("real recovery service must retain its owning lease")
        replacement = {**record, "token": "B" * 43}
        state.write_text(json.dumps(replacement))
        state.chmod(0o600)
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert json.loads(state.read_text()) == replacement
        _assert_retirement_authority(root, db, authority)
    finally:
        if servers:
            servers[0].shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("scenario", [
    "active_replaced", "destination_occupied", "retained_resealed", "retained_replaced",
])
def test_final_health_recovery_preserves_changed_authority_and_occupied_slots(
    tmp_path, monkeypatch, action, scenario
):
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(
        tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    destination = previous if action == "rollback" else failed
    retained = failed if action == "rollback" else previous
    displaced = apps / ".independent-displaced-bundle"
    actual_start = consumer._candidate_starts
    starts = []
    after_fault = {}

    def snapshot():
        # Include EMPTY directories and their exact inode identity, not only
        # file bytes: rename may otherwise silently replace an empty stranger.
        return {
            path.relative_to(apps).as_posix():
            (path.stat(follow_symlinks=False).st_dev,
             path.stat(follow_symlinks=False).st_ino,
             stat.S_IMODE(path.stat(follow_symlinks=False).st_mode))
            for path in [apps, *apps.rglob("*")]
        }

    def start(python, source):
        healthy = actual_start(python, source)
        assert healthy
        starts.append(source)
        if len(starts) == 2:
            assert source.parents[2] == app
            assert retained.is_dir() and not destination.exists()
            if scenario == "active_replaced":
                app.rename(displaced)
                shutil.copytree(displaced, app, symlinks=True)
                assert consumer._trusted_bundle(app)
            elif scenario == "destination_occupied":
                destination.mkdir()
                assert not list(destination.iterdir())
            elif scenario == "retained_resealed":
                _reseal_changed_source(retained)
            else:
                retained.rename(displaced)
                shutil.copytree(displaced, retained, symlinks=True)
                assert consumer._trusted_bundle(retained)
            after_fault["files"] = _identity_file_inventory(apps)
            after_fault["locations"] = snapshot()
            # A deterministic final-path health fault exercises recovery after
            # the real startup/identity proof. No live app or account is used.
            return False
        return healthy

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert len(starts) == 2
        assert result["ok"] is False
        assert result["reason"] == ("rollback_recovery_required" if action == "rollback"
                                    else "post_activation_recovery_required")
        # Complete source/runtime/native bytes, modes, EMPTY slots and inodes
        # after the fault remain at exactly those locations. No restore of a
        # resealed/replaced retained release, overwrite, cleanup or second start.
        assert _identity_file_inventory(apps) == after_fault["files"]
        assert snapshot() == after_fault["locations"]
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("scenario", [
    "unchanged", "destination_occupied", "destination_alias",
    "retained_resealed", "retained_replaced", "candidate_resealed",
])
def test_activation_rename_error_rechecks_recovery_authority_before_restore_or_cleanup(
    tmp_path, monkeypatch, action, scenario
):
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(
        tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    staging = apps / ("." + consumer.APP_NAME + ".app.installing")
    candidate = previous if action == "rollback" else staging
    retained = failed if action == "rollback" else previous
    displaced = apps / ".independent-displaced-activation"
    actual_rename = Path.rename
    actual_start = consumer._candidate_starts
    app_before = _identity_file_inventory(app)
    app_inode = app.stat().st_ino
    calls = []
    starts = []
    after_fault = {}

    def snapshot():
        return {
            path.relative_to(apps).as_posix():
            (path.stat(follow_symlinks=False).st_dev,
             path.stat(follow_symlinks=False).st_ino,
             stat.S_IMODE(path.stat(follow_symlinks=False).st_mode))
            for path in [apps, *apps.rglob("*")]
        }

    def start(python, source):
        actual = actual_start(python, source)
        assert actual
        starts.append(source)
        return actual

    def rename(source, target):
        if source == candidate and Path(target) == app and not calls:
            calls.append((source, Path(target)))
            assert retained.is_dir() and not app.exists()
            if scenario == "destination_occupied":
                app.mkdir()
                assert not list(app.iterdir())
            elif scenario == "destination_alias":
                app.symlink_to(tmp_path / "absent-foreign-app", target_is_directory=True)
                assert app.is_symlink() and not app.exists()
            elif scenario == "retained_resealed":
                _reseal_changed_source(retained)
            elif scenario == "retained_replaced":
                actual_rename(retained, displaced)
                shutil.copytree(displaced, retained, symlinks=True)
                assert consumer._trusted_bundle(retained)
            elif scenario == "candidate_resealed":
                _reseal_changed_source(candidate)
            after_fault["files"] = _identity_file_inventory(apps)
            after_fault["locations"] = snapshot()
            raise OSError("PRIVATE_SYNTHETIC_RENAME_ERROR")
        if calls:
            calls.append((source, Path(target)))
        return actual_rename(source, target)

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        monkeypatch.setattr(Path, "rename", rename)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert len(starts) == (2 if scenario == "unchanged" else 1)
        assert starts[0] == candidate / "Contents" / "Resources" / "release"
        if scenario == "unchanged":
            assert starts[1] == app / "Contents" / "Resources" / "release", (
                "restored original must pass actual final-path health before recovery")
        assert result["ok"] is False
        if scenario == "unchanged":
            assert result["reason"] == ("rollback_activation_failed" if action == "rollback"
                                        else "activation_failed")
            assert app.stat().st_ino == app_inode
            assert _identity_file_inventory(app) == app_before
            assert not retained.exists() and not retained.is_symlink()
            assert len(calls) == 2
            assert calls[-1] == (retained, app)
            assert candidate.is_dir() if action == "rollback" else not candidate.exists()
        else:
            assert result["reason"] == ("manual_recovery_required" if action == "rollback"
                                        else "rollback_required")
            assert len(calls) == 1, "no speculative restore move after authority changed"
            assert _identity_file_inventory(apps) == after_fault["files"]
            assert snapshot() == after_fault["locations"]
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("scenario", [
    "quarantine_replaced", "destination_occupied", "destination_alias",
])
def test_post_health_restore_rename_error_never_moves_foreign_quarantine_or_overwrites_slot(
    tmp_path, monkeypatch, scenario
):
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(tmp_path, rollback=False)
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    displaced = apps / ".independent-displaced-quarantine"
    actual_rename = Path.rename
    actual_start = consumer._candidate_starts
    starts = []
    after_fault = {}
    moves_after_fault = []

    def snapshot():
        return {
            path.relative_to(apps).as_posix():
            (path.stat(follow_symlinks=False).st_dev,
             path.stat(follow_symlinks=False).st_ino,
             stat.S_IMODE(path.stat(follow_symlinks=False).st_mode))
            for path in [apps, *apps.rglob("*")]
        }

    def start(python, source):
        actual = actual_start(python, source)
        assert actual
        starts.append(source)
        return False if len(starts) == 2 else actual

    def rename(source, target):
        if source == previous and Path(target) == app and len(starts) == 2:
            assert failed.is_dir() and not app.exists()
            if scenario == "quarantine_replaced":
                actual_rename(failed, displaced)
                shutil.copytree(displaced, failed, symlinks=True)
                assert consumer._trusted_bundle(failed)
            elif scenario == "destination_occupied":
                app.mkdir()
                assert not list(app.iterdir())
            else:
                app.symlink_to(tmp_path / "absent-foreign-app", target_is_directory=True)
                assert app.is_symlink() and not app.exists()
            after_fault["files"] = _identity_file_inventory(apps)
            after_fault["locations"] = snapshot()
            raise OSError("PRIVATE_SYNTHETIC_RESTORE_ERROR")
        if after_fault:
            moves_after_fault.append((source, Path(target)))
        return actual_rename(source, target)

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        monkeypatch.setattr(Path, "rename", rename)
        result = install_macos_app(repo, destination=apps, platform="darwin",
                                   task_state_root=state)
        assert len(starts) == 2
        assert result["ok"] is False
        assert result["reason"] == "post_activation_recovery_required"
        assert moves_after_fault == []
        assert _identity_file_inventory(apps) == after_fault["files"]
        assert snapshot() == after_fault["locations"]
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("scenario", [
    "unchanged", "retained_resealed", "retained_replaced",
    "destination_occupied", "destination_alias",
])
def test_successful_final_health_requires_retained_identity_and_free_recovery_slot(
    tmp_path, monkeypatch, action, scenario
):
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(
        tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    retained = failed if action == "rollback" else previous
    destination = previous if action == "rollback" else failed
    displaced = apps / ".independent-success-displaced"
    actual_start = consumer._candidate_starts
    starts = []
    after_health = {}

    def snapshot():
        return {
            path.relative_to(apps).as_posix():
            (path.stat(follow_symlinks=False).st_dev,
             path.stat(follow_symlinks=False).st_ino,
             stat.S_IMODE(path.stat(follow_symlinks=False).st_mode))
            for path in [apps, *apps.rglob("*")]
        }

    def start(python, source):
        healthy = actual_start(python, source)
        assert healthy
        starts.append(source)
        if len(starts) == 2:
            assert source.parents[2] == app
            assert retained.is_dir() and not destination.exists()
            if scenario == "retained_resealed":
                _reseal_changed_source(retained)
            elif scenario == "retained_replaced":
                retained.rename(displaced)
                shutil.copytree(displaced, retained, symlinks=True)
                assert consumer._trusted_bundle(retained)
            elif scenario == "destination_occupied":
                destination.mkdir()
                assert not list(destination.iterdir())
            elif scenario == "destination_alias":
                destination.symlink_to(tmp_path / "absent-success-foreign", target_is_directory=True)
                assert destination.is_symlink() and not destination.exists()
            after_health["files"] = _identity_file_inventory(apps)
            after_health["locations"] = snapshot()
        # Return the actual SUCCESSFUL startup. Only retained/slot admission,
        # not an invented health error, may prevent transaction completion.
        return healthy

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert len(starts) == 2
        assert result["ok"] is (scenario == "unchanged")
        if scenario == "unchanged":
            assert result["restored" if action == "rollback" else "installed"] is True
        else:
            assert result["reason"] == ("rollback_recovery_required" if action == "rollback"
                                       else "post_activation_recovery_required")
            assert "installed" not in result and "restored" not in result
        # Even a healthy candidate cannot authorize replacement/cleanup of a
        # different retained inode, resealed release or empty/aliased stranger.
        assert _identity_file_inventory(apps) == after_health["files"]
        assert snapshot() == after_health["locations"]
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


def test_installed_restore_entry_preserves_real_current_journal_and_retained_backup(
    tmp_path, monkeypatch
):
    import shutil
    import sqlite3
    from contextlib import closing
    from types import SimpleNamespace
    from executor.autonomy import state_compatibility
    from test_task_state_compatibility_v1 import legacy_state, authority, encrypted_answers

    actual = Path(__file__).resolve().parents[1]
    repo = tmp_path / "Job-Application-Executor"
    repo.mkdir()
    shutil.copytree(actual / "executor", repo / "executor",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(actual / "requirements.txt", repo / "requirements.txt")
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    apps = tmp_path / "Applications"
    empty = tmp_path / "empty-build-state"
    assert install_macos_app(repo, destination=apps, platform="darwin", task_state_root=empty)["ok"]
    app = apps / (consumer.APP_NAME + ".app")
    source = app / "Contents" / "Resources" / "release"
    first = (source / "release-source-manifest.json").read_bytes()
    with (repo / "executor" / "__init__.py").open("a") as handle:
        handle.write("\n# Synthetic explicitly displaced version.\n")
    assert install_macos_app(repo, destination=apps, platform="darwin", task_state_root=empty)["ok"]
    second = (source / "release-source-manifest.json").read_bytes()
    assert first != second
    monkeypatch.setattr(cli, "__file__", str(source / "executor" / "autonomy" / "cli.py"))
    # Only entry-platform admission is selected; consumer health/backup uses
    # real child processes and the platform's original runtime metadata.
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    for name in ("lifecycle", "open_ui", "ensure_chrome", "launch_native_consumer"):
        monkeypatch.setattr(cli, name, lambda *a, **kw: pytest.fail("restore is a cold transaction"))
    from executor.autonomy.worker import ProcessLock
    actual_transaction = consumer.rollback_macos_app
    receipts = []
    def observed_transaction(*args, **kwargs):
        result = actual_transaction(*args, **kwargs)
        receipts.append(result)
        return result
    monkeypatch.setattr(consumer, "rollback_macos_app", observed_transaction)
    from executor.autonomy.runtime_paths import default_runtime
    state = default_runtime(source)
    # The original legacy_state fixture creates only its leaf authority.
    # Prepare the real nested packaged namespace, not a different task root.
    state.parent.mkdir(parents=True, exist_ok=True)
    assert not state.exists()
    with closing(legacy_state(state)) as db:
        key = encrypted_answers(state, db)
        before = authority(db)
        answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        profile = "\n".join("PRIVATE_RESTORE_PROFILE_" + str(i) for i in range(1000))
        (state / "profile.json").write_text(profile)
        (state / "auth.token").write_text("PRIVATE_RESTORE_TOKEN")
        for guard in (state_compatibility.native_window_guard(state),
                      ProcessLock(state / "worker.lock"), ProcessLock(state / "migration.lock")):
            with guard:
                denied = cli.restore_installed_consumer(state)
                assert denied["ok"] is False and denied["reason"] == "task_state_in_use"
                assert (source / "release-source-manifest.json").read_bytes() == second
                assert authority(db) == before
                assert (state / "task-answers.key").read_bytes() == key
        result = cli.restore_installed_consumer(state)
        assert result == {"ok": True, "restored": True,
                          "final_click_actor": "user", "submit_capability": False}
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == answers
        assert (state / "task-answers.key").read_bytes() == key
        assert (state / "profile.json").read_text() == profile
        assert (state / "auth.token").read_text() == "PRIVATE_RESTORE_TOKEN"
        assert (source / "release-source-manifest.json").read_bytes() == first
        displaced = apps / ("." + consumer.APP_NAME + ".app.failed")
        assert (displaced / "Contents" / "Resources" / "release" /
                "release-source-manifest.json").read_bytes() == second
        assert not (apps / ("." + consumer.APP_NAME + ".app.previous")).exists()
        capsules = list(apps.glob(".jae-task-state-backup-*"))
        assert len(capsules) == 1
        capsule = capsules[0]
        manifest = json.loads((capsule / "backup-manifest.json").read_text())
        external = receipts[-1]["task_state_backup"]["receipt"]
        assert manifest == external
        assert state_compatibility.verify_task_state_backup(capsule, external)
        with closing(sqlite3.connect(capsule / "tasks.sqlite3")) as snapshot:
            assert authority(snapshot) == before
            assert snapshot.execute("SELECT * FROM task_answer_events").fetchall() == answers
        assert (capsule / "task-answers.key").read_bytes() == key
        assert "PRIVATE_" not in json.dumps(result)
        assert str(tmp_path) not in json.dumps(result)
        # No retained slot remains: a second explicit command is a refusal,
        # never a blind compensation/replay of the completed transaction.
        repeated = cli.restore_installed_consumer(state)
        assert repeated["ok"] is False and repeated["reason"] == "rollback_unavailable"
        assert (source / "release-source-manifest.json").read_bytes() == first
        assert authority(db) == before


@pytest.mark.parametrize("fault", [
    "platform", "checkout", "unverified", "digest_bool", "digest_mutable",
    "source_alias", "untrusted", "legacy_launcher",
])
def test_installed_restore_entry_refuses_unadmitted_location_before_transaction(
    tmp_path, monkeypatch, fault
):
    from types import SimpleNamespace
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    source = app / "Contents" / "Resources" / "release"
    module = source / "executor" / "autonomy" / "cli.py"
    module.parent.mkdir(parents=True)
    module.write_text("# synthetic admission fixture\n")
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    executable.parent.mkdir(parents=True)
    executable.write_text(consumer._native_packaged_launcher())
    identity = {"packaged": True, "expected": "a" * 64}
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cli, "__file__", str(module))
    monkeypatch.setattr(cli, "_consumer_release_identity", lambda: identity)
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda _: True)
    monkeypatch.setattr(consumer, "rollback_macos_app",
                        lambda *a, **kw: pytest.fail("refuse before activation/backup"))
    if fault == "platform":
        monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="linux"))
    elif fault == "checkout":
        monkeypatch.setattr(cli, "__file__", str(tmp_path / "repo" / "executor" / "autonomy" / "cli.py"))
    elif fault == "unverified":
        identity["packaged"] = False
    elif fault == "digest_bool":
        identity["expected"] = True
    elif fault == "digest_mutable":
        identity["expected"] = "main"
    elif fault == "source_alias":
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        monkeypatch.setattr(cli, "__file__", str(alias / "executor" / "autonomy" / "cli.py"))
    elif fault == "untrusted":
        monkeypatch.setattr(consumer, "_trusted_bundle", lambda _: False)
    elif fault == "legacy_launcher":
        executable.write_text(consumer._legacy_launcher(tmp_path / "Job-Application-Executor"))
    result = cli.restore_installed_consumer(tmp_path / "state")
    assert result == {"ok": False, "restored": False,
                      "reason": "macos_required" if fault == "platform" else "installed_restore_unverified",
                      "final_click_actor": "user", "submit_capability": False}
    assert not (tmp_path / "state").exists()
    assert "PRIVATE_" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("outcome", [
    {"ok": False, "reason": "task_state_in_use"},
    {"ok": False, "reason": "update_in_progress"},
    {"ok": False, "reason": "rollback_state_incompatible"},
    {"ok": False, "reason": "rollback_post_activation_unhealthy"},
    {"ok": False, "reason": "manual_recovery_required"},
    {"ok": False, "reason": "PRIVATE_UNKNOWN_REASON", "path": "PRIVATE_PATH"},
    {"ok": 1, "restored": True, "path": "PRIVATE_PATH"},
    {"ok": True, "restored": 1, "path": "PRIVATE_PATH"},
    None, "exception",
])
def test_installed_restore_entry_has_finite_outcome_without_retry_or_private_echo(
    tmp_path, monkeypatch, outcome
):
    from types import SimpleNamespace
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    source = app / "Contents" / "Resources" / "release"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    executable.parent.mkdir(parents=True)
    executable.write_text(consumer._native_packaged_launcher())
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cli, "__file__", str(source / "executor" / "autonomy" / "cli.py"))
    monkeypatch.setattr(cli, "_consumer_release_identity",
                        lambda: {"packaged": True, "expected": "a" * 64})
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda _: True)
    calls = []
    from executor.autonomy.runtime_paths import default_runtime
    state = default_runtime(source)
    def transaction(destination, *, task_state_root):
        calls.append((destination, task_state_root))
        if outcome == "exception":
            raise OSError("PRIVATE_PATH PRIVATE_EXCEPTION")
        return outcome
    monkeypatch.setattr(consumer, "rollback_macos_app", transaction)
    result = cli.restore_installed_consumer(state)
    assert calls == [(app.parent, state)]
    assert result["ok"] is False and result["restored"] is False
    known = {"task_state_in_use", "update_in_progress", "rollback_state_incompatible",
             "rollback_post_activation_unhealthy", "manual_recovery_required"}
    expected = outcome.get("reason") if type(outcome) is dict else None
    assert result["reason"] == (expected if expected in known else "restore_unconfirmed")
    assert result["submit_capability"] is False and result["final_click_actor"] == "user"
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


def test_native_restore_is_explicit_exclusive_and_never_automatic(tmp_path, monkeypatch, capsys):
    calls = []
    receipt = {"ok": True, "restored": True, "final_click_actor": "user",
               "submit_capability": False}
    monkeypatch.setattr(cli, "restore_installed_consumer",
                        lambda root: calls.append(("restore", root)) or receipt)
    monkeypatch.setattr(cli, "launch_native_consumer",
                        lambda root, port, *, smoke=False:
                        calls.append(("launch", root, port, smoke)) or {"ok": True})
    assert cli.main(["--runtime", str(tmp_path), "native-launch"]) == 0
    assert calls == [("launch", tmp_path, 9344, False)]
    calls.clear()
    assert cli.main(["--runtime", str(tmp_path), "native-launch", "--restore-previous"]) == 0
    assert calls == [("restore", tmp_path)]
    calls.clear()
    assert cli.main(["--runtime", str(tmp_path), "restore-app"]) == 0
    assert calls == [("restore", tmp_path)]
    calls.clear()
    with pytest.raises(SystemExit) as error:
        cli.main(["--runtime", str(tmp_path), "native-launch", "--restore-previous", "--native-smoke"])
    assert error.value.code == 2 and calls == []
    assert '"submit_capability": false' in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["alternate_empty", "alternate_private", "state_alias", "ancestor_alias"])
def test_installed_restore_cannot_substitute_another_task_authority(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace
    from executor.autonomy.runtime_paths import default_runtime
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    source = app / "Contents" / "Resources" / "release"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    executable.parent.mkdir(parents=True)
    executable.write_text(consumer._native_packaged_launcher())
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cli, "__file__", str(source / "executor" / "autonomy" / "cli.py"))
    monkeypatch.setattr(cli, "_consumer_release_identity",
                        lambda: {"packaged": True, "expected": "a" * 64})
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda _: True)
    monkeypatch.setattr(consumer, "rollback_macos_app",
                        lambda *a, **kw: pytest.fail("wrong authority must not acquire app/task locks"))
    current = default_runtime(source)
    current.mkdir(parents=True)
    private = "\n".join("PRIVATE_CURRENT_STATE_" + str(i) for i in range(1000))
    sentinel = current / "private-unchanged.txt"
    sentinel.write_text(private)
    old = {str(p.relative_to(current)):p.read_bytes() for p in current.rglob("*") if p.is_file()}
    observed = tmp_path / "alternate"
    if fault == "alternate_private":
        observed.mkdir()
        (observed / "private.txt").write_text("PRIVATE_ALTERNATE_AUTHORITY")
    elif fault == "state_alias":
        retained = tmp_path / "retained-current-state"
        current.rename(retained)
        current.symlink_to(retained, target_is_directory=True)
        observed = current
    elif fault == "ancestor_alias":
        parent = current.parent
        retained = tmp_path / "retained-current-parent"
        parent.rename(retained)
        parent.symlink_to(retained, target_is_directory=True)
        observed = current
    result = cli.restore_installed_consumer(observed)
    assert result == {"ok": False, "restored": False,
                      "reason": "installed_restore_state_unverified",
                      "final_click_actor": "user", "submit_capability": False}
    assert {str(p.relative_to(current)):p.read_bytes() for p in current.rglob("*") if p.is_file()} == old
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert not (current / "native-window.lock").exists()
    assert not (current / "worker.lock").exists()
    assert not (current / "migration.lock").exists()
    assert not (app.parent / ("." + consumer.APP_NAME + ".app.transaction.lock")).exists()
    if fault == "alternate_empty":
        assert not observed.exists()
    if fault == "alternate_private":
        assert (observed / "private.txt").read_text() == "PRIVATE_ALTERNATE_AUTHORITY"

def _installed_update_entry_fixture(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from executor.autonomy.runtime_paths import default_runtime
    from executor.autonomy import app_distribution
    monkeypatch.setenv("HOME", str(tmp_path / "Synthetic Home"))
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    source = app / "Contents" / "Resources" / "release"
    module = source / "executor" / "autonomy" / "cli.py"
    module.parent.mkdir(parents=True)
    module.write_text("# synthetic installed routing fixture\n")
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    executable.parent.mkdir(parents=True)
    executable.write_text(consumer._native_packaged_launcher())
    identity = {"packaged": True, "expected": "a" * 64}
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cli, "__file__", str(module))
    monkeypatch.setattr(cli, "_consumer_release_identity", lambda: identity)
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda _: True)
    root = default_runtime(source)
    delivery = tmp_path / "Chosen Public Delivery"
    delivery.mkdir()
    (delivery / "receipt-canary").write_text("PUBLIC_DELIVERY_PRESERVE")
    return app, source, root, delivery, identity, app_distribution


def test_installed_update_routes_one_delivery_to_its_own_app_and_task_authority(tmp_path, monkeypatch):
    app, source, root, delivery, _identity, distribution = _installed_update_entry_fixture(
        tmp_path, monkeypatch)
    calls = []
    def transaction(candidate, *, destination, task_state_root):
        calls.append((candidate, destination, task_state_root))
        return {"ok": True, "installed": True, "replaced": True,
                "app_path": "PRIVATE_APP", "task_state_backup": {"path": "PRIVATE_BACKUP"}}
    monkeypatch.setattr(distribution, "install_macos_distribution", transaction)
    result = cli.update_installed_consumer(root, delivery)
    assert calls == [(delivery, app.parent, root)]
    assert result == {"ok": True, "updated": True, "final_click_actor": "user",
                      "submit_capability": False}
    assert not root.exists()
    assert (delivery / "receipt-canary").read_text() == "PUBLIC_DELIVERY_PRESERVE"
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("fault", [
    "platform", "checkout", "unverified", "digest_bool", "digest_mutable",
    "source_alias", "untrusted", "legacy_launcher", "alternate_empty",
    "alternate_private", "state_alias", "ancestor_alias",
    "candidate_relative", "candidate_alias", "candidate_ancestor_alias",
])
def test_installed_update_refuses_substituted_authorities_before_intake_or_locks(
    tmp_path, monkeypatch, fault
):
    from types import SimpleNamespace
    app, source, root, delivery, identity, distribution = _installed_update_entry_fixture(
        tmp_path, monkeypatch)
    root.mkdir(parents=True)
    sentinel = root / "private-state"
    sentinel.write_bytes(b"PRIVATE_CURRENT_AUTHORITY")
    supplied_root, supplied_delivery = root, delivery
    if fault == "platform":
        monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="linux"))
    elif fault == "checkout":
        monkeypatch.setattr(cli, "__file__", str(tmp_path / "repo/executor/autonomy/cli.py"))
    elif fault == "unverified":
        identity["packaged"] = False
    elif fault == "digest_bool":
        identity["expected"] = True
    elif fault == "digest_mutable":
        identity["expected"] = "main"
    elif fault == "source_alias":
        alias = tmp_path / "source-alias"
        alias.symlink_to(source, target_is_directory=True)
        monkeypatch.setattr(cli, "__file__", str(alias / "executor/autonomy/cli.py"))
    elif fault == "untrusted":
        monkeypatch.setattr(consumer, "_trusted_bundle", lambda _: False)
    elif fault == "legacy_launcher":
        (app / "Contents/MacOS/AIApplicationManager").write_text(
            consumer._legacy_launcher(tmp_path / "Job-Application-Executor"))
    elif fault in {"alternate_empty", "alternate_private"}:
        supplied_root = tmp_path / "alternate"
        if fault == "alternate_private":
            supplied_root.mkdir()
            (supplied_root / "private-state").write_bytes(b"PRIVATE_ALTERNATE_AUTHORITY")
    elif fault == "state_alias":
        retained = tmp_path / "retained-current"
        root.rename(retained)
        root.symlink_to(retained, target_is_directory=True)
    elif fault == "ancestor_alias":
        retained = tmp_path / "retained-parent"
        root.parent.rename(retained)
        root.parent.symlink_to(retained, target_is_directory=True)
    elif fault == "candidate_relative":
        supplied_delivery = Path("relative-delivery")
    elif fault == "candidate_alias":
        supplied_delivery = tmp_path / "delivery-alias"
        supplied_delivery.symlink_to(delivery, target_is_directory=True)
    elif fault == "candidate_ancestor_alias":
        ancestor = tmp_path / "delivery-parent-alias"
        ancestor.symlink_to(tmp_path, target_is_directory=True)
        supplied_delivery = ancestor / delivery.name
    from executor.autonomy import state_compatibility
    def forbidden(*_args, **_kwargs):
        pytest.fail("unadmitted authority reached intake, task locks or activation")
    monkeypatch.setattr(distribution, "install_macos_distribution", forbidden)
    monkeypatch.setattr(state_compatibility, "_private_lock_fd", forbidden)
    monkeypatch.setattr(cli, "lifecycle", forbidden)
    before = sentinel.read_bytes()
    result = cli.update_installed_consumer(supplied_root, supplied_delivery)
    expected = ("macos_required" if fault == "platform" else
                "installed_update_state_unverified" if fault in {
                    "alternate_empty", "alternate_private", "state_alias", "ancestor_alias"} else
                "distribution_candidate_invalid" if fault.startswith("candidate_") else
                "installed_update_unverified")
    assert result == {"ok": False, "updated": False, "reason": expected,
                      "final_click_actor": "user", "submit_capability": False}
    assert sentinel.read_bytes() == before
    assert not (root / "native-window.lock").exists()
    assert not (root / "worker.lock").exists()
    assert not (root / "migration.lock").exists()
    assert not (app.parent / ("." + consumer.APP_NAME + ".app.transaction.lock")).exists()
    assert (delivery / "receipt-canary").read_text() == "PUBLIC_DELIVERY_PRESERVE"
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    if fault == "alternate_empty":
        assert not supplied_root.exists()
    if fault == "alternate_private":
        assert (supplied_root / "private-state").read_bytes() == b"PRIVATE_ALTERNATE_AUTHORITY"


@pytest.mark.parametrize("outcome", [
    {"ok": False, "reason": "task_state_in_use"},
    {"ok": False, "reason": "update_in_progress"},
    {"ok": False, "reason": "distribution_candidate_invalid"},
    {"ok": False, "reason": "manual_recovery_required"},
    {"ok": False, "reason": "PRIVATE_REASON", "path": "PRIVATE_PATH"},
    {"ok": 1, "installed": True, "replaced": True},
    {"ok": True, "installed": 1, "replaced": True},
    {"ok": True, "installed": True, "replaced": 1},
    {"ok": True, "installed": True, "replaced": False},
    None, "exception",
])
def test_installed_update_has_one_finite_private_safe_outcome_without_replay(
    tmp_path, monkeypatch, outcome
):
    app, _source, root, delivery, _identity, distribution = _installed_update_entry_fixture(
        tmp_path, monkeypatch)
    calls = []
    def transaction(candidate, *, destination, task_state_root):
        calls.append((candidate, destination, task_state_root))
        if outcome == "exception":
            raise OSError("PRIVATE_MOVE_EXCEPTION")
        return outcome
    monkeypatch.setattr(distribution, "install_macos_distribution", transaction)
    result = cli.update_installed_consumer(root, delivery)
    assert calls == [(delivery, app.parent, root)]
    reason = outcome.get("reason") if type(outcome) is dict else None
    known = {"task_state_in_use", "update_in_progress", "distribution_candidate_invalid",
             "manual_recovery_required"}
    assert result == {"ok": False, "updated": False,
                      "reason": reason if reason in known else "update_unconfirmed",
                      "final_click_actor": "user", "submit_capability": False}
    assert not root.exists()
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


def test_native_update_is_explicit_and_exclusive_without_launch_restore_or_restart(
    tmp_path, monkeypatch, capsys
):
    calls = []
    receipt = {"ok": True, "updated": True, "final_click_actor": "user",
               "submit_capability": False}
    monkeypatch.setattr(cli, "update_installed_consumer",
                        lambda root, candidate: calls.append((root, candidate)) or receipt)
    monkeypatch.setattr(cli, "restore_installed_consumer",
                        lambda *_: pytest.fail("update cannot become rollback"))
    monkeypatch.setattr(cli, "launch_native_consumer",
                        lambda *a, **kw: pytest.fail("update cannot start a service/window"))
    delivery = tmp_path / "Chosen Delivery"
    assert cli.main(["--runtime", str(tmp_path), "native-launch",
                     "--update-distribution", str(delivery)]) == 0
    assert calls == [(tmp_path, delivery)]
    calls.clear()
    for conflicting in ("--restore-previous", "--native-smoke"):
        with pytest.raises(SystemExit) as error:
            cli.main(["--runtime", str(tmp_path), "native-launch", conflicting,
                      "--update-distribution", str(delivery)])
        assert error.value.code == 2 and calls == []
    assert '"updated": true' in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["live", "isolated", "headless"])
def test_actual_serve_owns_selected_task_authority_before_business_initialization(
        tmp_path, monkeypatch, mode):
    from executor.autonomy import cli, queue as queue_module
    from executor.autonomy.state_compatibility import task_state_guard

    root = tmp_path / "selected-task-state"
    global_root = tmp_path / "separate-global-state"
    root.mkdir()
    retained = root / "retained-private"
    retained.write_bytes(b"SYNTHETIC_SELECTED_AUTHORITY_CANARY")
    monkeypatch.setattr(cli, "RUNTIME", global_root)
    monkeypatch.setattr(cli, "browser_mode", lambda: mode)

    def never(*args, **kwargs):
        pytest.fail("contended task authority reached business initialization")
    monkeypatch.setattr(queue_module, "TaskQueue", never)
    with task_state_guard(root):
        payload = {p.name: p.read_bytes() for p in root.iterdir()}
        inode = (root / "worker.lock").stat().st_ino
        with pytest.raises(RuntimeError, match="another local worker is active"):
            cli.serve(root, 9344)
        assert {p.name: p.read_bytes() for p in root.iterdir()} == payload
        assert (root / "worker.lock").stat().st_ino == inode
    assert not global_root.exists()
    assert retained.read_bytes() == b"SYNTHETIC_SELECTED_AUTHORITY_CANARY"
    assert not (root / "tasks.sqlite3").exists()
    assert not (root / "service.json").exists()


def test_actual_live_serve_retains_global_serialization_and_releases_selected_lock_on_refusal(
        tmp_path, monkeypatch):
    from executor.autonomy import cli, queue as queue_module
    from executor.autonomy.worker import ProcessLock
    from executor.autonomy.state_compatibility import task_state_guard

    root = tmp_path / "selected-task-state"
    global_root = tmp_path / "global-state"
    root.mkdir()
    retained = root / "retained-private"
    retained.write_bytes(b"SYNTHETIC_RETAINED_TASK_CANARY")
    monkeypatch.setattr(cli, "RUNTIME", global_root)
    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    def never(*args, **kwargs):
        pytest.fail("contended global browser authority reached business initialization")
    monkeypatch.setattr(queue_module, "TaskQueue", never)
    with ProcessLock(global_root / "worker.lock"):
        global_payload = {p.name: p.read_bytes() for p in global_root.iterdir()}
        with pytest.raises(RuntimeError, match="another local worker is active"):
            cli.serve(root, 9344)
        # Partial acquisition must not strand the selected journal's lease.
        with task_state_guard(root):
            pass
        assert {p.name: p.read_bytes() for p in global_root.iterdir()} == global_payload
    assert retained.read_bytes() == b"SYNTHETIC_RETAINED_TASK_CANARY"
    assert not (root / "tasks.sqlite3").exists()
    assert not (root / "service.json").exists()
    assert not (global_root / "service.json").exists()


def test_actual_live_serve_acquires_default_authority_only_once_before_queue_initialization(
        tmp_path, monkeypatch):
    from executor.autonomy import cli, queue as queue_module
    from executor.autonomy.state_compatibility import task_state_guard

    root = tmp_path / "same-authority"
    monkeypatch.setattr(cli, "RUNTIME", root)
    monkeypatch.setattr(cli, "browser_mode", lambda: "live")
    reached = []
    class ReachedQueue(Exception):
        pass
    def observe_queue(selected):
        assert selected == root
        reached.append(True)
        # The actual service already owns this journal before the constructor.
        with pytest.raises(BlockingIOError):
            with task_state_guard(root):
                pytest.fail("service did not hold its actual task lease")
        raise ReachedQueue()
    monkeypatch.setattr(queue_module, "TaskQueue", observe_queue)
    with pytest.raises(ReachedQueue):
        cli.serve(root, 9344)
    assert reached == [True]
    with task_state_guard(root):
        pass
    assert not (root / "tasks.sqlite3").exists()
    assert not (root / "service.json").exists()


@pytest.mark.parametrize("temporary_parent", ["canonical", "ancestor_alias"])
def test_real_candidate_health_canonicalizes_only_owned_temporary_authority(
    tmp_path, monkeypatch, temporary_parent
):
    """Real strict daemon, owned scratch journal and authenticated loopback."""
    from executor.autonomy.worker import ProcessLock

    canonical = tmp_path / "owned-health-temporaries"
    canonical.mkdir()
    alias = tmp_path / "temporary-parent-alias"
    alias.symlink_to(canonical, target_is_directory=True)
    supplied = alias if temporary_parent == "ancestor_alias" else canonical
    canary = canonical / "unrelated-temporary-canary"
    canary.write_bytes(b"PRIVATE_UNRELATED_TEMPORARY_UNCHANGED")
    canary.chmod(0o640)
    before = (canary.read_bytes(), canary.stat().st_mode, canary.stat().st_ino)
    source = tmp_path / "owned-complete-release"
    release.copy_source_candidate(Path(__file__).resolve().parents[1], source)
    assert verify_source_candidate(source)
    source_before = release.source_manifest(source)
    actual_temporary = consumer.tempfile.TemporaryDirectory
    actual_popen = consumer.subprocess.Popen
    created, children, requested = [], [], []

    def owned_temporary(*args, **kwargs):
        assert kwargs == {"prefix": "jae-candidate-health-"}
        context = actual_temporary(*args, **kwargs, dir=str(supplied))
        created.append(Path(context.name))
        if temporary_parent == "ancestor_alias":
            # The daemon still refuses arbitrary aliased caller roots.
            with pytest.raises(ValueError, match="worker_state_path_invalid"):
                with ProcessLock(Path(context.name) / "worker.lock"):
                    pytest.fail("the strict task-authority policy was weakened")
            assert not (Path(context.name) / "worker.lock").exists()
        return context

    def real_child(command, **kwargs):
        runtime = Path(command[-2])
        requested.append(runtime)
        assert runtime == created[-1].resolve(strict=True)
        assert not any(path.is_symlink() for path in (runtime, *runtime.parents))
        assert runtime.is_relative_to(canonical)
        assert kwargs["env"]["APPLICATION_EXECUTOR_BROWSER_MODE"] == "isolated"
        assert command[:4] == [sys.executable, "-I", "-B", "-c"]
        child = actual_popen(command, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(consumer.tempfile, "TemporaryDirectory", owned_temporary)
    monkeypatch.setattr(consumer.subprocess, "Popen", real_child)
    # No mock of serve, ProcessLock, dependency provenance, HTTP or health:
    # the returned True requires actual token auth, exact loaded source and
    # the user-only final actor on the existing loopback readiness path.
    assert _candidate_starts(Path(sys.executable), source) is True
    assert len(created) == len(children) == len(requested) == 1
    assert children[0].poll() is not None
    assert not created[0].exists() and not requested[0].exists()
    assert alias.is_symlink() and alias.readlink() == canonical
    assert list(canonical.iterdir()) == [canary]
    assert (canary.read_bytes(), canary.stat().st_mode, canary.stat().st_ino) == before
    assert release.source_manifest(source) == source_before
    assert verify_source_candidate(source)


@pytest.mark.parametrize("shape", ["root_alias", "ancestor_alias", "missing_descendant", "root_file", "foreign_root"])
@pytest.mark.parametrize("action", ["start", "restart", "health", "status", "stop", "launch"])
def test_service_parent_refuses_unadmitted_root_before_reads_or_effects(
    tmp_path, monkeypatch, shape, action
):
    outside = tmp_path / "PRIVATE_OUTSIDE_PARENT_AUTHORITY"
    outside.mkdir(mode=0o750)
    secret = outside / "auth.token"
    secret.write_text("PRIVATE_PARENT_TOKEN_" * 400)
    secret.chmod(0o640)
    (outside / "service.log").write_bytes(b"PRIVATE_PARENT_LOG_KEEP")
    alias = tmp_path / "runtime-alias"
    alias.symlink_to(outside, target_is_directory=True)
    if shape == "root_alias":
        root = alias
    elif shape == "ancestor_alias":
        (outside / "child").mkdir()
        root = alias / "child"
    elif shape == "missing_descendant":
        root = alias / "not-created" / "runtime"
    elif shape == "foreign_root":
        root = outside
        owner = os.geteuid()
        monkeypatch.setattr(cli.os, "geteuid", lambda: owner + 1)
    else:
        root = tmp_path / "not-a-runtime-directory"
        root.write_bytes(b"PRIVATE_ORDINARY_ROOT_KEEP")
    before = _retirement_inventory(tmp_path)

    def forbidden(*_args, **_kwargs):
        pytest.fail("root refusal must precede token, network, log, browser and process actions")
    for name in ("request", "local_token", "private_dir", "_stop_owned_service",
                 "ensure_chrome", "_consumer_release_identity", "open_ui"):
        monkeypatch.setattr(cli, name, forbidden)
    monkeypatch.setattr(cli.subprocess, "Popen", forbidden)
    result = (cli.launch_consumer(root, 9344) if action == "launch"
              else cli.lifecycle(action, root, 9344))
    assert result["ok"] is False
    assert result.get("bootstrap_reason", result.get("reason")) == "service_runtime_unavailable"
    if action == "launch":
        assert result["opened"] is False
        assert result["ready_for_live_e2e"] is False
        assert result["final_click_actor"] == "user" and result["submit_capability"] is False
    assert "PRIVATE" not in json.dumps(result)
    assert _retirement_inventory(tmp_path) == before
    assert not (outside / "not-created").exists()


@pytest.mark.parametrize("defect", ["symlink", "hardlink", "fifo", "directory",
                                   "public", "foreign", "replaced", "oversized"])
def test_shared_runtime_token_refuses_bad_inode_before_secret_use(
    tmp_path, monkeypatch, defect
):
    from executor.autonomy import runtime_paths

    monkeypatch.delenv("APPLICATION_EXECUTOR_LOCAL_TOKEN", raising=False)
    root = tmp_path / "owned-runtime"
    root.mkdir(mode=0o700)
    path = root / "auth.token"
    outside = tmp_path / "PRIVATE_TOKEN_OUTSIDE"
    outside.write_bytes(b"PRIVATE_SECRET_TOKEN_PAYLOAD_" * 400)
    outside.chmod(0o600)
    if defect == "symlink":
        path.symlink_to(outside)
    elif defect == "hardlink":
        os.link(outside, path)
    elif defect == "fifo":
        os.mkfifo(path, 0o600)
    elif defect == "directory":
        path.mkdir(mode=0o700)
    else:
        path.write_text("A" * (4097 if defect == "oversized" else 43))
        path.chmod(0o644 if defect == "public" else 0o600)
    before = _retirement_inventory(tmp_path)
    actual_open, actual_fstat = os.open, os.fstat
    descriptors, after_race = [], []

    def opened(value, flags, *args, **kwargs):
        fd = actual_open(value, flags, *args, **kwargs)
        if Path(value) == path:
            descriptors.append(fd)
            if defect == "replaced":
                path.rename(root / "displaced-original-token")
                path.write_text("PRIVATE_REPLACEMENT_TOKEN_" * 3)
                path.chmod(0o640)
                after_race.append(_retirement_inventory(tmp_path))
        return fd

    def metadata(fd):
        observed = actual_fstat(fd)
        if defect == "foreign" and fd in descriptors:
            values = list(observed)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(runtime_paths.os, "open", opened)
    monkeypatch.setattr(runtime_paths.os, "fstat", metadata)
    if defect != "oversized":
        monkeypatch.setattr(runtime_paths.os, "fdopen",
            lambda *_args, **_kwargs: pytest.fail("inadmissible token must not open a payload stream"))
    with pytest.raises(ValueError, match=("invalid private auth token" if defect == "oversized"
                                          else "private token permissions required")):
        runtime_paths.local_token(root)
    for fd in descriptors:
        with pytest.raises(OSError):
            fcntl.fcntl(fd, fcntl.F_GETFD)
    assert _retirement_inventory(tmp_path) == (after_race[0] if after_race else before)
    assert outside.read_bytes() == b"PRIVATE_SECRET_TOKEN_PAYLOAD_" * 400


@pytest.mark.parametrize("defect", ["symlink", "hardlink", "fifo", "directory", "foreign", "replaced"])
def test_service_parent_refuses_bad_log_before_chmod_or_child_start(
    tmp_path, monkeypatch, defect
):
    from executor.autonomy import runtime_paths

    monkeypatch.delenv("APPLICATION_EXECUTOR_LOCAL_TOKEN", raising=False)
    root = tmp_path / "owned-runtime"
    root.mkdir(mode=0o700)
    token = root / "auth.token"
    token.write_text("A" * 43)
    token.chmod(0o600)
    log = root / "service.log"
    outside = tmp_path / "PRIVATE_LOG_OUTSIDE"
    outside.write_bytes(b"PRIVATE_LOG_OUTSIDE_UNCHANGED")
    outside.chmod(0o640)
    if defect == "symlink":
        log.symlink_to(outside)
    elif defect == "hardlink":
        os.link(outside, log)
    elif defect == "fifo":
        os.mkfifo(log, 0o600)
    elif defect == "directory":
        log.mkdir(mode=0o700)
    else:
        log.write_bytes(b"PRIVATE_OLD_LOG_KEEP")
        log.chmod(0o640)
    before = _retirement_inventory(tmp_path)
    actual_open, actual_fstat = os.open, os.fstat
    descriptors, after_race = [], []

    def opened(value, flags, *args, **kwargs):
        fd = actual_open(value, flags, *args, **kwargs)
        if Path(value) == log:
            descriptors.append(fd)
            if defect == "replaced":
                log.rename(root / "displaced-original-log")
                log.write_bytes(b"PRIVATE_REPLACEMENT_LOG_KEEP")
                log.chmod(0o640)
                after_race.append(_retirement_inventory(tmp_path))
        return fd

    def metadata(fd):
        observed = actual_fstat(fd)
        if defect == "foreign" and fd in descriptors:
            values = list(observed)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(runtime_paths.os, "open", opened)
    monkeypatch.setattr(runtime_paths.os, "fstat", metadata)
    monkeypatch.setattr(cli, "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic service absent")))
    monkeypatch.setattr(cli.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("inadmissible log must not start a child"))
    result = cli.lifecycle("start", root, 9344)
    assert result == {"ok": False, "reason": "service_runtime_unavailable"}
    for fd in descriptors:
        with pytest.raises(OSError):
            fcntl.fcntl(fd, fcntl.F_GETFD)
    assert _retirement_inventory(tmp_path) == (after_race[0] if after_race else before)
    assert outside.read_bytes() == b"PRIVATE_LOG_OUTSIDE_UNCHANGED"


def test_owned_service_log_preserves_append_and_private_descriptor(tmp_path):
    from executor.autonomy.runtime_paths import private_service_log

    root = tmp_path / "owned-runtime"
    root.mkdir(mode=0o750)
    log = root / "service.log"
    log.write_bytes(b"SYNTHETIC_EXISTING_LOG\n")
    log.chmod(0o640)
    inode = log.stat().st_ino
    with private_service_log(root) as stream:
        fd = stream.fileno()
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        stream.write(b"SYNTHETIC_APPEND\n")
    with pytest.raises(OSError):
        fcntl.fcntl(fd, fcntl.F_GETFD)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert log.stat().st_ino == inode
    assert log.read_bytes() == b"SYNTHETIC_EXISTING_LOG\nSYNTHETIC_APPEND\n"


def test_actual_service_parent_start_health_and_authenticated_stop_keep_authority(
    tmp_path, monkeypatch
):
    from executor.autonomy.worker import ProcessLock

    monkeypatch.setenv("APPLICATION_EXECUTOR_BROWSER_MODE", "isolated")
    monkeypatch.delenv("APPLICATION_EXECUTOR_LOCAL_TOKEN", raising=False)
    root = tmp_path / "actual-parent-start"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    try:
        started = cli.lifecycle("start", root, port)
        assert started["ok"] is True
        health = cli.lifecycle("health", root, port)
        assert health["ok"] is True and health["final_click_actor"] == "user"
        identity = cli.request(root, port, "/v1/service-identity")
        assert json.loads((root / "service.json").read_text()) == identity
        assert identity["port"] == port
        with pytest.raises(RuntimeError, match="another local worker"):
            with ProcessLock(root / "worker.lock"):
                pytest.fail("actual started daemon lost its selected task authority")
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / "auth.token").stat().st_mode) == 0o600
        assert stat.S_IMODE((root / "service.log").stat().st_mode) == 0o600
        token = (root / "auth.token").read_bytes()
        assert cli.lifecycle("stop", root, port) == {"ok": True, "running": False}
        assert not (root / "service.json").exists()
        assert (root / "auth.token").read_bytes() == token
        with ProcessLock(root / "worker.lock"):
            pass
    finally:
        # Use only the existing authenticated exact-instance retirement path.
        # No PID signalling, browser adoption or private user authority.
        cli.lifecycle("stop", root, port)


@pytest.mark.parametrize("defect", [
    "symlink", "hardlink", "fifo", "directory", "foreign", "replaced", "ancestor_replaced",
])
def test_bootstrap_log_admission_refuses_before_spawn_and_preserves_all_authority(
        retirement_private_state, tmp_path, monkeypatch, defect):
    from executor.autonomy import runtime_paths

    root, queue, db, key = retirement_private_state
    log = root / "bootstrap.log"
    outside = tmp_path / "outside-recovery-log"
    outside.write_bytes(b"PRIVATE_RECOVERY_LOG_OUTSIDE_KEEP" * 400)
    outside.chmod(0o640)
    if defect == "symlink":
        log.symlink_to(outside)
    elif defect == "hardlink":
        os.link(outside, log)
    elif defect == "fifo":
        os.mkfifo(log, 0o600)
    elif defect == "directory":
        log.mkdir(mode=0o700)
    else:
        log.write_bytes(b"PRIVATE_EXISTING_RECOVERY_LOG_KEEP" * 400)
        log.chmod(0o640)
    # Keep the real ordinary bootstrap lease so no expected lock creation is
    # confused with an authority mutation during negative log admission.
    lease = root / "bootstrap-service.lock"
    lease.write_bytes(b"")
    lease.chmod(0o600)
    authority = _retirement_authority(root, db)
    before = _retirement_inventory(tmp_path)
    actual_open, actual_fstat = os.open, os.fstat
    descriptors, after_race = [], []
    displaced_root = tmp_path / "displaced-recovery-authority"

    def opened(path, flags, *args, **kwargs):
        fd = actual_open(path, flags, *args, **kwargs)
        if Path(path) == log:
            descriptors.append(fd)
            if defect == "replaced":
                log.rename(root / "displaced-original-bootstrap-log")
                log.write_bytes(b"PRIVATE_NEW_BOOTSTRAP_LOG_KEEP")
                log.chmod(0o640)
                after_race.append(_retirement_inventory(tmp_path))
            elif defect == "ancestor_replaced":
                root.rename(displaced_root)
                root.symlink_to(displaced_root, target_is_directory=True)
                after_race.append(_retirement_inventory(tmp_path))
        return fd

    def metadata(fd):
        observed = actual_fstat(fd)
        if defect == "foreign" and fd in descriptors:
            values = list(observed)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return observed

    def forbidden(*args, **kwargs):
        pytest.fail("unadmitted recovery log must not start, contact or present a service")

    monkeypatch.setattr(runtime_paths.os, "open", opened)
    monkeypatch.setattr(runtime_paths.os, "fstat", metadata)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", forbidden)
    monkeypatch.setattr(bootstrap.urllib.request, "build_opener", forbidden)
    result = bootstrap.open_bootstrap(root, 9344, presenter=forbidden)
    assert result == {"ok": False, "opened": False, "reason": "bootstrap_state_invalid"}
    assert "PRIVATE" not in json.dumps(result)
    for fd in descriptors:
        with pytest.raises(OSError):
            fcntl.fcntl(fd, fcntl.F_GETFD)
    _assert_retirement_inventory(tmp_path, after_race[0] if after_race else before)
    _assert_retirement_authority(
        displaced_root if defect == "ancestor_replaced" else root, db, authority)
    assert not (root / "bootstrap.json").exists()
    assert outside.read_bytes() == b"PRIVATE_RECOVERY_LOG_OUTSIDE_KEEP" * 400


@pytest.mark.parametrize("name", ["service.log", "bootstrap.log"])
def test_fixed_private_runtime_log_preserves_actual_append_and_closes_descriptor(
        tmp_path, name):
    from executor.autonomy.runtime_paths import private_runtime_log

    root = tmp_path / "runtime"
    root.mkdir(mode=0o750)
    log = root / name
    original = b"PRIVATE_OWNED_LOG_BYTES_KEEP" * 400
    log.write_bytes(original)
    log.chmod(0o640)
    identity = (log.stat().st_dev, log.stat().st_ino)
    with private_runtime_log(root, name) as stream:
        fd = stream.fileno()
        assert (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == identity
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        stream.write(b"\nSYNTHETIC_APPEND_ONLY\n")
    with pytest.raises(OSError):
        fcntl.fcntl(fd, fcntl.F_GETFD)
    assert log.read_bytes() == original + b"\nSYNTHETIC_APPEND_ONLY\n"
    assert (log.stat().st_dev, log.stat().st_ino) == identity
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


@pytest.mark.parametrize("name", [
    "../service.log", "/tmp/unowned.log", "unknown.log", "", None, [], "service.log/sub",
])
def test_private_runtime_log_names_refuse_before_any_directory_effect(
        tmp_path, monkeypatch, name):
    from executor.autonomy import runtime_paths

    root = tmp_path / "not-created-runtime"
    outside = tmp_path / "outside"
    outside.write_bytes(b"PRIVATE_NOT_A_LOG_KEEP" * 400)
    outside.chmod(0o640)
    before = _retirement_inventory(tmp_path)
    monkeypatch.setattr(runtime_paths, "private_dir",
        lambda *_args, **_kwargs: pytest.fail("unknown log name reached directory authority"))
    with pytest.raises(ValueError, match="^private runtime unavailable$"):
        runtime_paths.private_runtime_log(root, name)
    assert not root.exists()
    _assert_retirement_inventory(tmp_path, before)


@pytest.mark.parametrize("shape", ["root", "ancestor"])
def test_locked_bootstrap_entry_never_canonicalizes_an_unadmitted_caller_root(
        retirement_private_state, tmp_path, monkeypatch, shape):
    root, queue, db, key = retirement_private_state
    alias = tmp_path / "locked-entry-alias"
    alias.symlink_to(root if shape == "root" else tmp_path, target_is_directory=True)
    selected = alias if shape == "root" else alias / root.name
    authority = _retirement_authority(root, db)
    before = _retirement_inventory(tmp_path)
    monkeypatch.setattr(bootstrap, "LoopbackHTTPServer",
        lambda *_args, **_kwargs: pytest.fail("caller alias reached recovery server bind"))
    with pytest.raises(ValueError, match="^private runtime unavailable$"):
        bootstrap._serve_bootstrap_locked(selected, 9344, "service_unavailable")
    _assert_retirement_inventory(tmp_path, before)
    _assert_retirement_authority(root, db, authority)
    assert not (root / "bootstrap.json").exists()


def _native_handoff_entry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from executor.autonomy import runtime_paths
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    source = app / "Contents" / "Resources" / "release"
    module = source / "executor" / "autonomy" / "cli.py"
    module.parent.mkdir(parents=True)
    module.write_text("# synthetic entry routing admission only\n")
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    executable.parent.mkdir(parents=True)
    executable.write_text(consumer._native_packaged_launcher())
    root = tmp_path / "owned-task-state"
    root.mkdir()
    canary = root / "retained-private"
    canary.write_bytes(b"PRIVATE_NATIVE_HANDOFF_AUTHORITY" * 1000)
    monkeypatch.setattr(cli, "__file__", str(module))
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(cli, "_consumer_release_identity",
                        lambda: {"packaged": True, "expected": "a" * 64})
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda p: p == app)
    monkeypatch.setattr(runtime_paths, "default_runtime", lambda p: root)
    return app, source, executable, root, canary


@pytest.mark.parametrize("action", ["restore", "update"])
def test_native_foreground_handoff_releases_real_window_lease_before_safe_stop_and_transaction(
    tmp_path, monkeypatch, action
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from executor.autonomy import macos_host, native_reopen, state_compatibility
    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    baseline = canary.read_bytes()
    calls = []
    actual_guard = state_compatibility.native_window_guard
    @contextmanager
    def observed_guard(authority):
        with actual_guard(authority) as fd:
            calls.append("lease_enter")
            yield fd
        calls.append("lease_exit")
    monkeypatch.setattr(state_compatibility, "native_window_guard", observed_guard)
    intent = ({"action": "restore"} if action == "restore" else
              {"action": "update", "distribution": str(tmp_path / "Chosen Delivery")})
    class Presenter:
        def __init__(self, directory, **kwargs):
            assert directory == source.parent / "native-host"
            assert type(kwargs["ownership_fd"]) is int
            calls.append("presenter")
        def focus(self):
            pytest.fail("handoff cannot focus another window")
        def wait_for_close(self):
            with pytest.raises(BlockingIOError):
                with actual_guard(root):
                    pytest.fail("window released its parent lease too early")
            calls.append("window_exit")
            return True
        def take_release_request(self):
            calls.append("intent")
            return intent
        def close(self):
            calls.append("presenter_close")
    class Reopen:
        def start(self):
            calls.append("ipc_start")
        def close(self):
            calls.append("ipc_close")
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    monkeypatch.setattr(native_reopen, "create_owned_reopen_server", lambda *a: Reopen())
    monkeypatch.setattr(cli, "launch_consumer", lambda *a, **k: {"ok": True, "opened": True})
    def stopped(authority, port):
        assert authority == root and port == 9344
        assert calls[-1] == "lease_exit"
        with actual_guard(root):
            pass
        calls.append("stop")
        return {"ok": True, "stopped": True}
    monkeypatch.setattr(cli, "_stop_owned_service", stopped)
    def transact(authority, candidate=None):
        assert authority == root and calls[-1] == "stop"
        assert candidate == (intent.get("distribution"))
        with state_compatibility.task_state_guard(root):
            pass
        calls.append("transaction")
        return {"ok": True, "restored" if action == "restore" else "updated": True,
                "final_click_actor": "user", "submit_capability": False}
    monkeypatch.setattr(cli, "restore_installed_consumer",
        transact if action == "restore" else lambda *a: pytest.fail("update cannot restore"))
    monkeypatch.setattr(cli, "update_installed_consumer",
        transact if action == "update" else lambda *a: pytest.fail("restore cannot update"))
    def reopened(command, **kwargs):
        assert calls[-1] == "transaction"
        from executor.autonomy.process_entry import CLI_ENTRY_SCRIPT
        assert command == [str(app / "Contents/Resources/runtime/bin/python"),
                           "-I", "-B", "-c", CLI_ENTRY_SCRIPT, str(source),
                           "--runtime", str(root), "--port", "9344", "native-launch"]
        assert kwargs["cwd"] == source
        assert kwargs["start_new_session"] is True
        assert all(kwargs[key] == -3 for key in ("stdin", "stdout", "stderr"))
        # The transaction guard also owns native-window.lock. Prove both
        # authorities are free sequentially; nesting intentionally contends.
        with actual_guard(root):
            with pytest.raises(BlockingIOError):
                with state_compatibility.task_state_guard(root):
                    pytest.fail("activation must still refuse a live window")
        with state_compatibility.task_state_guard(root):
            with pytest.raises(BlockingIOError):
                with actual_guard(root):
                    pytest.fail("reopen must still refuse a live transaction")
        calls.append("reopen_active")
        return object()
    monkeypatch.setattr(cli, "subprocess", SimpleNamespace(Popen=reopened, DEVNULL=-3))
    result = cli.launch_native_consumer(root, 9344)
    assert calls == ["lease_enter", "presenter", "ipc_start", "window_exit", "intent",
                     "ipc_close", "presenter_close", "lease_exit", "stop",
                     "transaction", "reopen_active"]
    assert result == {"ok": True, "restored" if action == "restore" else "updated": True,
                      "final_click_actor": "user", "submit_capability": False,
                      "reopen_requested": True}
    assert canary.read_bytes() == baseline
    assert not (root / "tasks.sqlite3").exists()
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("reason", [
    "worker_active", "otp_in_flight", "runnable_task_pending",
    "worker_stopping_at_safe_checkpoint", "service_identity_changed",
    "service_identity_unverified", "update_in_progress",
])
def test_native_release_busy_or_unowned_service_never_enters_transaction_or_retries(
    tmp_path, monkeypatch, reason
):
    from types import SimpleNamespace
    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    before = canary.read_bytes()
    calls = []
    monkeypatch.setattr(cli, "_stop_owned_service",
                        lambda *a: calls.append("stop") or {"ok": False, "reason": reason})
    monkeypatch.setattr(cli, "restore_installed_consumer", lambda *a: pytest.fail("busy transaction"))
    monkeypatch.setattr(cli, "update_installed_consumer", lambda *a: pytest.fail("busy update"))
    monkeypatch.setattr(cli, "subprocess",
        SimpleNamespace(Popen=lambda *a, **k: pytest.fail("busy reopen"), DEVNULL=-3))
    result = cli.handoff_installed_consumer(root, 9344, {"action": "restore"})
    assert result == {"ok": False, "reason": reason,
                      "final_click_actor": "user", "submit_capability": False}
    assert calls == ["stop"] and canary.read_bytes() == before
    assert not (root / "worker.lock").exists()


@pytest.mark.parametrize("fault", ["foreign_root", "checkout", "alias", "unverified", "legacy_launcher"])
def test_native_handoff_refuses_unadmitted_installation_before_service_retirement(
    tmp_path, monkeypatch, fault
):
    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    before = canary.read_bytes()
    supplied = root
    if fault == "foreign_root":
        supplied = tmp_path / "other"
    elif fault == "checkout":
        monkeypatch.setattr(cli, "__file__", str(tmp_path / "executor/autonomy/cli.py"))
    elif fault == "alias":
        supplied = tmp_path / "alias"
        supplied.symlink_to(root, target_is_directory=True)
    elif fault == "unverified":
        monkeypatch.setattr(cli, "_consumer_release_identity",
                            lambda: {"packaged": True, "expected": ""})
    elif fault == "legacy_launcher":
        executable.write_text("unadmitted launcher")
    monkeypatch.setattr(cli, "_stop_owned_service", lambda *a: pytest.fail("unadmitted service stop"))
    monkeypatch.setattr(cli, "restore_installed_consumer", lambda *a: pytest.fail("unadmitted rollback"))
    result = cli.handoff_installed_consumer(supplied, 9344, {"action": "restore"})
    assert result["ok"] is False and result["reason"] == "native_release_installation_unverified"
    assert canary.read_bytes() == before and not (root / "worker.lock").exists()


@pytest.mark.parametrize("fault", ["refusal", "unconfirmed", "active_changed", "reopen_failed"])
def test_native_handoff_never_replays_transaction_or_claims_unobserved_readiness(
    tmp_path, monkeypatch, fault
):
    from types import SimpleNamespace
    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "_stop_owned_service",
                        lambda *a: calls.append("stop") or {"ok": False, "reason": "service_record_missing"})
    def transact(authority):
        calls.append("transaction")
        if fault == "refusal":
            return {"ok": False, "restored": False, "reason": "rollback_state_incompatible",
                    "final_click_actor": "user", "submit_capability": False}
        if fault == "unconfirmed":
            raise OSError("PRIVATE_TRANSACTION_PATH")
        if fault == "active_changed":
            executable.write_text("changed activated slot")
        return {"ok": True, "restored": True, "final_click_actor": "user", "submit_capability": False}
    monkeypatch.setattr(cli, "restore_installed_consumer", transact)
    def reopen(*args, **kwargs):
        calls.append("reopen")
        if fault == "reopen_failed":
            raise OSError("PRIVATE_REOPEN_PATH")
        pytest.fail("refused/changed release cannot reopen")
    monkeypatch.setattr(cli, "subprocess", SimpleNamespace(Popen=reopen, DEVNULL=-3))
    before = canary.read_bytes()
    result = cli.handoff_installed_consumer(root, 9344, {"action": "restore"})
    assert calls == ["stop", "transaction"] + (["reopen"] if fault == "reopen_failed" else [])
    assert result.get("reopen_requested") is not True
    assert "ready_for_live_e2e" not in result and "native_page" not in result
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert canary.read_bytes() == before

@pytest.mark.parametrize("artifact", ["tasks.sqlite3-wal", "task-answers.key"])
def test_legacy_authority_arriving_during_candidate_health_refuses_before_first_app_move(
    tmp_path, monkeypatch, artifact
):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    apps = tmp_path / "Applications"
    state = repo / "runtime" / "autonomy"
    target = tmp_path / "new-owned-state"
    real_start = consumer._candidate_starts
    starts = []
    def start_then_legacy_arrives(runtime_python, release_root):
        assert real_start(runtime_python, release_root)
        assert not state.exists()
        state.mkdir(parents=True)
        (state / artifact).write_bytes(b"PRIVATE_LATE_LEGACY_AUTHORITY")
        starts.append((runtime_python, release_root))
        return True
    monkeypatch.setattr(consumer, "_candidate_starts", start_then_legacy_arrives)
    result = install_macos_app(repo, destination=apps, platform="darwin",
                               task_state_root=target)
    assert len(starts) == 1
    assert result["ok"] is False
    assert result["reason"] == "legacy_state_migration_required"
    assert (state / artifact).read_bytes() == b"PRIVATE_LATE_LEGACY_AUTHORITY"
    assert "PRIVATE_LATE" not in json.dumps(result)
    assert str(state) not in json.dumps(result)
    assert not (apps / (consumer.APP_NAME + ".app")).exists()
    assert not (apps / ("." + consumer.APP_NAME + ".app.previous")).exists()
    assert (apps / ("." + consumer.APP_NAME + ".app.installing")).is_dir()


def test_hardlinked_legacy_lock_cannot_certify_an_empty_old_authority(tmp_path, monkeypatch):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    _minimal_source(repo)
    state = repo / "runtime" / "autonomy"
    state.mkdir(parents=True)
    unrelated = tmp_path / "unrelated-private-lock"
    unrelated.write_bytes(b"PRIVATE_UNRELATED_LOCK")
    os.link(unrelated, state / "worker.lock")
    assert (state / "worker.lock").stat().st_nlink == 2
    apps = tmp_path / "Applications"
    target = tmp_path / "new-owned-state"
    with pytest.raises(ValueError, match="legacy_state_path_invalid"):
        consumer._legacy_state_migration_needed(repo, apps / (consumer.APP_NAME + ".app"), target)
    monkeypatch.setattr(consumer, "_candidate_starts",
                        lambda *_: pytest.fail("aliased old authority cannot start candidate"))
    result = install_macos_app(repo, destination=apps, platform="darwin",
                               task_state_root=target)
    assert result["ok"] is False
    assert result["reason"] == "legacy_state_unavailable"
    assert unrelated.read_bytes() == b"PRIVATE_UNRELATED_LOCK"
    assert "PRIVATE_" not in json.dumps(result)
    assert not (apps / (consumer.APP_NAME + ".app")).exists()
    assert not (apps / ("." + consumer.APP_NAME + ".app.installing")).exists()


@pytest.mark.parametrize("action", ["restore", "update"])
@pytest.mark.parametrize("fault", ["launcher_changed", "bundle_alias"])
def test_native_handoff_never_claims_completion_after_activated_slot_loses_authority(
    tmp_path, monkeypatch, action, fault
):
    from types import SimpleNamespace
    app, source, executable, root, canary = _native_handoff_entry(tmp_path, monkeypatch)
    before = canary.read_bytes()
    calls = []

    def stopped(authority, port):
        assert authority == root and port == 9344
        calls.append("stop")
        return {"ok": False, "reason": "service_record_missing"}
    monkeypatch.setattr(cli, "_stop_owned_service", stopped)

    distribution = str(tmp_path / "Chosen Delivery")
    def transact(authority, candidate=None):
        assert authority == root and candidate == (distribution if action == "update" else None)
        calls.append("transaction")
        if fault == "launcher_changed":
            executable.write_text("changed activated slot")
        else:
            retained = tmp_path / "retained-altered-app"
            app.rename(retained)
            app.symlink_to(retained, target_is_directory=True)
        return {"ok": True, "restored" if action == "restore" else "updated": True,
                "final_click_actor": "user", "submit_capability": False}
    monkeypatch.setattr(cli, "restore_installed_consumer",
        transact if action == "restore" else lambda *args: pytest.fail("restore replay"))
    monkeypatch.setattr(cli, "update_installed_consumer",
        transact if action == "update" else lambda *args: pytest.fail("update replay"))
    monkeypatch.setattr(cli, "subprocess", SimpleNamespace(
        Popen=lambda *args, **kwargs: pytest.fail("unverified slot reopened"), DEVNULL=-3))

    intent = ({"action": "restore"} if action == "restore" else
              {"action": "update", "distribution": distribution})
    result = cli.handoff_installed_consumer(root, 9344, intent)
    assert calls == ["stop", "transaction"]
    assert result == {"ok": False, "restored" if action == "restore" else "updated": False,
                      "reopen_requested": False, "reason": "activated_app_unverified",
                      "final_click_actor": "user", "submit_capability": False}
    assert canary.read_bytes() == before
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)

@pytest.mark.parametrize("control", ["Info.plist", "AIApplicationManager"])
def test_bundle_control_hardlink_refuses_before_external_inode_read(tmp_path, monkeypatch, control):
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    info = app / "Contents" / "Info.plist"
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": consumer.BUNDLE_ID,
                       "CFBundleExecutable": "AIApplicationManager"}, handle)
    repo = tmp_path / "Job-Application-Executor"
    launcher = macos / "AIApplicationManager"
    launcher.write_text(consumer._legacy_launcher(repo), encoding="utf-8")
    launcher.chmod(0o755)
    assert consumer._trusted_bundle(app)
    assert consumer._bundle_transaction_identity(app) is not None

    target = info if control == "Info.plist" else launcher
    outside = tmp_path / ("outside-" + control)
    original = target.read_bytes()
    outside.write_bytes(original)
    outside.chmod(stat.S_IMODE(target.stat().st_mode))
    target.unlink()
    os.link(outside, target)
    assert target.stat().st_nlink == 2
    original_open, original_text, original_bytes = Path.open, Path.read_text, Path.read_bytes

    def no_external_open(path, *args, **kwargs):
        if path == target:
            pytest.fail("bundle must refuse an external inode before open")
        return original_open(path, *args, **kwargs)

    def no_external_text(path, *args, **kwargs):
        if path == target:
            pytest.fail("bundle must refuse an external inode before text read")
        return original_text(path, *args, **kwargs)

    def no_external_bytes(path, *args, **kwargs):
        if path == target:
            pytest.fail("bundle must refuse an external inode before bytes read")
        return original_bytes(path, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", no_external_open)
        guard.setattr(Path, "read_text", no_external_text)
        guard.setattr(Path, "read_bytes", no_external_bytes)
        assert consumer._trusted_bundle(app) is False
        assert consumer._bundle_transaction_identity(app) is None
        if control == "AIApplicationManager":
            assert consumer._legacy_bundle_matches(launcher) is False
            assert consumer._packaged_bundle_matches(launcher, resources / "runtime") is False
            assert consumer._isolated_bundle_startup(app) is False
    assert outside.read_bytes() == original
    assert target.read_bytes() == original

@pytest.mark.parametrize("control", ["Info.plist", "AIApplicationManager"])
def test_bundle_control_swap_after_precheck_never_reads_external_inode(
    tmp_path, monkeypatch, control
):
    app = tmp_path / "Applications" / (consumer.APP_NAME + ".app")
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    info = app / "Contents" / "Info.plist"
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": consumer.BUNDLE_ID,
                       "CFBundleExecutable": "AIApplicationManager"}, handle)
    repo = tmp_path / "Job-Application-Executor"
    launcher = macos / "AIApplicationManager"
    launcher.write_text(consumer._legacy_launcher(repo), encoding="utf-8")
    launcher.chmod(0o755)
    assert consumer._trusted_bundle(app)
    target = info if control == "Info.plist" else launcher
    outside = tmp_path / ("external-" + control)
    original = target.read_bytes()
    outside.write_bytes(original)
    outside.chmod(stat.S_IMODE(target.stat().st_mode))
    actual_owned = consumer._owned_bundle_file
    actual_open, actual_text, actual_fdopen = Path.open, Path.read_text, os.fdopen
    inspections = 0

    def swap_after_owned_precheck(path):
        nonlocal inspections
        admitted = actual_owned(path)
        if path == target:
            inspections += 1
            # Info is read after its first check. The legacy launcher has a
            # second check inside _legacy_bundle_matches just before reading.
            if inspections == (1 if control == "Info.plist" else 2):
                assert admitted
                target.unlink()
                os.link(outside, target)
        return admitted

    def refuse_path_open(path, *args, **kwargs):
        if path == target:
            pytest.fail("external control inode must not be read")
        return actual_open(path, *args, **kwargs)

    def refuse_path_text(path, *args, **kwargs):
        if path == target:
            pytest.fail("external launcher inode must not be read")
        return actual_text(path, *args, **kwargs)

    def refuse_external_fdopen(fd, *args, **kwargs):
        if os.fstat(fd).st_ino == outside.stat().st_ino:
            pytest.fail("external control descriptor must not be read")
        return actual_fdopen(fd, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(consumer, "_owned_bundle_file", swap_after_owned_precheck)
        guard.setattr(Path, "open", refuse_path_open)
        guard.setattr(Path, "read_text", refuse_path_text)
        guard.setattr(os, "fdopen", refuse_external_fdopen)
        assert consumer._trusted_bundle(app) is False
    assert inspections >= (1 if control == "Info.plist" else 2)
    assert outside.read_bytes() == original
    assert target.read_bytes() == original
