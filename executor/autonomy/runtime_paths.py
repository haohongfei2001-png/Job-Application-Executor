"""Dependency-free owned runtime paths and the existing local authentication token.

Recovery must not import the task schema, worker, browser or model provider.
These helpers retain the existing path, mode, token and fail-closed contracts.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .release import is_packaged_source

# A packaged release is immutable. Keep task state outside the app bundle so
# replacing or rolling back source/runtime never replaces the applicant journal.
def default_runtime(source_root: Path, *, home: Path | None = None) -> Path:
    source_root = Path(source_root)
    if is_packaged_source(source_root):
        return (home or Path.home()) / "Library" / "Application Support" / "AI投递经理" / "autonomy"
    return source_root / "runtime" / "autonomy"


_SOURCE_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = default_runtime(_SOURCE_ROOT)


def _runtime_path(path):
    path = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("private runtime unavailable")
    return path


def _same_entry(path, metadata):
    current = path.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise ValueError("private runtime unavailable")


def _runtime_directory_fd(path):
    path = _runtime_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise ValueError("private runtime unavailable")
        _runtime_path(path)
        _same_entry(path, metadata)
        return fd
    except BaseException:
        os.close(fd)
        raise


def runtime_root(path):
    """Admit lexical authority before reads or effects; never resolve aliases."""
    path = _runtime_path(path)
    try:
        fd = _runtime_directory_fd(path)
    except FileNotFoundError:
        return path  # An ordinary missing root may be created by startup.
    os.close(fd)
    return path


def private_dir(path):
    path = runtime_root(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = _runtime_directory_fd(path)
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    return path


def _private_regular_fd(path, flags, *, require_private=False):
    """Validate the owned ordinary inode before payload reads/writes or chmod."""
    _runtime_path(path)
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or (require_private and metadata.st_mode & 0o077)):
            raise ValueError("private runtime unavailable")
        _runtime_path(path)
        _same_entry(path, metadata)
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_service_log(root):
    """Append only to the admitted log descriptor, never chmod a path alias."""
    path = private_dir(root) / "service.log"
    fd = _private_regular_fd(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "ab")
    except BaseException:
        os.close(fd)
        raise


def local_token(root):
    root = runtime_root(root)
    token = os.getenv("APPLICATION_EXECUTOR_LOCAL_TOKEN")
    if token:
        if len(token) < 32:
            raise ValueError("local auth token must have at least 32 characters")
        return token
    path = private_dir(root) / "auth.token"
    try:
        try:
            fd = _private_regular_fd(path, os.O_RDONLY, require_private=True)
        except FileNotFoundError:
            try:
                created = _private_regular_fd(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, require_private=True)
                with os.fdopen(created, "w", encoding="utf-8") as handle:
                    handle.write(secrets.token_urlsafe(32))
            except FileExistsError:
                pass
            fd = _private_regular_fd(path, os.O_RDONLY, require_private=True)
    except (OSError, ValueError):
        raise ValueError("private token permissions required") from None
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        raw = handle.read(4097)
        _runtime_path(path)
        _same_entry(path, metadata)
    if len(raw) > 4096:
        raise ValueError("invalid private auth token")
    token = raw.strip()
    if not 32 <= len(token) <= 4096:
        raise ValueError("invalid private auth token")
    return token
