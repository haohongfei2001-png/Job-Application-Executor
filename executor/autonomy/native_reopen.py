"""Private, focus-only IPC for an already-owned native window.

No URL, ticket, task command or applicant value crosses this endpoint. The
ordinary kernel lease remains the authority; stale endpoints cannot mint one.
Long task-root paths use a nonce-authenticated loopback focus handshake under
that same lease; the ordinary Unix transport and its refusal semantics remain.
"""
from __future__ import annotations

import fcntl
import json
import secrets
import os
from pathlib import Path
import socket
import stat
import threading
import time

_REQUEST = b'{"command":"focus"}\n'
_ACCEPTED = b'{"ok":true,"focus_only":true}\n'
_REFUSED = b'{"ok":false,"focus_only":false}\n'
_NAME = "native-focus.sock"


def _root(value):
    root = Path(value).expanduser().absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("native_reopen_unavailable")
    info = root.stat(follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("native_reopen_unavailable")
    return root


def _identity(info):
    return info.st_dev, info.st_ino


def _lock_info(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)


def _active_lease(root, descriptor=None):
    path = root / "native-window.lock"
    fd = None
    try:
        before = path.stat(follow_symlinks=False)
        if not _lock_info(before):
            return False
        if descriptor is not None:
            held = os.fstat(descriptor)
            if not _lock_info(held) or _identity(held) != _identity(before):
                return False
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        current = os.fstat(fd)
        if not _lock_info(current) or _identity(current) != _identity(before):
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _identity(path.stat(follow_symlinks=False)) == _identity(current)
        return False
    except (OSError, ValueError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _socket_info(path):
    info = path.stat(follow_symlinks=False)
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("native_reopen_unavailable")
    return _identity(info)


def _receive(connection, seconds, limit=64):
    deadline, chunks, size = time.monotonic() + seconds, [], 0
    while size <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        connection.settimeout(remaining)
        chunk = connection.recv(limit + 1 - size)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
    return None


class NativeReopenServer:
    """One ephemeral endpoint under the held private task-root lease."""
    def __init__(self, root, ownership_fd, focus):
        self.root, self.ownership_fd, self.focus = root, ownership_fd, focus
        self.listener = self.thread = self.identity = self.path = None
        self.stopped = threading.Event()

    def __repr__(self):
        return "<NativeReopenServer focus_only=True>"

    def start(self):
        try:
            root = _root(self.root)
            if (type(self.ownership_fd) is not int or self.ownership_fd < 0
                    or not callable(self.focus)
                    or not _active_lease(root, self.ownership_fd)):
                return False
            self.root, self.path = root, root / _NAME
            # Acquiring this exact kernel lease already proved no previous
            # presenter owns it. Remove only its private stale SOCKET, never a
            # symlink, regular file, foreign inode, live lease or task record.
            if self.path.exists() or self.path.is_symlink():
                _socket_info(self.path)
                self.path.unlink()
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind(str(self.path))
            info = self.path.stat(follow_symlinks=False)
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                raise ValueError("native_reopen_unavailable")
            self.identity = _identity(info)
            os.chmod(self.path, 0o600, follow_symlinks=False)
            if _socket_info(self.path) != self.identity:
                raise ValueError("native_reopen_unavailable")
            self.listener.listen(4)
            self.thread = threading.Thread(target=self._serve, daemon=True)
            self.thread.start()
            return True
        except (OSError, ValueError, TypeError, AttributeError):
            self.close()
            return False

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except (OSError, AttributeError):
                return
            with connection:
                ok = False
                try:
                    if (_receive(connection, 1) == _REQUEST
                            and not self.stopped.is_set()
                            and _root(self.root) == self.root
                            and _socket_info(self.path) == self.identity
                            and _active_lease(self.root, self.ownership_fd)):
                        ok = self.focus() is True
                    connection.sendall(_ACCEPTED if ok else _REFUSED)
                except Exception:
                    # Never echo an input, path or exception into diagnostics.
                    try:
                        connection.sendall(_REFUSED)
                    except OSError:
                        pass

    def close(self):
        self.stopped.set()
        listener, self.listener = self.listener, None
        if listener is not None:
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            listener.close()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self.path is not None and self.identity is not None:
            try:
                if _socket_info(self.path) == self.identity:
                    self.path.unlink()
            except (OSError, ValueError):
                pass
        self.thread = self.identity = None


def request_owned_focus(root):
    """One request; uncertainty never starts a service or a second window."""
    try:
        root = _root(root)
        if _needs_loopback(root):
            return _request_loopback_focus(root)
        path = root / _NAME
        identity = _socket_info(path)
        if not _active_lease(root):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(6)
            connection.connect(str(path))
            if _socket_info(path) != identity or not _active_lease(root):
                return False
            connection.sendall(_REQUEST)
            connection.shutdown(socket.SHUT_WR)
            accepted = _receive(connection, 6) == _ACCEPTED
        return (accepted and _root(root) == root
                and _socket_info(path) == identity and _active_lease(root))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


# Portable bound below both Darwin and Linux sockaddr_un pathname limits.
_LOOPBACK_NAME = "native-focus.loopback.json"
_FORMAT = "jae-native-loopback-focus-v1"


def _needs_loopback(root):
    return len(os.fsencode(Path(root).expanduser().absolute() / _NAME)) > 100


def _endpoint_bytes(record):
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_loopback_endpoint(root):
    path = root / _LOOPBACK_NAME
    before = path.stat(follow_symlinks=False)
    if not _lock_info(before) or before.st_size > 2048:
        raise ValueError("native_reopen_unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(fd)
        if not _lock_info(current) or _identity(current) != _identity(before):
            raise ValueError("native_reopen_unavailable")
        payload = os.read(fd, 2049)
    finally:
        os.close(fd)
    record = json.loads(payload)
    if (type(record) is not dict or set(record) != {
            "format", "nonce", "port", "root_identity", "lease_identity"}
            or record.get("format") != _FORMAT
            or type(record.get("port")) is not int or not 1 <= record["port"] <= 65535
            or type(record.get("nonce")) is not str or len(record["nonce"]) != 64
            or any(char not in "0123456789abcdef" for char in record["nonce"])
            or _endpoint_bytes(record) != payload):
        raise ValueError("native_reopen_unavailable")
    for name in ("root_identity", "lease_identity"):
        values = record[name]
        if (type(values) is not list or len(values) != 2
                or any(type(value) is not int or value < 0 for value in values)):
            raise ValueError("native_reopen_unavailable")
    lease = (root / "native-window.lock").stat(follow_symlinks=False)
    if (not _lock_info(lease)
            or record["root_identity"] != list(_identity(_root(root).stat(follow_symlinks=False)))
            or record["lease_identity"] != list(_identity(lease))
            or _identity(path.stat(follow_symlinks=False)) != _identity(current)):
        raise ValueError("native_reopen_unavailable")
    return record, _identity(current)


def _focus_packet(nonce):
    return ('{"command":"focus","nonce":"' + nonce + '"}\n').encode()


class _LoopbackFocusServer:
    """Ephemeral focus-only transport; no ticket/URL/task protocol."""
    def __init__(self, root, ownership_fd, focus):
        self.root, self.ownership_fd, self.focus = root, ownership_fd, focus
        self.listener = self.thread = self.identity = self.path = self.record = None
        self.stopped = threading.Event()

    def __repr__(self):
        return "<NativeReopenServer focus_only=True>"

    def start(self):
        try:
            root = _root(self.root)
            if (type(self.ownership_fd) is not int or self.ownership_fd < 0
                    or not callable(self.focus)
                    or not _active_lease(root, self.ownership_fd)):
                return False
            self.root, self.path = root, root / _LOOPBACK_NAME
            # Only a well-formed endpoint bound to this exact root/lease may
            # be stale. Never remove aliases, unknown bytes or private state.
            if self.path.exists() or self.path.is_symlink():
                _record, identity = _read_loopback_endpoint(root)
                if _identity(self.path.stat(follow_symlinks=False)) != identity:
                    return False
                self.path.unlink()
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(4)
            self.record = {
                "format": _FORMAT, "nonce": secrets.token_hex(32),
                "port": self.listener.getsockname()[1],
                "root_identity": list(_identity(root.stat(follow_symlinks=False))),
                "lease_identity": list(_identity(os.fstat(self.ownership_fd))),
            }
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                if not _lock_info(info):
                    raise ValueError("native_reopen_unavailable")
                self.identity = _identity(info)
                payload = _endpoint_bytes(self.record)
                if os.write(fd, payload) != len(payload):
                    raise ValueError("native_reopen_unavailable")
                os.fsync(fd)
            finally:
                os.close(fd)
            if not self._current():
                raise ValueError("native_reopen_unavailable")
            self.thread = threading.Thread(target=self._serve, daemon=True)
            self.thread.start()
            return True
        except (OSError, ValueError, TypeError, AttributeError):
            self.close()
            return False

    def _current(self):
        record, identity = _read_loopback_endpoint(_root(self.root))
        return (record == self.record and identity == self.identity
                and _active_lease(self.root, self.ownership_fd))

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except (OSError, AttributeError):
                return
            with connection:
                ok = False
                try:
                    if (_receive(connection, 1, 128) == _focus_packet(self.record["nonce"])
                            and not self.stopped.is_set() and self._current()):
                        ok = self.focus() is True
                    connection.sendall(_ACCEPTED if ok else _REFUSED)
                except Exception:
                    try:
                        connection.sendall(_REFUSED)
                    except OSError:
                        pass

    def close(self):
        self.stopped.set()
        listener, self.listener = self.listener, None
        if listener is not None:
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            listener.close()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self.path is not None and self.identity is not None:
            try:
                record, identity = _read_loopback_endpoint(_root(self.root))
                if identity == self.identity and record == self.record:
                    self.path.unlink()
            except (OSError, ValueError):
                pass
        self.thread = self.identity = self.record = None


def create_owned_reopen_server(root, ownership_fd, focus):
    """Select transport only; kernel-lease admission is never optional."""
    server = _LoopbackFocusServer if _needs_loopback(root) else NativeReopenServer
    return server(root, ownership_fd, focus)


def _request_loopback_focus(root):
    record, identity = _read_loopback_endpoint(root)
    if not _active_lease(root):
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(6)
        connection.connect(("127.0.0.1", record["port"]))
        if _read_loopback_endpoint(root) != (record, identity) or not _active_lease(root):
            return False
        connection.sendall(_focus_packet(record["nonce"]))
        connection.shutdown(socket.SHUT_WR)
        accepted = _receive(connection, 6) == _ACCEPTED
    return (accepted and _root(root) == root
            and _read_loopback_endpoint(root) == (record, identity)
            and _active_lease(root))
