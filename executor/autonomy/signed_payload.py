"""Current signed bytes, distinct from retained unsigned input provenance.

Only a trusted caller's publisher policy enables reads. This module never
signs, repairs input RECORDs, imports candidate code or authorizes installation.
The producer is the existing build finalizer's nested-signing stage.
"""
from __future__ import annotations

import hashlib
import json
import os
from itertools import chain
from pathlib import Path
import plistlib
import stat
from xml.parsers.expat import ExpatError

from .publisher_policy import verify_publisher
from .release import CURRENT_PAYLOAD_NAME

RELATIVE_PATH = 'Contents/Resources/' + CURRENT_PAYLOAD_NAME
FORMAT = 'jae-current-signed-payload-v1'
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('signed_payload_duplicate')
        value[key] = item
    return value


def _signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _tree_fence(app):
    result = {}
    for path in chain((app,), app.rglob('*')):
        metadata = path.lstat()
        if (len(result) >= 100_000 or metadata.st_uid != os.geteuid()
                or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))
                or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1):
            raise ValueError('signed_payload_tree_invalid')
        result[path.relative_to(app).as_posix()] = _signature(metadata)
    return result


class _UniqueDict(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError('signed_payload_envelope_duplicate')
        super().__setitem__(key, value)


def _read(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('signed_payload_alias')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_size > MAX_MANIFEST_BYTES):
            raise ValueError('signed_payload_file_invalid')
        data = handle.read(MAX_MANIFEST_BYTES + 1)
        if (len(data) != before.st_size or len(data) > MAX_MANIFEST_BYTES
                or _signature(os.fstat(handle.fileno())) != _signature(before)
                or _signature(path.lstat()) != _signature(before)):
            raise ValueError('signed_payload_changed')
    return data


def app_for_runtime(root, release):
    root, release = Path(root).absolute(), Path(release).absolute()
    if (root.name != 'runtime' or release != root.parent / 'release'
            or root.parent.name != 'Resources' or root.parent.parent.name != 'Contents'):
        return None
    return root.parent.parent.parent


def has_current_payload(app):
    path = Path(app) / RELATIVE_PATH
    return path.exists() or path.is_symlink()


def current_payload(app, *, unsigned_receipt_sha256):
    """Pure build snapshot, never a trust result; caller owns signing admission."""
    from .release import runtime_manifest, source_manifest, verify_source_candidate
    app = Path(app).absolute()
    resources = app / 'Contents/Resources'
    release, runtime = resources / 'release', resources / 'runtime'
    native = resources / 'native-host'
    if not verify_source_candidate(release):
        raise ValueError('signed_payload_source_invalid')
    original_native = json.loads(_read(native / 'native-host-manifest.json'), object_pairs_hook=_pairs)
    if (type(original_native) is not dict or set(original_native) != {
            'format', 'source_sha256', 'executable_sha256'}
            or original_native.get('format') != 'jae-native-host-v1'):
        raise ValueError('signed_payload_native_provenance_invalid')
    executable = native / 'AIApplicationWindow'
    # All original native files remain present and unchanged by the producer.
    if set(p.name for p in native.iterdir()) != {'AIApplicationWindow', 'native-host-manifest.json'}:
        raise ValueError('signed_payload_native_invalid')
    native_bytes = _read(executable)
    if not executable.stat().st_mode & stat.S_IXUSR:
        raise ValueError('signed_payload_native_invalid')
    return {
        'format': FORMAT, 'unsigned_receipt_sha256': unsigned_receipt_sha256,
        'source_sha256': source_manifest(release)['source_sha256'],
        'runtime': runtime_manifest(runtime, release, include_manifest=True),
        'native': {'source_sha256': original_native['source_sha256'],
                   'executable_sha256': hashlib.sha256(native_bytes).hexdigest(),
                   'original_manifest_sha256': hashlib.sha256(_read(native / 'native-host-manifest.json')).hexdigest()},
        'input_record_role': 'ORIGINAL_UNSIGNED_PROVENANCE',
        'consumer_admission': 'NOT_ADMITTED', 'certification': 'NOT_CERTIFIED',
    }


def read_current_payload(app, *, required_publisher_policy=None):
    """Authenticate then rehash actual payload; no trust from an adjacent receipt."""
    app = Path(app).absolute()
    if any(item.is_symlink() for item in (app, *app.parents)):
        raise ValueError('signed_payload_alias')
    before = _tree_fence(app)
    verify_publisher(app, required_publisher_policy)
    encoded = _read(app / RELATIVE_PATH)
    saved = json.loads(encoded, object_pairs_hook=_pairs)
    if type(saved) is not dict:
        raise ValueError('signed_payload_invalid')
    pin = saved.get('unsigned_receipt_sha256')
    if (type(pin) is not str or len(pin) != 64
            or any(c not in '0123456789abcdef' for c in pin)
            or saved != current_payload(app, unsigned_receipt_sha256=pin)):
        raise ValueError('signed_payload_mismatch')
    # A current manifest must be an explicit ordinary resource in the verified
    # envelope. Unknown envelope representations refuse, never weaken coverage.
    envelope_path = app / 'Contents/_CodeSignature/CodeResources'
    envelope_bytes = _read(envelope_path)
    try:
        envelope = plistlib.loads(envelope_bytes, dict_type=_UniqueDict)
    except (plistlib.InvalidFileException, ExpatError, RecursionError, TypeError) as exc:
        raise ValueError('signed_payload_envelope_invalid') from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get('files2'), dict):
        raise ValueError('signed_payload_envelope_invalid')
    entry = envelope['files2'].get(RELATIVE_PATH.removeprefix('Contents/'))
    if (not isinstance(entry, dict) or set(entry) - {'hash', 'hash2'}
            or entry.get('hash2') != hashlib.sha256(encoded).digest()
            or 'hash' in entry and entry['hash'] != hashlib.sha1(encoded).digest()):
        raise ValueError('signed_payload_not_sealed')
    if (_read(app / RELATIVE_PATH) != encoded or _read(envelope_path) != envelope_bytes
            or _tree_fence(app) != before):
        raise ValueError('signed_payload_changed')
    return saved
