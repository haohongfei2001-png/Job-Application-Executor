from __future__ import annotations

import json
import plistlib
import stat

from executor.autonomy import cli, preflight
from executor.autonomy.consumer import install_macos_app
from executor.autonomy.dashboard import DASHBOARD_HTML


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


def test_consumer_launch_fails_closed_before_ui_when_preflight_is_not_ready(
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

    def forbidden_open(*_):
        raise AssertionError("UI must not open before consumer readiness passes")

    monkeypatch.setattr(cli, "open_ui", forbidden_open)
    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert result["ok"] is False
    assert result["opened"] is False
    assert result["submit_capability"] is False
    assert "DeepSeek" in result["message"]


def test_consumer_launch_does_not_start_live_browser_in_isolated_mode(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "browser_mode", lambda: "isolated")

    def forbidden():
        raise AssertionError("isolated/test launch must not start live Chrome")

    monkeypatch.setattr(cli, "ensure_chrome", forbidden)
    result = cli.launch_consumer(tmp_path / "runtime", 9344)

    assert result["ok"] is False
    assert result["opened"] is False
    assert result["submit_capability"] is False
    assert "use_live_browser_mode" in result["remediation"]


def test_macos_consumer_app_installs_idempotently(tmp_path):
    repo = tmp_path / "Job-Application-Executor"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    apps = tmp_path / "Applications"

    first = install_macos_app(repo, destination=apps, platform="darwin")
    app = apps / "AI 投递经理.app"
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    info_path = app / "Contents" / "Info.plist"

    assert first["ok"] is True
    assert first["replaced"] is False
    assert app.is_dir()
    assert executable.stat().st_mode & stat.S_IXUSR
    launcher = executable.read_text(encoding="utf-8")
    assert str(repo.resolve()) in launcher
    assert "executor.autonomy.cli launch" in launcher
    assert "submit" not in launcher.casefold()

    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleDisplayName"] == "AI 投递经理"
    assert info["CFBundlePackageType"] == "APPL"

    rogue = app / "Contents" / "old-file.txt"
    rogue.write_text("old")
    second = install_macos_app(repo, destination=apps, platform="darwin")
    assert second["ok"] is True
    assert second["replaced"] is True
    assert not rogue.exists()


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
    assert "placeholder=\"告诉我你想投哪个岗位" in DASHBOARD_HTML
    assert "复制诊断" in DASHBOARD_HTML
    assert "检查并更新" in DASHBOARD_HTML
    assert "/ui/api/diagnostics" in DASHBOARD_HTML
    assert "/ui/api/update" in DASHBOARD_HTML
    assert "otp_in_flight" in DASHBOARD_HTML
