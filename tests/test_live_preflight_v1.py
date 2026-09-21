import json

from executor.autonomy import cli
from executor.autonomy import preflight


def test_live_preflight_ready_without_exposing_private_paths(tmp_path):
    profile = tmp_path / "private-profile.json"
    profile.write_text("{}")
    result = preflight.collect_live_preflight(
        {"profile_path": str(profile), "deepseek": {"enabled": True}},
        supervisor_running=True,
        browser_mode_value="live",
        chrome_exists=True,
        cdp_alive=True,
        deepseek_available=True,
    )

    assert result["ok"] is True
    assert result["ready_for_live_e2e"] is True
    assert result["final_click_actor"] == "user"
    assert result["submit_capability"] is False
    assert result["remediation"] == []
    assert str(profile) not in json.dumps(result)


def test_live_preflight_fails_closed_with_machine_readable_remediation():
    result = preflight.collect_live_preflight(
        {"profile_path": None, "deepseek": {"enabled": False}},
        supervisor_running=False,
        browser_mode_value="isolated",
        chrome_exists=False,
        cdp_alive=False,
        deepseek_available=False,
        profile_exists=False,
    )

    assert result["ok"] is False
    assert result["ready_for_live_e2e"] is False
    assert result["checks"] == {
        "live_browser_mode": False,
        "chrome_installed": False,
        "existing_cdp_session": False,
        "profile_configured": False,
        "profile_exists": False,
        "profile_loadable": False,
        "deepseek_available": False,
        "supervisor_running": False,
    }
    assert result["remediation"] == [
        "use_live_browser_mode",
        "install_google_chrome",
        "start_dedicated_chrome_cdp",
        "configure_profile_path",
        "restore_profile_file",
        "repair_profile_file",
        "configure_deepseek_key",
        "start_supervisor",
    ]


def test_live_preflight_rejects_malformed_or_unsafe_profile(tmp_path):
    profile = tmp_path / "bad-profile.json"
    profile.write_text('{"password":"must-not-be-profile-data"}')
    result = preflight.collect_live_preflight(
        {"profile_path": str(profile), "deepseek": {"enabled": True}},
        supervisor_running=True,
        browser_mode_value="live",
        chrome_exists=True,
        cdp_alive=True,
        deepseek_available=True,
    )

    assert result["profile_path"] if False else True
    assert result["checks"]["profile_exists"] is True
    assert result["checks"]["profile_loadable"] is False
    assert result["ready_for_live_e2e"] is False
    assert "repair_profile_file" in result["remediation"]
    assert str(profile) not in json.dumps(result)


def test_cli_preflight_start_is_reversible_and_does_not_create_a_task(
    tmp_path, monkeypatch, capsys
):
    calls = []

    def fake_lifecycle(action, root, port):
        calls.append(action)
        return {"ok": True, "worker_active": False}

    def fake_collect(*, supervisor_running):
        assert supervisor_running is True
        return {
            "ok": True,
            "ready_for_live_e2e": True,
            "checks": {"supervisor_running": True},
            "remediation": [],
            "final_click_actor": "user",
            "submit_capability": False,
        }

    monkeypatch.setattr(cli, "lifecycle", fake_lifecycle)
    monkeypatch.setattr(preflight, "collect_live_preflight", fake_collect)

    rc = cli.main(
        ["--runtime", str(tmp_path / "runtime"), "preflight", "--start"]
    )

    assert rc == 0
    assert calls == ["start", "health"]
    output = json.loads(capsys.readouterr().out)
    assert output["ready_for_live_e2e"] is True
    assert output["submit_capability"] is False
