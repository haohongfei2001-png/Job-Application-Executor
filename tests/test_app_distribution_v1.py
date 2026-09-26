from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from executor.autonomy.app_distribution import (
    ARCHIVE_NAME, RECEIPT_NAME, _archive_app, build_macos_distribution,
)
from executor.autonomy.consumer import APP_NAME, _candidate_starts
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.release import (
    copy_source_candidate, source_manifest, verify_runtime_candidate,
    verify_source_candidate,
)
from executor.autonomy.standalone_runtime import (
    copy_standalone_runtime_candidate, verify_standalone_runtime,
)


@pytest.mark.parametrize("name", [
    "Contents/Resources/runtime/task-answers.key",
    "Contents/Resources/runtime/tasks.sqlite3-wal",
    "Contents/Resources/release/.env",
    "Contents/Resources/applicant-note.txt",
])
def test_archive_refuses_private_or_undeclared_payload_before_publication(tmp_path, name):
    app = tmp_path / (APP_NAME + ".app")
    payload = app / name
    payload.parent.mkdir(parents=True)
    payload.write_text("CANARY_PRIVATE_DISTRIBUTION", encoding="utf-8")
    archive = tmp_path / ARCHIVE_NAME
    with pytest.raises(ValueError, match="distribution_private_payload"):
        _archive_app(app, archive)
    assert not archive.exists()
    assert payload.read_text() == "CANARY_PRIVATE_DISTRIBUTION"


def test_build_refuses_existing_output_and_alias_without_overwriting(tmp_path):
    source = Path(__file__).resolve().parents[1]
    output = tmp_path / "existing"
    output.mkdir()
    canary = output / RECEIPT_NAME
    canary.write_text("existing artifact")
    with pytest.raises(ValueError, match="distribution_output_unavailable"):
        build_macos_distribution(source, standalone_runtime=tmp_path / "runtime",
                                 output_dir=output, platform="darwin")
    assert canary.read_text() == "existing artifact"
    alias = tmp_path / "alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="distribution_path_alias"):
        build_macos_distribution(source, standalone_runtime=tmp_path / "runtime",
                                 output_dir=alias / "new", platform="darwin")
    assert sorted(path.name for path in output.iterdir()) == [RECEIPT_NAME]



def test_candidate_failure_leaves_no_ready_artifact_or_source_state_change(tmp_path, monkeypatch):
    from executor.autonomy import app_distribution as distribution

    repo = tmp_path / "checkout"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    state = repo / "runtime" / "autonomy"
    state.mkdir(parents=True)
    canary = state / "auth.token"
    canary.write_text("CANARY_UNCHANGED_SOURCE_AUTH")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    output = tmp_path / "failed-output"
    attempts = []
    def reject(source, **kwargs):
        attempts.append(source)
        assert source != repo
        assert verify_source_candidate(source)
        assert not (source / "runtime").exists()
        assert kwargs["standalone_runtime"] == runtime
        assert kwargs["task_state_root"] != state
        return {"ok": False, "reason": "candidate_start_failed"}
    monkeypatch.setattr(distribution, "install_macos_app", reject)
    with pytest.raises(ValueError, match="distribution_candidate_failed"):
        build_macos_distribution(repo, standalone_runtime=runtime,
                                 output_dir=output, platform="darwin")
    assert len(attempts) == 1
    assert not output.exists()
    assert canary.read_text() == "CANARY_UNCHANGED_SOURCE_AUTH"
    assert verify_source_candidate(repo) is False  # Repo-local state is excluded, not erased.


def test_archive_refuses_member_alias_without_reading_private_target(tmp_path):
    app = tmp_path / (APP_NAME + ".app")
    runtime = app / "Contents" / "Resources" / "runtime"
    runtime.mkdir(parents=True)
    private = tmp_path / "private-key"
    private.write_text("CANARY_UNREAD_ALIAS")
    (runtime / "module.py").symlink_to(private)
    archive = tmp_path / ARCHIVE_NAME
    with pytest.raises(ValueError, match="distribution_member_invalid"):
        _archive_app(app, archive)
    assert not archive.exists()
    assert private.read_text() == "CANARY_UNREAD_ALIAS"


def test_real_archive_relocates_without_checkout_runtime_or_private_build_state(
    tmp_path, monkeypatch
):
    source = Path(os.environ["JAE_STANDALONE_RUNTIME"])
    repo = tmp_path / "checkout"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    prepared = tmp_path / "prepared-runtime"
    copy_standalone_runtime_candidate(source, prepared, repo)
    # Real legacy task authority and credentials in the source checkout must
    # neither block a pure build nor enter the distributed app.
    private = repo / "runtime" / "autonomy"
    queue = TaskQueue(private)
    task = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
        target_url="https://example.invalid/jobs/build-fixture",
        profile_ref="synthetic-profile.json"))
    before = queue.get(task["task_id"])
    (repo / ".env").write_text("CANARY_BUILD_CREDENTIAL")
    (private / "task-answers.key").write_text("CANARY_BUILD_ANSWER_KEY")
    (repo / "profile.json").write_text('{"name":"CANARY_BUILD_PROFILE"}')
    output = tmp_path / "unsigned-build"
    receipt = build_macos_distribution(
        repo, standalone_runtime=prepared, output_dir=output, platform="darwin")
    assert queue.get(task["task_id"]) == before
    assert (private / "task-answers.key").read_text() == "CANARY_BUILD_ANSWER_KEY"
    assert sorted(path.name for path in output.iterdir()) == [ARCHIVE_NAME, RECEIPT_NAME]
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
    assert receipt["signing"] == "unsigned"
    assert receipt["certification"] == "NOT_CERTIFIED"
    assert receipt["task_state"] == "excluded"
    assert receipt["final_click_actor"] == "user"
    digest = hashlib.sha256()
    with (output / ARCHIVE_NAME).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    assert receipt["archive_sha256"] == digest.hexdigest()
    extracted = tmp_path / "Relocated Applications"
    extracted.mkdir()
    with tarfile.open(output / ARCHIVE_NAME, "r:gz") as archive:
        members = archive.getmembers()
        assert members
        for member in members:
            assert member.name == APP_NAME + ".app" or member.name.startswith(APP_NAME + ".app/")
            assert member.isfile() or member.isdir()
            assert member.uid == member.gid == member.mtime == 0
            assert member.uname == member.gname == ""
            assert not any(part in {".env", "profile.json", "tasks.sqlite3",
                "tasks.sqlite3-wal", "tasks.sqlite3-shm", "task-answers.key",
                "auth.token", "service.json"} for part in Path(member.name).parts)
        archive.extractall(extracted, filter="data")
    shutil.rmtree(repo)
    shutil.rmtree(prepared)
    app = extracted / (APP_NAME + ".app")
    release = app / "Contents" / "Resources" / "release"
    runtime = app / "Contents" / "Resources" / "runtime"
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('unowned startup')")
    monkeypatch.setenv("PYTHONHOME", str(poison))
    monkeypatch.setenv("PYTHONPATH", str(poison))
    assert verify_source_candidate(release)
    assert source_manifest(release)["source_sha256"] == receipt["source_sha256"]
    assert verify_runtime_candidate(runtime, release)
    identity = json.loads((runtime / "release-runtime-manifest.json").read_text())
    assert identity["runtime_sha256"] == receipt["runtime_sha256"]
    assert identity["requirements_sha256"] == receipt["requirements_sha256"]
    assert verify_standalone_runtime(runtime, release)
    assert _candidate_starts(runtime / "bin" / "python", release)
    # Installer-health journals and tokens are temporary build resources.
    assert not (release / "runtime").exists()
    assert not (runtime / "auth.token").exists()
    assert json.loads((output / RECEIPT_NAME).read_text()) == receipt
