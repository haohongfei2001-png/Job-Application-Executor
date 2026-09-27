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
    monkeypatch.setattr(cli, "handoff_installed_consumer",
                        lambda *a, **k: pytest.fail("ordinary close cannot release/update task authority"))
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
        def take_release_request(self):
            return None  # Ordinary close has no native release intent.
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
        def take_release_request(self):
            pytest.fail("unopened window cannot inspect a release intent")
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


# Focus-only reopen uses a private socket, never a second window writer.
@pytest.fixture
def short_reopen_root():
    import tempfile
    # Real Unix socket path limits are retained. Keep this isolated cloud fixture
    # short on BOTH platforms; long-path refusal is tested separately below.
    with tempfile.TemporaryDirectory(prefix="jwr-",
            dir="/private/tmp" if sys.platform == "darwin" else "/tmp") as temporary:
        yield Path(temporary) / "state"


def test_focus_only_endpoint_uses_real_child_process_without_private_state_access(short_reopen_root):
    from executor.autonomy.native_reopen import NativeReopenServer
    from executor.autonomy.queue import TaskQueue, TaskSpec
    root = short_reopen_root
    queue = TaskQueue(root)
    task = queue.enqueue(TaskSpec(company="Synthetic reopen", role="Engineer",
        target_url="https://example.invalid/jobs/reopen", profile_ref="synthetic.json"))
    queue.pause(task["task_id"])
    queue.remember_task_view(task["task_id"], expected_revision=0)
    before = (queue.tasks(), queue.recent_events(1000), queue.task_view_context())
    private = root / "PRIVATE_REOPEN_CANARY"
    private.write_bytes(b"PRIVATE_REOPEN_CANARY")
    calls = []
    child_source = """
import sys
sys.path.insert(0, sys.argv[1])
from executor.autonomy.native_reopen import request_owned_focus
print("FOCUSED" if request_owned_focus(sys.argv[2]) else "REFUSED")
"""
    with native_window_guard(root) as fd:
        server = NativeReopenServer(root, fd, lambda: calls.append(True) or True)
        assert server.start() is True
        try:
            assert stat.S_IMODE((root / "native-focus.sock").stat().st_mode) == 0o600
            payload = {p.name: p.read_bytes() for p in root.iterdir() if p.name != "native-focus.sock"}
            for _ in range(2):
                child = subprocess.run([sys.executable, "-I", "-B", "-c", child_source,
                    str(Path(__file__).resolve().parents[1]), str(root)],
                    capture_output=True, text=True, timeout=8, check=False)
                assert child.returncode == 0 and child.stdout == "FOCUSED\n" and child.stderr == ""
            assert calls == [True, True]
            # Check all journal/private bytes BEFORE fixture SELECTs change SHM.
            assert {p.name: p.read_bytes() for p in root.iterdir() if p.name != "native-focus.sock"} == payload
            assert (queue.tasks(), queue.recent_events(1000), queue.task_view_context()) == before
            with pytest.raises(BlockingIOError):
                with task_state_guard(root):
                    pytest.fail("focus released the window/update fence")
            assert "PRIVATE_" not in repr(server)
        finally:
            server.close()
        assert not (root / "native-focus.sock").exists()
        assert (root / "native-window.lock").read_bytes() == b""


@pytest.mark.parametrize("packet", [
    b'{"command":"focus","url":"PRIVATE_FORGED_URL"}\n',
    b'{"command":"focus","ticket":"PRIVATE_FORGED_TICKET"}\n',
    b'{"command":true}\n', b'{"command":"launch"}\n',
    b'{"command":"focus"}\n{"command":"focus"}\n',
    b'PRIVATE_APPLICANT_CANARY\n', b"x" * 65,
])
def test_reopen_rejects_every_non_focus_packet_without_echo_or_callback(short_reopen_root, packet):
    import socket
    from executor.autonomy.native_reopen import NativeReopenServer
    calls = []
    with native_window_guard(short_reopen_root) as fd:
        server = NativeReopenServer(short_reopen_root, fd, lambda: calls.append(True) or True)
        assert server.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(short_reopen_root / "native-focus.sock"))
                connection.sendall(packet)
                connection.shutdown(socket.SHUT_WR)
                reply = connection.recv(128)
            assert reply == b'{"ok":false,"focus_only":false}\n'
            assert calls == [] and b"PRIVATE_" not in reply
        finally:
            server.close()


@pytest.mark.parametrize("changed", ["root_alias", "socket_alias", "socket_file", "foreign_identity", "lease_inode"])
def test_reopen_refuses_changed_authority_without_touching_replacement(short_reopen_root, monkeypatch, changed):
    from executor.autonomy import native_reopen
    root, calls = short_reopen_root, []
    with native_window_guard(root) as fd:
        server = native_reopen.NativeReopenServer(root, fd, lambda: calls.append(True) or True)
        assert server.start()
        supplied, replacement = root, None
        try:
            if changed == "root_alias":
                supplied = root.parent / "alias"
                supplied.symlink_to(root, target_is_directory=True)
            elif changed in {"socket_alias", "socket_file"}:
                path = root / "native-focus.sock"
                path.unlink()
                replacement = root / "PRIVATE_REPLACEMENT_CANARY"
                replacement.write_bytes(b"PRIVATE_REPLACEMENT_CANARY")
                if changed == "socket_alias":
                    path.symlink_to(replacement)
                else:
                    path.write_bytes(b"PRIVATE_REPLACEMENT_CANARY")
                    replacement = path
            elif changed == "lease_inode":
                path = root / "native-window.lock"
                path.unlink()
                path.write_bytes(b"")
                path.chmod(0o600)
            if changed == "foreign_identity":
                current_uid = os.geteuid()
                with monkeypatch.context() as context:
                    context.setattr(native_reopen.os, "geteuid", lambda: current_uid + 1)
                    assert native_reopen.request_owned_focus(supplied) is False
            else:
                assert native_reopen.request_owned_focus(supplied) is False
            assert calls == []
        finally:
            server.close()
        if replacement is not None:
            assert replacement.read_bytes() == b"PRIVATE_REPLACEMENT_CANARY"


def test_reopen_cannot_adopt_stale_socket_without_active_window_lease(short_reopen_root):
    import socket
    from executor.autonomy.native_reopen import NativeReopenServer, request_owned_focus
    root = short_reopen_root
    with native_window_guard(root):
        pass
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = root / "native-focus.sock"
    listener.bind(str(path));path.chmod(0o600);listener.listen(1)
    calls = []
    try:
        server = NativeReopenServer(root, -1, lambda: calls.append(True) or True)
        assert server.start() is False
        assert request_owned_focus(root) is False
        assert path.exists() and calls == []
        # A fresh exact lease can replace only that owned stale SOCKET.
        listener.close()
        with native_window_guard(root) as fd:
            recovered = NativeReopenServer(root, fd, lambda: calls.append(True) or True)
            assert recovered.start()
            try:
                assert request_owned_focus(root) is True
                assert calls == [True]
            finally:
                recovered.close()
    finally:
        listener.close()
    assert not path.exists()


def test_duplicate_cli_focuses_existing_presenter_without_ticket_service_or_journal_write(short_reopen_root, monkeypatch):
    from executor.autonomy import macos_host
    from executor.autonomy.queue import TaskQueue, TaskSpec
    cli = _recognized_app(short_reopen_root.parent, monkeypatch)
    root = short_reopen_root
    queue = TaskQueue(root)
    task = queue.enqueue(TaskSpec(company="Synthetic CLI reopen", role="Engineer",
        target_url="https://example.invalid/jobs/reopen", profile_ref="synthetic.json"))
    queue.pause(task["task_id"]);queue.remember_task_view(task["task_id"], expected_revision=0)
    before = (queue.tasks(), queue.recent_events(1000), queue.task_view_context())
    calls, nested = [], []
    class Presenter:
        def __init__(self, directory, *, consumer_smoke, ownership_fd):
            assert os.fstat(ownership_fd).st_nlink == 1
            calls.append("presenter")
        def focus(self):
            calls.append("focus")
            return True
        def wait_for_close(self):
            nested.append(cli.launch_native_consumer(root, 9344))
            calls.append("closed")
            return True
        def take_release_request(self):
            return None  # Ordinary close has no native release intent.
        def close(self):
            calls.append("cleanup")
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    monkeypatch.setattr(cli, "launch_consumer",
        lambda *a, **k: calls.append("service_and_ticket") or {"ok": True, "opened": True})
    result = cli.launch_native_consumer(root, 9344)
    assert result == {"ok": True, "opened": True, "native_window": True,
        "native_page": None, "final_click_actor": "user"}
    assert nested == [{"ok": True, "opened": True, "native_window": True,
        "focus_only": True, "final_click_actor": "user"}]
    assert calls == ["presenter", "service_and_ticket", "focus", "closed", "cleanup"]
    assert (queue.tasks(), queue.recent_events(1000), queue.task_view_context()) == before
    assert not (root / "native-focus.sock").exists()


def test_long_socket_path_refuses_reopen_without_deleting_private_state(short_reopen_root):
    from executor.autonomy.native_reopen import NativeReopenServer, request_owned_focus
    root = short_reopen_root / ("long-owned-segment-" * 7)
    calls = []
    with native_window_guard(root) as fd:
        marker = root / "PRIVATE_LONG_PATH_CANARY";marker.write_bytes(b"PRIVATE_LONG_PATH_CANARY")
        server = NativeReopenServer(root, fd, lambda: calls.append(True) or True)
        assert server.start() is False
        assert request_owned_focus(root) is False
        assert marker.read_bytes() == b"PRIVATE_LONG_PATH_CANARY" and calls == []
        assert (root / "native-window.lock").read_bytes() == b""


# Long roots use the same window lease with a private nonce, never a task API.
@pytest.fixture
def long_reopen_root(tmp_path):
    return tmp_path / ("long-owned-segment-" * 7)


def test_long_root_reopen_real_children_preserve_all_populated_private_state(long_reopen_root, monkeypatch):
    import socket
    from executor.autonomy.native_reopen import create_owned_reopen_server, request_owned_focus
    from executor.autonomy.queue import TaskQueue, TaskSpec
    root = long_reopen_root
    queue = TaskQueue(root)
    tasks = [queue.enqueue(TaskSpec(company=f"Synthetic long reopen {index}", role="Engineer",
        target_url=f"https://example.invalid/jobs/long-{index}", profile_ref=f"synthetic-{index}.json"))
        for index in range(3)]
    queue.pause(tasks[0]["task_id"])
    queue.remember_task_view(tasks[0]["task_id"], expected_revision=0)
    before = (queue.tasks(), queue.recent_events(1000), queue.task_view_context())
    private = root / "PRIVATE_LONG_REOPEN_CANARY"
    private.write_bytes(b"PRIVATE_LONG_REOPEN_CANARY")
    calls = []
    monkeypatch.setattr(socket, "getfqdn", lambda *a, **k: pytest.fail("focus resolved FQDN"))
    child_source = """
import sys
sys.path.insert(0, sys.argv[1])
from executor.autonomy.native_reopen import request_owned_focus
print("FOCUSED" if request_owned_focus(sys.argv[2]) else "REFUSED")
"""
    with native_window_guard(root) as fd:
        server = create_owned_reopen_server(root, fd, lambda: calls.append(True) or True)
        assert server.start() is True
        endpoint = root / "native-focus.loopback.json"
        try:
            record = json.loads(endpoint.read_bytes())
            assert stat.S_IMODE(root.stat().st_mode) == 0o700
            assert stat.S_IMODE(endpoint.stat().st_mode) == 0o600
            assert server.listener.getsockname() == ("127.0.0.1", record["port"])
            assert record["root_identity"] == [root.stat().st_dev, root.stat().st_ino]
            assert record["lease_identity"] == [os.fstat(fd).st_dev, os.fstat(fd).st_ino]
            payload = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file() and p != endpoint}
            for _ in range(2):
                child = subprocess.run([sys.executable, "-I", "-B", "-c", child_source,
                    str(Path(__file__).resolve().parents[1]), str(root)],
                    capture_output=True, text=True, timeout=8, check=False)
                assert child.returncode == 0 and child.stdout == "FOCUSED\n" and child.stderr == ""
            assert calls == [True, True]
            assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file() and p != endpoint} == payload
            assert (queue.tasks(), queue.recent_events(1000), queue.task_view_context()) == before
            for guard in (native_window_guard, task_state_guard):
                with pytest.raises(BlockingIOError):
                    with guard(root):
                        pytest.fail("long focus released the existing writer/update fence")
            assert "PRIVATE_" not in repr(server) and record["nonce"] not in repr(server)
            assert not (root / "native-focus.sock").exists()
        finally:
            server.close()
        assert not endpoint.exists() and private.read_bytes() == b"PRIVATE_LONG_REOPEN_CANARY"
    assert request_owned_focus(root) is False
    with native_window_guard(root):
        pass


@pytest.mark.parametrize("kind", ["missing_nonce", "wrong_nonce", "extra_private_route", "oversized"])
def test_long_reopen_refuses_unauthenticated_or_non_focus_packets(long_reopen_root, kind):
    import socket
    from executor.autonomy.native_reopen import create_owned_reopen_server
    root, calls = long_reopen_root, []
    with native_window_guard(root) as fd:
        server = create_owned_reopen_server(root, fd, lambda: calls.append(True) or True)
        assert server.start()
        try:
            record = json.loads((root / "native-focus.loopback.json").read_bytes())
            if kind == "missing_nonce":
                packet = b'{"command":"focus"}\n'
            elif kind == "wrong_nonce":
                packet = b'{"command":"focus","nonce":"' + b"0" * 64 + b'"}\n'
                assert record["nonce"] != "0" * 64
            elif kind == "extra_private_route":
                packet = json.dumps({"command": "focus", "nonce": record["nonce"],
                    "url": "PRIVATE_FORGED_ROUTE"}).encode() + b"\n"
            else:
                packet = b"x" * 129
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(("127.0.0.1", record["port"]))
                connection.sendall(packet)
                connection.shutdown(socket.SHUT_WR)
                reply = connection.recv(128)
            assert reply == b'{"ok":false,"focus_only":false}\n'
            assert calls == [] and b"PRIVATE_" not in reply and record["nonce"].encode() not in reply
        finally:
            server.close()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "malformed", "foreign_root"])
def test_long_reopen_preserves_unrecognized_endpoint_authority(long_reopen_root, kind):
    from executor.autonomy.native_reopen import create_owned_reopen_server, request_owned_focus
    root, calls = long_reopen_root, []
    with native_window_guard(root) as fd:
        endpoint = root / "native-focus.loopback.json"
        marker = root / "PRIVATE_ENDPOINT_CANARY"
        marker.write_bytes(b"PRIVATE_ENDPOINT_CANARY")
        if kind == "symlink":
            endpoint.symlink_to(marker)
        elif kind == "hardlink":
            os.link(marker, endpoint)
        elif kind == "fifo":
            os.mkfifo(endpoint, 0o600)
        elif kind == "malformed":
            endpoint.write_bytes(b"PRIVATE_MALFORMED_ENDPOINT")
            endpoint.chmod(0o600)
        else:
            record = {"format": "jae-native-loopback-focus-v1", "nonce": "0" * 64, "port": 9344,
                "root_identity": [root.stat().st_dev, root.stat().st_ino + 1],
                "lease_identity": [os.fstat(fd).st_dev, os.fstat(fd).st_ino]}
            endpoint.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            endpoint.chmod(0o600)
        identity = (endpoint.lstat().st_dev, endpoint.lstat().st_ino, endpoint.lstat().st_mode)
        server = create_owned_reopen_server(root, fd, lambda: calls.append(True) or True)
        assert server.start() is False and request_owned_focus(root) is False
        server.close()
        assert (endpoint.lstat().st_dev, endpoint.lstat().st_ino, endpoint.lstat().st_mode) == identity
        assert marker.read_bytes() == b"PRIVATE_ENDPOINT_CANARY" and calls == []
        if kind in {"malformed", "foreign_root"}:
            assert endpoint.read_bytes().startswith(b"PRIVATE_" if kind == "malformed" else b"{")
        if kind == "symlink":
            assert endpoint.readlink() == marker


@pytest.mark.parametrize("changed", ["endpoint_inode", "nonce", "lease_inode"])
def test_long_reopen_refuses_mutated_authority_and_preserves_replacement(long_reopen_root, changed):
    from executor.autonomy.native_reopen import create_owned_reopen_server, request_owned_focus
    root, calls = long_reopen_root, []
    with native_window_guard(root) as fd:
        server = create_owned_reopen_server(root, fd, lambda: calls.append(True) or True)
        assert server.start()
        endpoint = root / "native-focus.loopback.json"
        try:
            original = endpoint.read_bytes()
            if changed == "endpoint_inode":
                endpoint.rename(root / "original-endpoint")
                endpoint.write_bytes(original)
                endpoint.chmod(0o600)
            elif changed == "nonce":
                record = json.loads(original)
                record["nonce"] = "0" * 64 if record["nonce"] != "0" * 64 else "1" * 64
                endpoint.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            else:
                lease = root / "native-window.lock"
                lease.rename(root / "original-lease")
                lease.write_bytes(b"")
                lease.chmod(0o600)
            replacement = endpoint.read_bytes()
            assert request_owned_focus(root) is False and calls == []
        finally:
            server.close()
        assert endpoint.read_bytes() == replacement
        assert (root / "native-window.lock").read_bytes() == b""


def test_long_duplicate_cli_focuses_existing_window_without_new_service_or_ticket(long_reopen_root, monkeypatch):
    from executor.autonomy import macos_host
    from executor.autonomy.queue import TaskQueue, TaskSpec
    cli = _recognized_app(long_reopen_root.parent, monkeypatch)
    root = long_reopen_root
    queue = TaskQueue(root)
    task = queue.enqueue(TaskSpec(company="Synthetic long CLI reopen", role="Engineer",
        target_url="https://example.invalid/jobs/reopen", profile_ref="synthetic.json"))
    queue.pause(task["task_id"])
    queue.remember_task_view(task["task_id"], expected_revision=0)
    before = (queue.tasks(), queue.recent_events(1000), queue.task_view_context())
    calls, nested = [], []
    class Presenter:
        def __init__(self, directory, *, consumer_smoke, ownership_fd):
            assert os.fstat(ownership_fd).st_nlink == 1
            calls.append("presenter")
        def focus(self):
            calls.append("focus")
            return True
        def wait_for_close(self):
            nested.append(cli.launch_native_consumer(root, 9344))
            calls.append("closed")
            return True
        def take_release_request(self):
            return None  # Ordinary close has no native release intent.
        def close(self):
            calls.append("cleanup")
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    monkeypatch.setattr(cli, "launch_consumer",
        lambda *a, **k: calls.append("service_and_ticket") or {"ok": True, "opened": True})
    assert cli.launch_native_consumer(root, 9344) == {
        "ok": True, "opened": True, "native_window": True, "native_page": None,
        "final_click_actor": "user"}
    assert nested == [{"ok": True, "opened": True, "native_window": True,
        "focus_only": True, "final_click_actor": "user"}]
    assert calls == ["presenter", "service_and_ticket", "focus", "closed", "cleanup"]
    assert (queue.tasks(), queue.recent_events(1000), queue.task_view_context()) == before
    assert not (root / "native-focus.loopback.json").exists()


def test_long_stale_endpoint_requires_actual_kernel_lease_before_recovery(long_reopen_root):
    from executor.autonomy.native_reopen import create_owned_reopen_server, request_owned_focus
    root, calls = long_reopen_root, []
    with native_window_guard(root) as fd:
        lease_identity = [os.fstat(fd).st_dev, os.fstat(fd).st_ino]
    endpoint = root / "native-focus.loopback.json"
    stale = {"format": "jae-native-loopback-focus-v1", "nonce": "0" * 64, "port": 9344,
        "root_identity": [root.stat().st_dev, root.stat().st_ino],
        "lease_identity": lease_identity}
    endpoint.write_text(json.dumps(stale, sort_keys=True, separators=(",", ":")) + "\n")
    endpoint.chmod(0o600)
    before = endpoint.read_bytes()
    refused = create_owned_reopen_server(root, -1, lambda: calls.append(True) or True)
    assert refused.start() is False and request_owned_focus(root) is False
    assert endpoint.read_bytes() == before and calls == []
    with native_window_guard(root) as fd:
        recovered = create_owned_reopen_server(root, fd, lambda: calls.append(True) or True)
        assert recovered.start()
        try:
            assert json.loads(endpoint.read_bytes())["nonce"] != stale["nonce"]
            assert request_owned_focus(root) is True and calls == [True]
        finally:
            recovered.close()
    assert not endpoint.exists()


@pytest.mark.parametrize("field,value", [
    ("port", True), ("port", 9344.0), ("port", -1),
    ("nonce", "PRIVATE_FORGED_NONCE"), ("extra", "PRIVATE_EXTRA_FIELD"),
    ("root_identity", [True, 1]),
])
def test_long_reopen_rejects_invalid_metadata_before_any_socket(long_reopen_root, monkeypatch, field, value):
    import socket
    from executor.autonomy.native_reopen import create_owned_reopen_server, request_owned_focus
    root = long_reopen_root
    with native_window_guard(root) as fd:
        endpoint = root / "native-focus.loopback.json"
        record = {"format": "jae-native-loopback-focus-v1", "nonce": "0" * 64, "port": 9344,
            "root_identity": [root.stat().st_dev, root.stat().st_ino],
            "lease_identity": [os.fstat(fd).st_dev, os.fstat(fd).st_ino]}
        record[field] = value
        endpoint.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        endpoint.chmod(0o600)
        before = endpoint.read_bytes()
        monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("invalid metadata opened a socket"))
        server = create_owned_reopen_server(root, fd, lambda: pytest.fail("invalid metadata focused"))
        assert server.start() is False and request_owned_focus(root) is False
        assert endpoint.read_bytes() == before


@pytest.mark.parametrize("opened,closed,smoke", [
    (False, False, False), (True, False, False),
    (True, True, True), (True, True, False),
])
def test_native_ordinary_close_contract_never_becomes_release_handoff(
    tmp_path, monkeypatch, opened, closed, smoke
):
    from executor.autonomy import macos_host
    cli = _recognized_app(tmp_path, monkeypatch)
    root = tmp_path / "retained-authority"
    root.mkdir()
    canary = root / "retained-private"
    canary.write_bytes(b"PRIVATE_ORDINARY_CLOSE_CANARY" * 1000)
    original = canary.read_bytes()
    calls = []
    class Presenter:
        def __init__(self, directory, *, consumer_smoke, ownership_fd):
            assert consumer_smoke is smoke and os.fstat(ownership_fd).st_nlink == 1
        def wait_for_close(self):
            assert opened is True
            with pytest.raises(BlockingIOError):
                with native_window_guard(root):
                    pytest.fail("window wait lost native owner")
            calls.append("wait")
            return closed
        def take_release_request(self):
            assert opened is True and closed is True and smoke is False
            calls.append("ordinary_intent")
            return None
        def close(self):
            calls.append("cleanup")
    monkeypatch.setattr(macos_host, "NativePresenter", Presenter)
    monkeypatch.setattr(cli, "launch_consumer",
                        lambda *a, **k: {"ok": opened, "opened": opened})
    result = cli.launch_native_consumer(root, 9344, smoke=smoke)
    assert result == {"ok": opened and closed, "opened": opened,
                     "native_window": opened, "native_page": closed if smoke else None,
                     "final_click_actor": "user"}
    assert calls == (["wait"] if opened else []) + (
        ["ordinary_intent"] if opened and closed and not smoke else []) + ["cleanup"]
    assert canary.read_bytes() == original
    assert "PRIVATE_" not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
    assert not (root / "tasks.sqlite3").exists() and not (root / "service.json").exists()
    with native_window_guard(root):
        pass
