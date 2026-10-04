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
    assert "timeout-minutes: 30" in foundation and "timeout-minutes: 25" in mac


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


# Read-only interruption observability belongs to the same complete owning file
# already selected by the existing Linux and hosted Mac jobs.
def _inspector():
    import importlib.util
    path = SCRIPT.with_name("inspect_lifecycle_soak.py")
    spec = importlib.util.spec_from_file_location("soak_inspector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkpoint_chain(tmp_path, *, terminal="COMPLETED_PARTIAL_WINDOW", mutate=None):
    root = tmp_path / "checkpoints"
    root.mkdir(mode=0o700)
    records = []
    for index in range(3):
        records.append({
            "format": "jae-lifecycle-soak-v1", "checkpoint": index,
            "status": terminal if index == 2 else "RUNNING",
            "certification": "NOT_CERTIFIED", "full_z04_pass": False,
            "continuous_24h_observed": False,
            "observed_seconds": float(index), "requested_seconds": 2.0,
            "completed_cycles": index, "health_observations": index,
            "tasks": 3, "source_sha256": "a" * 64, "harness_sha256": "b" * 64,
            "requirements_sha256": "c" * 64, "source_git_head": "d" * 40,
            "checkout_dirty": False, "started_at": "2026-10-03T00:00:00+00:00",
            "cleanup": "child_exited_and_lock_released" if index == 2 else "owned_child_running",
        })
    if mutate:
        mutate(records)
    previous_bytes = 0
    for index, record in enumerate(records):
        total = previous_bytes
        while True:
            record["evidence_bytes"] = total
            raw = (json.dumps(record, sort_keys=True) + "\n").encode()
            next_total = previous_bytes + len(raw)
            if next_total == total:
                break
            total = next_total
        path = root / f"checkpoint-{index:06d}.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        previous_bytes = total
    return root


def test_inspector_never_converts_missing_terminal_or_elapsed_gap_to_completion(tmp_path):
    root = _checkpoint_chain(tmp_path, terminal="RUNNING")
    last = root / "checkpoint-000002.json"
    report = _inspector().inspect(root, now=last.stat().st_mtime + 86400)
    assert report["classification"] == "INCOMPLETE_NO_TERMINAL"
    assert report["freshness"] == "STALE"
    assert report["actual_observation_seconds_reported"] == 2
    assert report["terminal_report_present"] is False
    assert report["execution_independently_verified"] is False
    assert report["full_z04_pass"] is False


def test_inspector_complete_report_remains_self_reported_and_ignores_private_runtime(tmp_path, monkeypatch):
    root = _checkpoint_chain(tmp_path)
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    for name in ("auth.token", "task-answers.key", "service.json", "tasks.sqlite3", "service.log"):
        (runtime / name).write_text("PRIVATE_NEVER_READ")
    module = _inspector()
    original_open = os.open
    opened = []
    def audited_open(path, *args, **kwargs):
        opened.append(str(path))
        assert "runtime" not in str(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", audited_open)
    report = module.inspect(root)
    assert report["classification"] == "COMPLETED_PARTIAL_REPORTED"
    assert report["execution_independently_verified"] is False
    assert report["runtime_files_read"] is report["runtime_actions_performed"] is False
    assert report["evidence_bytes"] == sum(p.stat().st_size for p in root.glob("checkpoint-*.json"))
    assert report["full_z04_pass"] is False
    assert "PRIVATE_NEVER_READ" not in json.dumps(report)
    assert len(opened) == 4


@pytest.mark.parametrize("status", ["FAILED", "INTERRUPTED"])
def test_inspector_retains_failure_and_interruption(tmp_path, status):
    report = _inspector().inspect(_checkpoint_chain(tmp_path, terminal=status))
    assert report["classification"] == status + "_REPORTED"
    assert report["full_z04_pass"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("checkpoint", 99, "checkpoint_index"),
    ("source_sha256", "e" * 64, "identity_changed"),
    ("requested_seconds", 5.0, "identity_changed"),
    ("observed_seconds", .5, "progress_regressed"),
    ("completed_cycles", 0, "progress_regressed"),
    ("health_observations", False, "invalid_progress"),
    ("observed_seconds", float("nan"), "invalid_duration"),
    ("status", "PRIVATE_SECRET_MARKER", "unknown_status"),
    ("cleanup", "forced_owned_child_cleanup", "inconsistent_completion"),
    ("full_z04_pass", True, "certification_claim"),
    ("continuous_24h_observed", True, "inconsistent_24h_claim"),
])
def test_inspector_rejects_inconsistent_chain_without_echoing_private_values(tmp_path, field, value, reason):
    root = _checkpoint_chain(tmp_path, mutate=lambda rows: rows[2].update({field: value}))
    report = _inspector().inspect(root)
    assert report["classification"] == "INVALID_EVIDENCE"
    assert report["reason"] == reason
    assert "PRIVATE_SECRET_MARKER" not in json.dumps(report)
    assert report["full_z04_pass"] is False


def test_inspector_rejects_terminal_chain_with_observation_gap(tmp_path):
    def change(rows):
        for row in rows:
            row["requested_seconds"] = 600
        rows[2]["observed_seconds"] = 600
    report = _inspector().inspect(_checkpoint_chain(tmp_path, mutate=change))
    assert report["classification"] == "INCOMPLETE_OBSERVATION_GAP"
    assert report["maximum_reported_observation_gap_seconds"] == 599
    assert report["full_z04_pass"] is False


@pytest.mark.parametrize("fault", ["missing_middle", "truncated", "bytes", "symlink", "hardlink", "public_mode", "fifo"])
def test_inspector_refuses_damaged_or_unowned_checkpoint_without_modification(tmp_path, fault):
    root = _checkpoint_chain(tmp_path)
    path = root / "checkpoint-000001.json"
    if fault == "missing_middle":
        path.unlink()
    elif fault == "truncated":
        path.write_text('{"PRIVATE_SECRET_MARKER":')
    elif fault == "bytes":
        path.write_text(path.read_text().replace('"evidence_bytes":', '"evidence_bytes":0,"old_bytes":', 1))
    elif fault == "symlink":
        outside = tmp_path / "private-token"
        outside.write_text("PRIVATE_SECRET_MARKER")
        path.unlink()
        path.symlink_to(outside)
    elif fault == "hardlink":
        os.link(path, root / "second-link")
    elif fault == "public_mode":
        path.chmod(0o644)
    else:
        path.unlink()
        os.mkfifo(path, 0o600)
    before = [(p.name, p.lstat().st_mode, p.lstat().st_size, p.lstat().st_ino) for p in root.iterdir()]
    report = _inspector().inspect(root)
    after = [(p.name, p.lstat().st_mode, p.lstat().st_size, p.lstat().st_ino) for p in root.iterdir()]
    assert report["classification"] == "INVALID_EVIDENCE"
    assert before == after
    assert "PRIVATE_SECRET_MARKER" not in json.dumps(report)


def test_inspector_refuses_data_after_terminal_and_aliased_root(tmp_path):
    root = _checkpoint_chain(tmp_path, mutate=lambda rows: rows[1].update(status="INTERRUPTED"))
    assert _inspector().inspect(root)["reason"] == "data_after_terminal"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert _inspector().inspect(alias)["reason"] == "workspace_alias"
