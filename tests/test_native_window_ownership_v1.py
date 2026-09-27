from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from executor.autonomy.state_compatibility import native_window_guard, task_state_guard


def test_native_window_lease_is_private_stable_and_released_without_pid_guessing(tmp_path):
    root = tmp_path / "state"
    with native_window_guard(root) as fd:
        info = os.fstat(fd)
        path = root / "native-window.lock"
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
        assert path.read_bytes() == b""
        with pytest.raises(BlockingIOError):
            with native_window_guard(root):
                pytest.fail("duplicate native window admitted")
    with native_window_guard(root) as fd:
        current = os.fstat(fd)
        assert (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)
    assert path.read_bytes() == b""


@pytest.mark.parametrize("kind", ["symlink_root", "symlink_parent", "symlink_lock", "hardlink_lock", "fifo_lock", "directory_lock"])
def test_native_window_lease_refuses_alias_and_nonordinary_metadata(tmp_path, kind):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "state"
    if kind == "symlink_root":
        root.symlink_to(target, target_is_directory=True)
    elif kind == "symlink_parent":
        alias = tmp_path / "alias"
        alias.symlink_to(target, target_is_directory=True)
        root = alias / "state"
    else:
        root.mkdir()
        lock = root / "native-window.lock"
        secret = target / "PRIVATE_LEASE_CANARY"
        secret.write_text("PRIVATE_LEASE_CANARY")
        if kind == "symlink_lock":
            lock.symlink_to(secret)
        elif kind == "hardlink_lock":
            os.link(secret, lock)
        elif kind == "fifo_lock":
            os.mkfifo(lock)
        else:
            lock.mkdir()
    with pytest.raises((OSError, ValueError)) as result:
        with native_window_guard(root):
            pytest.fail("invalid native lease admitted")
    assert "PRIVATE_LEASE_CANARY" not in str(result.value)
    if (target / "PRIVATE_LEASE_CANARY").exists():
        assert (target / "PRIVATE_LEASE_CANARY").read_text() == "PRIVATE_LEASE_CANARY"


def test_native_window_and_transactional_activation_share_both_direction_fence(tmp_path):
    root = tmp_path / "state"
    with native_window_guard(root):
        with pytest.raises(BlockingIOError):
            with task_state_guard(root):
                pytest.fail("active window admitted app activation")
    with task_state_guard(root):
        with pytest.raises(BlockingIOError):
            with native_window_guard(root):
                pytest.fail("activation admitted new window")
    with native_window_guard(root):
        pass


def test_native_window_fd_survives_parent_release_until_inherited_child_closes(tmp_path):
    root = tmp_path / "state"
    process = None
    script = "import sys;print('OWNED_CHILD_READY',flush=True);sys.stdin.readline()"
    try:
        with native_window_guard(root) as fd:
            process = subprocess.Popen([sys.executable, "-I", "-c", script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, pass_fds=(fd,))
            assert process.stdout.readline().strip() == "OWNED_CHILD_READY"
        # Closing the parent's descriptor models parent death. A surviving
        # owned child keeps the SAME kernel lease, with no PID-file takeover.
        with pytest.raises(BlockingIOError):
            with native_window_guard(root):
                pytest.fail("surviving child lost ownership")
        process.stdin.write("close\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 0
        with native_window_guard(root):
            pass
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


@pytest.mark.parametrize("invalid", [True, False, -1, "3", 1.0])
def test_presenter_rejects_non_descriptor_ownership(invalid):
    from executor.autonomy.macos_host import NativePresenter
    with pytest.raises(ValueError, match="native_host_ownership_invalid"):
        NativePresenter("unused", ownership_fd=invalid)


def _recognized_app(tmp_path, monkeypatch):
    from executor.autonomy import cli, consumer
    app = tmp_path / "Synthetic.app"
    source = app / "Contents/Resources/release"
    module = source / "executor/autonomy/cli.py"
    module.parent.mkdir(parents=True)
    executable = app / "Contents/MacOS/AIApplicationManager"
    executable.parent.mkdir(parents=True)
    executable.write_text(consumer._native_packaged_launcher())
    monkeypatch.setattr(cli, "__file__", str(module))
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(consumer, "_trusted_bundle", lambda candidate: candidate == app)
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("browser fallback"))
    return cli


def test_duplicate_native_launch_never_mints_ticket_starts_service_or_resets_view(tmp_path, monkeypatch):
    from executor.autonomy import macos_host
    cli = _recognized_app(tmp_path, monkeypatch)
    root = tmp_path / "state"
    from executor.autonomy.queue import TaskQueue, TaskSpec
    queue = TaskQueue(root)
    task = queue.enqueue(TaskSpec(company="Synthetic singleton", role="Engineer",
        target_url="https://example.invalid/jobs/singleton",
        profile_ref="synthetic-profile.json"))
    queue.pause(task["task_id"])
    queue.remember_task_view(task["task_id"], expected_revision=0)
    before = (queue.tasks(), queue.recent_events(1000), queue.task_view_context())
    calls, nested = [], []
    class Presenter:
        def __init__(self, directory, *, consumer_smoke, ownership_fd):
            assert os.fstat(ownership_fd).st_nlink == 1
            calls.append("presenter")
        def wait_for_close(self):
            nested.append(cli.launch_native_consumer(root, 9344))
            calls.append("closed")
            return True
        def close(self):
            calls.append("cleanup")
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    monkeypatch.setattr(cli, "launch_consumer",
        lambda *a, **k: calls.append("service_and_ticket") or {"ok": True, "opened": True})
    result = cli.launch_native_consumer(root, 9344)
    assert result == {"ok": True, "opened": True, "native_window": True,
        "native_page": None, "final_click_actor": "user"}
    assert nested == [{"ok": False, "opened": False, "reason": "native_window_already_open"}]
    assert calls == ["presenter", "service_and_ticket", "closed", "cleanup"]
    assert (queue.tasks(), queue.recent_events(1000), queue.task_view_context()) == before
    with native_window_guard(root):
        pass


def test_failed_native_launch_releases_ownership_and_invalid_root_is_safe(tmp_path, monkeypatch):
    from executor.autonomy import macos_host
    cli = _recognized_app(tmp_path, monkeypatch)
    class Presenter:
        def __init__(self, *a, **k):
            pass
        def close(self):
            pass
        def wait_for_close(self):
            pytest.fail("unopened window waited")
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    calls = []
    monkeypatch.setattr(cli, "launch_consumer",
        lambda *a, **k: calls.append(True) or {"ok": False, "opened": False})
    root = tmp_path / "state"
    result = cli.launch_native_consumer(root, 9344)
    assert result["opened"] is False and result["ok"] is False
    with native_window_guard(root):
        pass
    alias = tmp_path / "PRIVATE_ALIAS_CANARY"
    alias.symlink_to(root, target_is_directory=True)
    refused = cli.launch_native_consumer(alias, 9344)
    assert refused == {"ok": False, "opened": False, "reason": "native_window_state_invalid"}
    assert calls == [True] and "PRIVATE_ALIAS_CANARY" not in json.dumps(refused)
