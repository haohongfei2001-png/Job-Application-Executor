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

from executor.autonomy import bootstrap, cli, preflight, release
from executor.autonomy.consumer import install_macos_app, rollback_macos_app
from executor.autonomy.loopback_http import LoopbackHTTPServer
from executor.autonomy.release import verify_source_candidate
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


def _minimal_source(repo):
    package = repo / "executor"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "consumer_entry.py").write_text("VERSION = 'fixture'\n", encoding="utf-8")
    autonomy = package / "autonomy"
    autonomy.mkdir()
    (autonomy / "__init__.py").write_text("", encoding="utf-8")
    (autonomy / "cli.py").write_text("VERSION = 'fixture'\n", encoding="utf-8")
    (autonomy / "release.py").write_text(
        (Path(__file__).resolve().parents[1] / "executor" / "autonomy" / "release.py")
        .read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / "requirements.txt").write_text(f"pydantic=={version('pydantic')}\n", encoding="utf-8")


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
    env = {**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"}
    process = subprocess.Popen(
        [sys.executable, "-m", "executor.autonomy.cli", "--runtime", str(runtime),
         "--port", str(service_port), "bootstrap-serve", "--reason", "service_start_failed"],
        env=env, cwd=os.getcwd(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
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
        opened = []
        monkeypatch.setattr(bootstrap.webbrowser, "open", lambda value, **kwargs: opened.append(value) or True)
        assert bootstrap.open_bootstrap(runtime, service_port)["opened"] is True
        assert opened == [url]
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
    finally:
        cli.lifecycle("stop", runtime, service_port)
        process.terminate()
        process.wait(timeout=5)


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
    assert str(repo.resolve()) in launcher
    assert 'cd "$RELEASE_ROOT"' in launcher
    assert 'export PYTHONPATH="$RELEASE_ROOT"' in launcher
    assert "export PYTHONDONTWRITEBYTECODE=1" in launcher
    assert '"$PYTHON" -B -m executor.autonomy.cli launch' in launcher
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




def test_macos_consumer_app_upgrades_and_rolls_back_exact_legacy_bundle(tmp_path):
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

    restored = rollback_macos_app(apps)
    assert restored["ok"] is True
    assert executable.read_text() == old_launcher
    assert (apps / ".AI 投递经理.app.failed").is_dir()


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
    current_cli.write_text("VERSION = 'fixture'\n", encoding="utf-8")
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
    assert previous_version.read_text() == "VERSION = 'fixture'\n"

    (repo / "executor" / "autonomy" / "cli.py").write_text(
        "import deliberately_missing_release_dependency\n", encoding="utf-8"
    )
    result = install_macos_app(repo, destination=apps, platform="darwin")
    assert result["ok"] is False
    assert result["reason"] == "candidate_start_failed"
    assert previous_version.read_text() == "VERSION = 'fixture'\n"
    assert not (apps / ".AI 投递经理.app.previous").exists()
    assert not (apps / ".AI 投递经理.app.installing").exists()


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
