from __future__ import annotations

import json
import os
from importlib.metadata import version
import socket
import subprocess
import sys
import time
import venv
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from executor.autonomy.bootstrap import _version
from executor.autonomy.queue import default_runtime

from executor.autonomy.release import (
    MANIFEST_NAME,
    copy_source_candidate,
    copy_runtime_candidate,
    installed_dependencies_match,
    read_release_identity,
    source_manifest,
    verify_runtime_candidate,
    verify_source_candidate,
)


def test_packaged_runtime_default_stays_outside_app_with_missing_manifest(tmp_path):
    source = tmp_path / "AI 投递经理.app" / "Contents" / "Resources" / "release"
    source.mkdir(parents=True)
    home = tmp_path / "consumer-home"
    assert not (source / MANIFEST_NAME).exists()
    assert default_runtime(source, home=home) == (
        home / "Library" / "Application Support" / "AI投递经理" / "autonomy"
    )
    checkout = tmp_path / "development-checkout"
    assert default_runtime(checkout, home=home) == checkout / "runtime" / "autonomy"


def _source(tmp_path):
    repo = tmp_path / "source"
    package = repo / "executor"
    (package / "autonomy").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "autonomy" / "cli.py").write_text("VERSION = 'one'\n", encoding="utf-8")
    (repo / "requirements.txt").write_text(f"pydantic=={version('pydantic')}\n", encoding="utf-8")
    (repo / "config").mkdir()
    (repo / "config" / "private-token.json").write_text('{"secret":"never-copy"}')
    return repo


def test_release_source_snapshot_is_independent_and_detects_candidate_drift(tmp_path):
    repo = _source(tmp_path)
    candidate = tmp_path / "candidate"
    expected = source_manifest(repo)
    actual = copy_source_candidate(repo, candidate)

    assert actual == expected
    assert actual["format"] == "jae-release-source-v1"
    assert len(actual["source_sha256"]) == 64
    assert verify_source_candidate(candidate)
    assert read_release_identity(candidate) == {
        "status": "verified", "source_sha256": expected["source_sha256"]
    }
    assert not (candidate / "config").exists()
    assert json.loads((candidate / MANIFEST_NAME).read_text()) == expected

    (repo / "executor" / "autonomy" / "cli.py").write_text("VERSION = 'two'\n")
    assert verify_source_candidate(candidate)
    (candidate / "executor" / "autonomy" / "cli.py").write_text("VERSION = 'tampered'\n")
    assert not verify_source_candidate(candidate)
    assert read_release_identity(candidate) == {
        "status": "unverified", "source_sha256": ""
    }


def test_undecodable_release_manifest_is_unverified_without_exposing_state(tmp_path):
    repo = _source(tmp_path)
    candidate = tmp_path / "candidate"
    copy_source_candidate(repo, candidate)
    (candidate / MANIFEST_NAME).write_bytes(bytes((0xff, 0xfe)))
    assert verify_source_candidate(candidate) is False
    assert read_release_identity(candidate) == {
        "status": "unverified", "source_sha256": ""
    }


def test_candidate_dependency_check_rejects_missing_or_changed_versions(tmp_path):
    repo = _source(tmp_path)
    candidate = tmp_path / "candidate"
    copy_source_candidate(repo, candidate)
    assert installed_dependencies_match(candidate)
    requirements = candidate / "requirements.txt"
    requirements.write_text("pydantic==0.0.0\n", encoding="utf-8")
    assert not installed_dependencies_match(candidate)
    requirements.write_text("missing-jae-dependency==1.0.0\n", encoding="utf-8")
    assert not installed_dependencies_match(candidate)
    requirements.write_text("pydantic>=2\n", encoding="utf-8")
    assert not installed_dependencies_match(candidate)



def test_runtime_snapshot_binds_interpreter_to_release_pins_and_rejects_symlink(tmp_path):
    repo = _source(tmp_path)
    release = tmp_path / "release"
    copy_source_candidate(repo, release)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    private = tmp_path / "private-token"
    private.write_text("synthetic secret", encoding="utf-8")
    (venv / "private").symlink_to(private)
    with pytest.raises(ValueError, match="release_runtime_source_symlink"):
        copy_runtime_candidate(venv, tmp_path / "refused", release)
    assert not (tmp_path / "refused").exists()

    (venv / "private").unlink()
    runtime = tmp_path / "runtime"
    manifest = copy_runtime_candidate(venv, runtime, release)
    assert manifest["format"] == "jae-release-runtime-v1"
    assert verify_runtime_candidate(runtime, release)
    assert (runtime / "bin" / "python").is_file()
    assert not (runtime / "bin" / "python").is_symlink()
    assert not (runtime / "private").exists()
    assert private.read_text() == "synthetic secret"

    (release / "requirements.txt").write_text("pydantic==0.0.0\n")
    assert not verify_runtime_candidate(runtime, release)



def test_copied_virtualenv_runs_after_source_checkout_is_removed(tmp_path):
    repo = _source(tmp_path)
    release = tmp_path / "release"
    copy_source_candidate(repo, release)
    source_venv = repo / ".venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(source_venv)
    runtime = tmp_path / "runtime"
    copy_runtime_candidate(source_venv, runtime, release)
    assert verify_runtime_candidate(runtime, release)

    repo.rename(tmp_path / "checkout-removed")
    result = subprocess.run(
        [str(runtime / "bin" / "python"), "-I", "-B", "-c",
         "import pathlib,sys; print(pathlib.Path(sys.prefix).resolve())"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result.stdout.strip() == str(runtime.resolve())
    assert verify_runtime_candidate(runtime, release)



def test_packaged_default_task_state_survives_release_replacement(tmp_path):
    source = Path(__file__).resolve().parents[1]
    candidate = tmp_path / "staged-release"
    copy_source_candidate(source, candidate)
    user_home = tmp_path / "synthetic-home"
    user_home.mkdir()
    expected = user_home / "Library" / "Application Support" / "AI投递经理" / "autonomy"
    script = (
        "import pathlib,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "from executor.autonomy.queue import RUNTIME,TaskQueue;"
        "assert RUNTIME==pathlib.Path(sys.argv[2]);"
        "TaskQueue()"
    )
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(candidate), str(expected)],
        env={**os.environ, "HOME": str(user_home)},
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert (expected / "tasks.sqlite3").is_file()
    assert not (candidate / "runtime").exists()
    assert verify_source_candidate(candidate)

    replacement = tmp_path / "replacement-release"
    copy_source_candidate(source, replacement)
    subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(replacement), str(expected)],
        env={**os.environ, "HOME": str(user_home)},
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert (expected / "tasks.sqlite3").is_file()
    assert not (replacement / "runtime").exists()
    assert verify_source_candidate(replacement)

def test_runtime_snapshot_refuses_an_unrelated_executable_alias(tmp_path):
    repo = _source(tmp_path)
    release = tmp_path / "release"
    copy_source_candidate(repo, release)
    venv_root = tmp_path / "venv"
    (venv_root / "bin").mkdir(parents=True)
    (venv_root / "bin" / "python").symlink_to(sys.executable)
    unrelated = tmp_path / "other-executable"
    unrelated.write_bytes(b"synthetic private executable")
    unrelated.chmod(0o755)
    (venv_root / "bin" / "python3").symlink_to(unrelated)
    with pytest.raises(ValueError, match="release_runtime_source_symlink"):
        copy_runtime_candidate(venv_root, tmp_path / "refused", release)
    assert not (tmp_path / "refused").exists()



def test_runtime_snapshot_refuses_external_library_directory_alias(tmp_path):
    repo = _source(tmp_path)
    release = tmp_path / "release"
    copy_source_candidate(repo, release)
    venv_root = tmp_path / "venv"
    (venv_root / "bin").mkdir(parents=True)
    (venv_root / "bin" / "python").symlink_to(sys.executable)
    external = tmp_path / "outside-library"
    external.mkdir()
    (external / "private-token").write_text("never-copy", encoding="utf-8")
    (venv_root / "lib64").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="release_runtime_source_symlink"):
        copy_runtime_candidate(venv_root, tmp_path / "refused", release)
    assert not (tmp_path / "refused").exists()


def test_recovery_version_uses_verified_packaged_source(tmp_path):
    repo = _source(tmp_path)
    candidate = tmp_path / "candidate"
    manifest = copy_source_candidate(repo, candidate)
    assert _version(candidate) == manifest["source_sha256"][:12]
    (candidate / "executor" / "autonomy" / "cli.py").write_text(
        "VERSION = 'tampered'\\n", encoding="utf-8"
    )
    assert _version(candidate) == "unknown"


def test_packaged_version_without_manifest_never_invokes_git(tmp_path, monkeypatch):
    from executor.autonomy import bootstrap

    source = tmp_path / "AI投递经理.app" / "Contents" / "Resources" / "release"
    source.mkdir(parents=True)
    invoked = []

    def forbidden_git(*args, **kwargs):
        invoked.append((args, kwargs))
        raise AssertionError("installed app must not invoke Git")

    monkeypatch.setattr(bootstrap.subprocess, "run", forbidden_git)
    assert bootstrap._version(source) == "unknown"
    assert invoked == []


def test_release_candidate_rejects_added_file_and_symlink(tmp_path):
    repo = _source(tmp_path)
    candidate = tmp_path / "candidate"
    copy_source_candidate(repo, candidate)
    added = candidate / "executor" / "unexpected.py"
    added.write_text("print('unexpected')\n")
    assert not verify_source_candidate(candidate)
    added.unlink()
    link = candidate / "executor" / "linked.py"
    link.symlink_to(repo / "executor" / "__init__.py")
    assert not verify_source_candidate(candidate)
    link.unlink()
    assert verify_source_candidate(candidate)
    candidate_link = tmp_path / "candidate-link"
    candidate_link.symlink_to(candidate, target_is_directory=True)
    assert not verify_source_candidate(candidate_link)

    (repo / "executor" / "external.py").symlink_to(
        repo / "executor" / "__init__.py"
    )
    with pytest.raises(ValueError, match="release_source_symlink"):
        copy_source_candidate(repo, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()


def test_packaged_candidate_starts_on_loopback_and_answers_health_without_fqdn(tmp_path):
    source = Path(__file__).resolve().parents[1]
    candidate = tmp_path / "candidate"
    copy_source_candidate(source, candidate)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]

    script = (
        "import pathlib,runpy,socket,sys;"
        "socket.getfqdn=lambda *_: (_ for _ in ()).throw("
        "AssertionError('FQDN lookup during service startup'));"
        f"sys.path.insert(0,{str(candidate)!r});"
        "sys.argv=['executor.autonomy.cli','--runtime',sys.argv[1],"
        "'--port',sys.argv[2],'serve'];"
        "runpy.run_module('executor.autonomy.cli',run_name='__main__')"
    )
    log_path = tmp_path / "candidate-service.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", script, str(runtime), str(port)],
            cwd=candidate, env={**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"},
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        health = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and process.poll() is None:
            token_path = runtime / "auth.token"
            if token_path.exists():
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Authorization": "Bearer " + token_path.read_text().strip()},
                )
                try:
                    with urllib.request.urlopen(request, timeout=1) as response:
                        health = json.load(response)
                    break
                except (OSError, urllib.error.URLError):
                    pass
            time.sleep(0.05)
        assert health == {
            "ok": True,
            "worker_active": None,
            "final_click_actor": "user",
            "loaded_source_sha256": source_manifest(candidate)["source_sha256"],
        }, log_path.read_text(errors="replace")[-4000:]
        assert process.poll() is None
        assert verify_source_candidate(candidate)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
