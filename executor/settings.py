from __future__ import annotations

import json
import hashlib
import fcntl
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from .autonomy import runtime_paths
from .autonomy.release import is_packaged_source


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path.home() / "Job-Application-Executor"
PACKAGED = is_packaged_source(_SOURCE_ROOT)


def default_settings_path(source_root: Path, *, home: Path | None = None) -> Path:
    """One persistent authority outside an immutable app; no legacy fallback."""
    if is_packaged_source(source_root):
        return runtime_paths.default_runtime(source_root, home=home) / "settings.json"
    return (home or Path.home()) / "Job-Application-Executor" / "config" / "settings.json"


PATH = default_settings_path(_SOURCE_ROOT)
_LIMIT = 1024 * 1024


def _defaults():
    return {"resume_path": None, "profile_path": None, "auth_wait_seconds": 900}


@contextmanager
def _parent(path: Path, *, create: bool):
    # Walk through directory descriptors so an ancestor link/swap cannot redirect
    # a read, temporary write or atomic replacement into another authority.
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError("settings_unavailable")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if PACKAGED and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("settings_unavailable")
        yield descriptor
    finally:
        os.close(descriptor)


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_size > _LIMIT
            or (PACKAGED and stat.S_IMODE(info.st_mode) & 0o077)):
        raise ValueError("settings_unavailable")


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("settings_unavailable")
        value[key] = item
    return value


def _constant(_value):
    raise ValueError("settings_unavailable")


def _read_at(parent):
    try:
        descriptor = os.open(PATH.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
    except FileNotFoundError:
        return _defaults()
    with os.fdopen(descriptor, "rb") as handle:
        _regular(os.fstat(handle.fileno()))
        data = handle.read(_LIMIT + 1)
        if len(data) > _LIMIT:
            raise ValueError("settings_unavailable")
    result = json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                        parse_constant=_constant)
    if not isinstance(result, dict):
        raise ValueError("settings_unavailable")
    return result


def settings_version(data):
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_settings():
    try:
        with _parent(PATH, create=False) as parent:
            return _read_at(parent)
    except FileNotFoundError:
        return _defaults()
    except (OSError, UnicodeError, ValueError, TypeError):
        # Never expose JSON fragments, applicant values or local paths.
        raise ValueError("settings_unavailable") from None


def _encoded(data):
    if not isinstance(data, dict):
        raise ValueError("settings_unavailable")
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    json.loads(encoded.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    if len(encoded) > _LIMIT:
        raise ValueError("settings_unavailable")
    return encoded


def _write_at(parent, encoded):
    temporary = None
    try:
        try:
            existing = os.stat(PATH.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _regular(existing)
        temporary = ".settings-" + secrets.token_hex(16) + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, PATH.name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary = None
        os.fsync(parent)
    finally:
        if temporary is not None:
            os.unlink(temporary, dir_fd=parent)


def save_settings(data):
    try:
        encoded = _encoded(data)  # Validate before creating a directory.
        with _parent(PATH, create=True) as parent:
            # The directory itself is the lock: no second settings/lock authority.
            fcntl.flock(parent, fcntl.LOCK_EX)
            _write_at(parent, encoded)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise ValueError("settings_unavailable") from None


class SettingsConflict(RuntimeError):
    pass


def change_settings(expected_version, change):
    """Serialize a compare-and-publish change through the same pinned authority."""
    if (not isinstance(expected_version, str) or len(expected_version) != 64
            or any(c not in "0123456789abcdef" for c in expected_version)):
        raise ValueError("settings_unavailable")
    try:
        with _parent(PATH, create=True) as parent:
            fcntl.flock(parent, fcntl.LOCK_EX)
            current = _read_at(parent)
            if settings_version(current) != expected_version:
                raise SettingsConflict("settings_changed")
            updated = change(parent, current)
            _write_at(parent, _encoded(updated))
            return updated
    except SettingsConflict:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        raise ValueError("settings_unavailable") from None
