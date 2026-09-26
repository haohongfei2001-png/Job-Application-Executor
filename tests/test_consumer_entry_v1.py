from __future__ import annotations

import json
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
    from executor.autonomy import queue

    actual = queue.default_runtime
    monkeypatch.setattr(queue, "default_runtime",
                        lambda source, *, home=None: actual(
                            source, home=home if home is not None else tmp_path / "synthetic-home"))


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
    assert "检查并更新" in DASHBOARD_HTML
    assert "/ui/api/diagnostics" in DASHBOARD_HTML
    assert "/ui/api/update" in DASHBOARD_HTML
    assert "otp_in_flight" in DASHBOARD_HTML


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
        return real_start(runtime_python, source) if attempts == 1 else False

    monkeypatch.setattr(consumer, "_candidate_starts", fail_after_move)
    result = rollback_macos_app(apps)

    assert attempts == 2
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


@pytest.mark.parametrize("destructive", [False, True, "schema"])
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
        db.commit()
        before = (db.execute("SELECT * FROM tasks").fetchall(),
                  db.execute("SELECT * FROM events").fetchall())
        assert (state / "tasks.sqlite3-wal").stat().st_size > 0

        if destructive:
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
            starts.append(source)
            return real_start(runtime_python, source)

        monkeypatch.setattr(consumer, "_candidate_starts", checked_start)
        result = install_macos_app(
            repo, destination=apps, platform="darwin", task_state_root=state)
        assert (db.execute("SELECT * FROM tasks").fetchall(),
                db.execute("SELECT * FROM events").fetchall()) == before
        assert "revision" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
        assert not (state / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
        if destructive:
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
