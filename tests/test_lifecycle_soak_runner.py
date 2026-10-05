"""Black-box partial endurance-runner admission, lifecycle and failure canaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_lifecycle_soak.py"


def command(workspace, *extra):
    return [sys.executable, "-I", "-B", str(SCRIPT), "--workspace", str(workspace),
            "--duration-seconds", "4", "--hold-seconds", ".5", "--tasks", "3", *extra]


def result(process):
    return json.loads(process.stdout.strip().splitlines()[-1])


def await_checkpoint(workspace, child):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        assert child.poll() is None, "runner stopped before canary"
        files = sorted(workspace.glob("checkpoint-*.json"))
        for path in reversed(files):
            try:
                report = json.loads(path.read_text())
                if report.get("health_observations", 0):
                    return report
            except (OSError, ValueError):
                pass
        time.sleep(.05)
    pytest.fail("runner did not reach actual health observation")


def test_actual_two_cycle_fresh_home_soak_is_partial_and_private(tmp_path):
    ambient = tmp_path / "ambient"
    config = ambient / "Job-Application-Executor/config"
    config.mkdir(parents=True)
    poison = "PRIVATE_DO_NOT_READ"
    for name in ["protected-targets.json", "settings.json"]:
        (config / name).write_text(poison)
    workspace = tmp_path / "run"
    env = {**os.environ, "HOME": str(ambient), "APPLICATION_EXECUTOR_LOCAL_TOKEN": poison,
           "DEEPSEEK_API_KEY": poison, "HTTPS_PROXY": "http://127.0.0.1:1"}
    run = subprocess.run(command(workspace, "--duration-seconds", "10"), env=env, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
    report = result(run)
    assert report["status"] == "COMPLETED_PARTIAL_WINDOW"
    assert report["completed_cycles"] >= 2
    assert report["health_observations"] >= 2
    assert report["observed_seconds"] >= 10
    assert report["preparation_seconds"] > 0
    assert report["preparation_started_at"] <= report["started_at"]
    assert report["continuous_24h_observed"] is False
    assert report["full_z04_pass"] is False
    assert report["certification"] == "NOT_CERTIFIED"
    assert report["cleanup"] == "child_exited_and_lock_released"
    assert len(report["source_sha256"]) == len(report["harness_sha256"]) == 64
    assert not (workspace / "runtime/service.json").exists()
    assert workspace.stat().st_mode & 0o077 == 0
    checkpoints = sorted(workspace.glob("checkpoint-*.json"))
    assert [json.loads(p.read_text())["checkpoint"] for p in checkpoints] == list(range(len(checkpoints)))
    assert all(p.stat().st_mode & 0o077 == 0 for p in checkpoints)
    total = 0
    for path in checkpoints:
        total += path.stat().st_size
        assert json.loads(path.read_text())["evidence_bytes"] == total
    assert report["evidence_bytes"] == total
    assert report["interpreter"]["runner_isolated"] is True
    assert report["interpreter"]["version"]
    assert len(report["interpreter"]["binary_sha256"]) == 64
    assert report["host"]["os"] == sys.platform
    token = (workspace / "runtime/auth.token").read_text().strip()
    for text in [run.stdout, run.stderr, *(p.read_text() for p in checkpoints)]:
        assert poison not in text and token not in text
    assert all((config / name).read_text() == poison for name in ["protected-targets.json", "settings.json"])


@pytest.mark.parametrize("path_kind", ["existing", "symlink", "ancestor_symlink"])
def test_soak_refuses_existing_or_aliased_workspace_without_touching_sentinel(tmp_path, path_kind):
    owned = tmp_path / "existing"
    owned.mkdir(mode=0o750)
    sentinel = owned / "sentinel"
    sentinel.write_bytes(b"UNCHANGED")
    before = owned.stat().st_mode
    candidate = owned
    if path_kind != "existing":
        alias = tmp_path / "alias"
        alias.symlink_to(owned, target_is_directory=True)
        candidate = alias if path_kind == "symlink" else alias / "new"
    run = subprocess.run(command(candidate), capture_output=True, text=True, timeout=5)
    assert run.returncode != 0 and result(run)["status"] == "REFUSED"
    assert sentinel.read_bytes() == b"UNCHANGED"
    assert owned.stat().st_mode == before
    assert sorted(p.name for p in owned.iterdir()) == ["sentinel"]


@pytest.mark.parametrize("option,value", [
    ("--duration-seconds", "nan"), ("--duration-seconds", "inf"),
    ("--duration-seconds", "0"), ("--hold-seconds", "0"),
    ("--tasks", "1"), ("--tasks", "101"),
])
def test_soak_refuses_invalid_bounds_before_workspace_creation(tmp_path, option, value):
    workspace = tmp_path / "never-created"
    run = subprocess.run(command(workspace, option, value), capture_output=True, text=True, timeout=5)
    assert run.returncode != 0
    assert not workspace.exists()


@pytest.mark.parametrize("fault", ["interrupt", "receipt_corruption", "source_corruption", "wrong_registry"])
def test_running_soak_records_interruptions_and_real_canary_failures(tmp_path, fault):
    workspace = tmp_path / "run"
    child = subprocess.Popen(command(workspace, "--duration-seconds", "30", "--hold-seconds", "1"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        await_checkpoint(workspace, child)
        if fault == "interrupt":
            child.send_signal(signal.SIGTERM)
        elif fault == "receipt_corruption":
            with sqlite3.connect(workspace / "runtime/tasks.sqlite3") as db:
                db.execute("UPDATE commands SET receipt='{}'")
        elif fault == "source_corruption":
            source = workspace / "source/executor/__init__.py"
            source.write_text(source.read_text() + "\n# synthetic integrity canary\n")
        else:
            path = workspace / "runtime/service.json"
            record = json.loads(path.read_text())
            record["pid"] += 1
            path.write_text(json.dumps(record))
        out, err = child.communicate(timeout=20)
    finally:
        if child.poll() is None:
            child.terminate()
            child.communicate(timeout=15)
    report = json.loads(out.strip().splitlines()[-1])
    assert child.returncode != 0, out + err
    assert report["status"] == ("INTERRUPTED" if fault == "interrupt" else "FAILED")
    assert report["full_z04_pass"] is False and report["continuous_24h_observed"] is False
    assert report["cleanup"] != "UNCONFIRMED_OWNED_CHILD"
    if fault != "interrupt":
        assert report["failure_reason"] in {"journal_changed_while_running", "journal_changed_after_restart", "receipt_changed", "source_changed", "authenticated_stop_unconfirmed"}


@pytest.mark.parametrize("metric", ["rss_bytes", "open_fds"])
def test_soak_resource_threshold_canary_cannot_report_success(tmp_path, metric):
    workspace = tmp_path / "run"
    # The canary changes only the observation in a separate test process. It
    # never allocates excessive memory/descriptors or changes production limits.
    script = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('soak',{str(SCRIPT)!r});"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "m.resource_sample=lambda child: {'rss_bytes':None,'open_fds':None,"
        f"'measurement':'synthetic_threshold_canary',{metric!r}:10**12}};"
        f"sys.argv={command(workspace)[3:]!r};"
        "raise SystemExit(m.main())"
    )
    run = subprocess.run([sys.executable, "-I", "-B", "-c", script],
                         capture_output=True, text=True, timeout=20)
    assert run.returncode != 0, run.stdout + run.stderr
    report = result(run)
    assert report["status"] == "FAILED"
    assert report["failure_reason"] == ("rss_limit_exceeded" if metric == "rss_bytes" else "fd_limit_exceeded")
    assert report["completed_cycles"] == 0
    assert report["cleanup"] == "child_exited_after_failure"
    assert report["full_z04_pass"] is False


def test_expired_window_without_complete_health_cycle_does_not_pass(tmp_path):
    run = subprocess.run(command(tmp_path / "run", "--duration-seconds", ".00001"),
                         capture_output=True, text=True, timeout=10)
    assert run.returncode != 0
    report = result(run)
    assert report["status"] == "FAILED"
    assert report["failure_reason"] == "no_complete_observed_cycle"
    assert report["health_observations"] == report["completed_cycles"] == 0
    assert report["continuous_24h_observed"] is False


def test_existing_hosted_jobs_keep_complete_runner_coverage_without_new_allocation():
    workflow = (SCRIPT.parents[1] / ".github/workflows/application-executor-ci.yml").read_text()
    foundation = workflow.split("  foundation:\n", 1)[1].split("  packaged_candidate:\n", 1)[0]
    mac = workflow.split("  macos_consumer_release:\n", 1)[1].split("  test:\n", 1)[0]
    assert foundation.count("tests/test_lifecycle_soak_runner.py") == 1
    assert mac.count("tests/test_lifecycle_soak_runner.py") == 1
    step = mac.split("      - name: Complete partial lifecycle-soak runner contract on hosted Mac\n", 1)[1].split("      - name:", 1)[0]
    assert "        if: matrix.suite == 'runtime_distribution'\n" in step
    assert "        run: python -m pytest -q tests/test_lifecycle_soak_runner.py\n" in step
    assert "timeout-minutes: 30" in foundation
    assert "timeout-minutes: ${{ matrix.suite == 'native_integration' && 30 || 25 }}" in mac


def test_real_preparation_delay_is_not_counted_as_observation(tmp_path):
    workspace = tmp_path / "delayed-run"
    script = "\n".join([
        "import importlib.util,sys,time",
        f"spec=importlib.util.spec_from_file_location('soak',{str(SCRIPT)!r})",
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)",
        "original=m.subprocess.run",
        "def delayed(*args,**kwargs):",
        "    if args[0][:2]==['git','rev-parse']: time.sleep(5)",
        "    return original(*args,**kwargs)",
        "m.subprocess.run=delayed",
        f"sys.argv={command(workspace, '--duration-seconds', '3')[3:]!r}",
        "raise SystemExit(m.main())",
    ])
    begun = time.monotonic()
    run = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True, timeout=20)
    total = time.monotonic() - begun
    assert run.returncode == 0, run.stdout + run.stderr
    report = result(run)
    assert report["preparation_seconds"] >= 5
    assert report["observed_seconds"] >= 3
    assert report["observed_seconds"] < total - 4
    assert report["health_observations"] >= 1 and report["completed_cycles"] >= 1
    assert report["full_z04_pass"] is False and report["continuous_24h_observed"] is False
