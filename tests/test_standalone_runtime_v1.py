from __future__ import annotations

import os
import shutil
import sys
import venv
from pathlib import Path

import pytest

from executor.autonomy.consumer import (APP_NAME, _candidate_starts, install_macos_app,
                                        rollback_macos_app)
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.release import copy_source_candidate, source_manifest, verify_runtime_candidate
from executor.autonomy.standalone_runtime import (
    copy_standalone_runtime_candidate, verify_standalone_runtime,
)


def release(tmp_path):
    source = Path(__file__).resolve().parents[1]
    repo = tmp_path / "checkout"
    copy_source_candidate(source, repo)
    return repo


def test_standalone_snapshot_refuses_host_virtualenv_before_copy(tmp_path):
    repo = release(tmp_path)
    source = tmp_path / "host-venv"
    venv.EnvBuilder(with_pip=False).create(source)
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="standalone_runtime_source_invalid"):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()
    assert (source / "pyvenv.cfg").is_file()


@pytest.mark.parametrize("defect", ["private-file", "directory", "fifo", "loop"])
def test_standalone_snapshot_refuses_aliases_and_nonregular_payload(
    tmp_path, defect
):
    repo = release(tmp_path)
    source = tmp_path / "runtime-source"
    (source / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, source / "bin" / "python")
    private = tmp_path / "private"
    private.mkdir()
    canary = private / "key"
    canary.write_text("CANARY_PRIVATE_KEY", encoding="utf-8")
    if defect == "private-file":
        (source / "alias").symlink_to(canary)
    elif defect == "directory":
        (source / "alias").symlink_to(private, target_is_directory=True)
    elif defect == "loop":
        (source / "alias").symlink_to(source / "alias")
    else:
        os.mkfifo(source / "pipe")
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="standalone_runtime_"):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()
    assert canary.read_text() == "CANARY_PRIVATE_KEY"


def test_copied_host_interpreter_is_not_standalone_provenance(tmp_path):
    repo = release(tmp_path)
    source = tmp_path / "runtime-source"
    (source / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, source / "bin" / "python")
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="standalone_runtime_provenance_failed"):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()


def test_real_standalone_app_preserves_journal_and_runs_after_build_sources_removed(
    tmp_path, monkeypatch
):
    # Required cloud input, prepared once with a fixed archive digest. Missing
    # preparation is a failed gate, never a skipped or synthetic positive test.
    source = Path(os.environ["JAE_STANDALONE_RUNTIME"])
    assert source.is_dir()
    repo = release(tmp_path)
    prepared = tmp_path / "prepared-runtime"
    copy_standalone_runtime_candidate(source, prepared, repo)
    state = tmp_path / "private-state"
    queue = TaskQueue(state)
    task = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
        target_url="https://example.invalid/jobs/standalone-fixture",
        profile_ref="synthetic-profile.json"))
    before = queue.get(task["task_id"])
    apps = tmp_path / "Applications"
    result = install_macos_app(repo, destination=apps, platform="darwin",
        task_state_root=state, standalone_runtime=prepared)
    assert result["ok"] is True, result
    app = apps / (APP_NAME + ".app")
    owned = app / "Contents" / "Resources" / "runtime"
    app_source = app / "Contents" / "Resources" / "release"
    assert not (owned / "pyvenv.cfg").exists()
    assert verify_standalone_runtime(owned, app_source)
    assert queue.get(task["task_id"]) == before
    repo.rename(tmp_path / "checkout-removed")
    prepared.rename(tmp_path / "prepared-runtime-removed")
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('unowned startup')")
    monkeypatch.setenv("PYTHONHOME", str(poison))
    monkeypatch.setenv("PYTHONPATH", str(poison))
    assert verify_standalone_runtime(owned, app_source)
    assert _candidate_starts(owned / "bin" / "python", app_source)
    assert verify_runtime_candidate(owned, app_source)
    assert queue.get(task["task_id"]) == before
    first_identity = source_manifest(app_source)
    # Exercise the same complete runtime in a real second activation and the
    # production rollback transaction, rather than a mocked health positive.
    replacement = tmp_path / "replacement-checkout"
    copy_source_candidate(Path(__file__).resolve().parents[1], replacement)
    initializer = replacement / "executor" / "__init__.py"
    initializer.write_text(initializer.read_text() + "\n# replacement fixture\n")
    updated = install_macos_app(replacement, destination=apps, platform="darwin",
        task_state_root=state, standalone_runtime=source)
    assert updated["ok"] is True and updated["replaced"] is True, updated
    assert source_manifest(app_source) != first_identity
    assert verify_standalone_runtime(owned, app_source)
    assert queue.get(task["task_id"]) == before
    restored = rollback_macos_app(apps, task_state_root=state)
    assert restored["ok"] is True and restored["restored"] is True, restored
    assert source_manifest(app_source) == first_identity
    assert verify_standalone_runtime(owned, app_source)
    assert _candidate_starts(owned / "bin" / "python", app_source)
    failed = apps / ("." + APP_NAME + ".app.failed")
    assert failed.is_dir()
    assert verify_runtime_candidate(failed / "Contents" / "Resources" / "runtime",
        failed / "Contents" / "Resources" / "release")
    assert queue.get(task["task_id"]) == before
    # A complete runtime checksum alone is not base/stdlib independence.
    marker = owned / "release-standalone-runtime.json"
    marker.write_text('{"format":"tampered"}')
    assert not verify_standalone_runtime(owned, app_source)
    assert not _candidate_starts(owned / "bin" / "python", app_source)
    assert queue.get(task["task_id"]) == before


def test_installer_cli_routes_explicit_standalone_runtime_without_other_effects(
    tmp_path, monkeypatch, capsys
):
    from executor.autonomy import cli, consumer

    calls = []
    runtime = tmp_path / "prepared-runtime"
    monkeypatch.setattr(consumer, "install_macos_app",
        lambda repo, **kwargs: calls.append((repo, kwargs)) or {"ok": True})
    monkeypatch.setattr(sys, "argv", ["executor", "install-app",
        "--standalone-runtime", str(runtime)])
    cli.main()
    assert len(calls) == 1
    assert calls[0][1] == {"standalone_runtime": runtime}
    assert '"ok": true' in capsys.readouterr().out.lower()
