from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from executor.autonomy.bootstrap import _version

from executor.autonomy.release import (
    MANIFEST_NAME,
    copy_source_candidate,
    read_release_identity,
    source_manifest,
    verify_source_candidate,
)


def _source(tmp_path):
    repo = tmp_path / "source"
    package = repo / "executor"
    (package / "autonomy").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "autonomy" / "cli.py").write_text("VERSION = 'one'\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("pydantic==2.13.0\n", encoding="utf-8")
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


def test_recovery_version_uses_verified_packaged_source(tmp_path):
    repo = _source(tmp_path)
    candidate = tmp_path / "candidate"
    manifest = copy_source_candidate(repo, candidate)
    assert _version(candidate) == manifest["source_sha256"][:12]
    (candidate / "executor" / "autonomy" / "cli.py").write_text(
        "VERSION = 'tampered'\\n", encoding="utf-8"
    )
    assert _version(candidate) == "unknown"


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
            [sys.executable, "-I", "-c", script, str(runtime), str(port)],
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
            "worker_active": False,
            "final_click_actor": "user",
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
