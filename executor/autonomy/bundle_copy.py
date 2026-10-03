"""Copy an admitted unsigned bundle without reconstructing its payload.

This is a local installer staging primitive, not publisher authentication. The
existing distribution inventory remains authoritative; signed envelopes are
not silently admitted by this engineering path.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat


def _signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _inventory(app):
    from .app_distribution import _bundle_members
    members = _bundle_members(app)
    return {p.relative_to(app).as_posix(): _signature(p.lstat()) for p in members}


def _open_relative(root_fd, relative, entries):
    """Anchor every directory component, never traverse a visible alias."""
    parts = Path(relative).parts
    parent = os.dup(root_fd)
    try:
        for index, name in enumerate(parts):
            key = Path(*parts[:index + 1]).as_posix()
            directory = index < len(parts) - 1 or stat.S_ISDIR(entries[key][2])
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            child = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=parent)
            try:
                if (_signature(os.fstat(child)) != entries[key]
                        or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != entries[key]):
                    raise ValueError('installer_bundle_changed')
            except BaseException:
                os.close(child)
                raise
            os.close(parent)
            parent = child
        result, parent = parent, None
        return result
    finally:
        if parent is not None:
            os.close(parent)


def copy_bundle_payload(source: Path, target: Path, expected_identity: tuple) -> None:
    """Populate one empty owned staging root; preserve all bytes and modes.

On refusal retain partial staging evidence. Caller alone decides whether any
later cleanup is safe. No source data, target app or private state is removed.
"""
    from .consumer import _bundle_transaction_identity
    source, target = Path(source).absolute(), Path(target).absolute()
    if (any(p.is_symlink() for p in (source, *source.parents, target, *target.parents))
            or _bundle_transaction_identity(source) != expected_identity):
        raise ValueError('installer_bundle_changed')
    entries = _inventory(source)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    target_fd = None
    try:
        if _signature(os.fstat(source_fd)) != entries['.']:
            raise ValueError('installer_bundle_changed')
        target_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        target_entry = os.fstat(target_fd)
        if (target_entry.st_uid != os.geteuid() or os.listdir(target_fd)
                or (target_entry.st_dev, target_entry.st_ino) !=
                   (target.lstat().st_dev, target.lstat().st_ino)):
            raise ValueError('installer_stage_unverified')
        destination_dirs = {'.': (target_entry.st_dev, target_entry.st_ino)}
        for relative, saved in entries.items():
            if relative == '.':
                continue
            path = Path(relative)
            source_file = _open_relative(source_fd, relative, entries)
            destination_parent = os.dup(target_fd)
            try:
                for index, part in enumerate(path.parts[:-1]):
                    child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY,
                                    dir_fd=destination_parent)
                    os.close(destination_parent)
                    destination_parent = child
                    current = os.fstat(child)
                    if (current.st_dev, current.st_ino) != destination_dirs[Path(*path.parts[:index + 1]).as_posix()]:
                        raise ValueError('installer_stage_changed')
                mode = stat.S_IMODE(saved[2])
                if stat.S_ISDIR(saved[2]):
                    os.mkdir(path.name, mode=mode, dir_fd=destination_parent)
                    child = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY,
                                    dir_fd=destination_parent)
                    try:
                        current = os.fstat(child)
                        if current.st_uid != os.geteuid() or os.listdir(child):
                            raise ValueError('installer_stage_changed')
                        os.fchmod(child, mode)
                        destination_dirs[relative] = (current.st_dev, current.st_ino)
                    finally:
                        os.close(child)
                else:
                    output = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     mode, dir_fd=destination_parent)
                    try:
                        while data := os.read(source_file, 1024 * 1024):
                            view = memoryview(data)
                            while view:
                                count = os.write(output, view)
                                if count <= 0:
                                    raise OSError('installer_copy_incomplete')
                                view = view[count:]
                        os.fchmod(output, mode)
                        os.fsync(output)
                    finally:
                        os.close(output)
                if _signature(os.fstat(source_file)) != saved:
                    raise ValueError('installer_bundle_changed')
            finally:
                os.close(source_file)
                os.close(destination_parent)
        os.fchmod(target_fd, stat.S_IMODE(entries['.'][2]))
        os.fsync(target_fd)
        if (_inventory(source) != entries
                or _bundle_transaction_identity(source) != expected_identity
                or (target.lstat().st_dev, target.lstat().st_ino) != destination_dirs['.']):
            raise ValueError('installer_bundle_changed')
        copied = _inventory(target)
        if set(copied) != set(entries):
            raise ValueError('installer_copy_incomplete')
        for relative, expected in entries.items():
            actual = copied[relative]
            if stat.S_IMODE(actual[2]) != stat.S_IMODE(expected[2]):
                raise ValueError('installer_copy_incomplete')
            if stat.S_ISREG(expected[2]):
                original = _open_relative(source_fd, relative, entries)
                staged = _open_relative(target_fd, relative, copied)
                try:
                    def digest(fd):
                        value = hashlib.sha256()
                        while data := os.read(fd, 1024 * 1024):
                            value.update(data)
                        return value.digest()
                    if digest(original) != digest(staged):
                        raise ValueError('installer_copy_incomplete')
                finally:
                    os.close(original)
                    os.close(staged)
        if (_inventory(source) != entries or _inventory(target) != copied
                or _bundle_transaction_identity(source) != expected_identity
                or _bundle_transaction_identity(target) is None):
            raise ValueError('installer_bundle_changed')
    finally:
        os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)
