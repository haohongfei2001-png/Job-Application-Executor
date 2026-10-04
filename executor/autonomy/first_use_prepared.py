"""Private first-use prepared payloads; no old-state import or repair.

Only a complete receipt binds the three exclusively created initial objects to
one existing pending installation and its root/lock identities. Unknown entries
and incomplete preparation are preserved and refused, never cleaned or adopted.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat


RECEIPT = '.first-use-prepared.json'
LOCKS = ('native-window.lock', 'worker.lock', 'migration.lock')
ROLES = ('tasks.sqlite3', 'task-answers.key', 'auth.token')
LIMITS = {'tasks.sqlite3': 8 * 1024 * 1024, 'task-answers.key': 44, 'auth.token': 43}
FORMAT = 'jae-first-use-prepared-v1'


def _tag(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('first_use_prepared_invalid')
        result[key] = value
    return result


def _entry(info, *, directory=False):
    if ((not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not directory and info.st_nlink != 1):
        raise ValueError('first_use_prepared_authority_changed')
    return [info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid]


def _context(value):
    if (type(value) is not dict or set(value) != {'bundle_tag', 'fence_tag', 'root_tag'}
            or not all(_digest(item) for item in value.values())):
        raise ValueError('first_use_prepared_context_invalid')
    return value


@contextmanager
def _root(root, context):
    root = Path(root).absolute()
    _context(context)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('first_use_prepared_authority_changed')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        entry = _entry(os.fstat(fd), directory=True)
        def check():
            if (_entry(os.fstat(fd), directory=True) != entry
                    or any(p.is_symlink() for p in (root, *root.parents))
                    or _entry(root.stat(follow_symlinks=False), directory=True) != entry):
                raise ValueError('first_use_prepared_authority_changed')
            locks = [_entry(os.stat(name, dir_fd=fd, follow_symlinks=False)) for name in LOCKS]
            if _tag([entry, *locks]) != context['root_tag']:
                raise ValueError('first_use_prepared_authority_changed')
        check()
        yield fd, check
        check()
    finally:
        os.close(fd)


def _read(fd, name, maximum):
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(source)
        entry = _entry(before)
        if before.st_size > maximum:
            raise ValueError('first_use_prepared_bound')
        chunks, remaining = [], maximum + 1
        while remaining:
            chunk = os.read(source, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk); remaining -= len(chunk)
        data = b''.join(chunks)
        after = os.fstat(source)
        if (len(data) != before.st_size or len(data) > maximum
                or _entry(after) != entry
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                   (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or _entry(os.stat(name, dir_fd=fd, follow_symlinks=False)) != entry):
            raise ValueError('first_use_prepared_changed')
        return data, entry
    finally:
        os.close(source)


@dataclass(frozen=True)
class Prepared:
    tag: str
    record: dict = field(repr=False)
    payloads: dict = field(repr=False)


def _inspect(fd, context):
    names = set(os.listdir(fd))
    if names == set(LOCKS):
        return None
    if RECEIPT not in names:
        raise ValueError('first_use_preparation_incomplete')
    raw, receipt_entry = _read(fd, RECEIPT, 4096)
    record = json.loads(raw, object_pairs_hook=_unique)
    if (type(record) is not dict or set(record) != {'format', 'context', 'files', 'receipt_entry'}
            or record['format'] != FORMAT or record['context'] != context
            or record['receipt_entry'] != receipt_entry
            or type(record['files']) is not dict or set(record['files']) != set(ROLES)):
        raise ValueError('first_use_prepared_invalid')
    payloads = {}
    expected = set(LOCKS) | {RECEIPT} | set(ROLES)
    if names != expected:
        raise ValueError('first_use_prepared_unknown_entry')
    for final in ROLES:
        item = record['files'][final]
        if (type(item) is not dict or set(item) != {'entry', 'bytes', 'sha256'}
                or type(item['entry']) is not list or len(item['entry']) != 4
                or any(type(n) is not int or n < 0 for n in item['entry'])
                or type(item['bytes']) is not int or not 0 < item['bytes'] <= LIMITS[final]
                or not _digest(item['sha256'])):
            raise ValueError('first_use_prepared_invalid')
        data, entry = _read(fd, final, LIMITS[final])
        if (entry != item['entry'] or len(data) != item['bytes']
                or hashlib.sha256(data).hexdigest() != item['sha256']):
            raise ValueError('first_use_prepared_changed')
        payloads[final] = data
    if names != expected or set(os.listdir(fd)) != expected:
        raise ValueError('first_use_prepared_unknown_entry')
    return Prepared(_tag([receipt_entry, hashlib.sha256(raw).hexdigest()]), record, payloads)


def inspect(root, context):
    with _root(root, context) as (fd, check):
        result = _inspect(fd, context)
        check()
        return result


def admission_tag(context, prepared):
    return context['root_tag'] if prepared is None else _tag([context['root_tag'], prepared.tag])


def prepare(root, context, payloads):
    if (type(payloads) is not dict or set(payloads) != set(ROLES)
            or any(type(data) is not bytes or not 0 < len(data) <= LIMITS[name]
                   for name, data in payloads.items())):
        raise ValueError('first_use_prepared_payload_invalid')
    with _root(root, context) as (fd, check):
        if _inspect(fd, context) is not None:
            raise ValueError('first_use_prepared_already_present')
        created, handles = {}, []
        expected = set(LOCKS)
        try:
            for final in ROLES:
                check()
                if set(os.listdir(fd)) != expected:
                    raise ValueError('first_use_prepared_unknown_entry')
                output = os.open(final, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=fd)
                handles.append(output)
                entry = _entry(os.fstat(output)); data = payloads[final]
                if os.write(output, data) != len(data):
                    raise OSError('first_use_prepared_write_incomplete')
                os.fsync(output)
                observed, visible = _read(fd, final, LIMITS[final])
                if observed != data or visible != entry or _entry(os.fstat(output)) != entry:
                    raise ValueError('first_use_prepared_changed')
                created[final] = {'entry': entry, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                expected.add(final)
            check()
            if set(os.listdir(fd)) != expected:
                raise ValueError('first_use_prepared_unknown_entry')
            output = os.open(RECEIPT, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=fd)
            handles.append(output); entry = _entry(os.fstat(output))
            record = {'format': FORMAT, 'context': dict(context), 'files': created, 'receipt_entry': entry}
            raw = json.dumps(record, sort_keys=True, separators=(',', ':')).encode()
            if len(raw) > 4096:
                raise ValueError('first_use_prepared_bound')
            if os.write(output, raw) != len(raw):
                raise OSError('first_use_prepared_write_incomplete')
            os.fsync(output); os.fsync(fd)
            observed, visible = _read(fd, RECEIPT, 4096)
            if observed != raw or visible != entry or _entry(os.fstat(output)) != entry:
                raise ValueError('first_use_prepared_changed')
            check()
            return _inspect(fd, context)
        finally:
            # Keep every partial object on failure; never erase unknown state.
            for handle in reversed(handles):
                os.close(handle)

