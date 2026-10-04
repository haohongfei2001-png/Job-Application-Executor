"""Prepare a build-only signing workspace from the existing unsigned artifact.

No signing, signature acceptance, candidate execution, installer or publication.
The original manifests and wheel metadata keep their original bytes. This is an
input to a future trusted signing stage, never a signed-output trust decision.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from itertools import chain
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata

BRIDGE = 'Contents/Resources/runtime/signing-input-bridge.json'
IDENTITY = 'prepared-bundle-identity-v2.json'
ORIGINAL_RECEIPT = 'unsigned-distribution-manifest.json'
MACHO_MAGIC = frozenset((b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe',
    b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe',
    b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'))


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode('utf-8')


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def publisher_policy(team_id, bundle_id):
    """Validate an explicit future requirement, without authenticating anyone.

    Values come from the build operator, never the candidate's claimed signer.
    No default team, development/ad-hoc fallback or arbitrary requirement input.
    """
    from executor.autonomy.consumer import BUNDLE_ID
    if (type(team_id) is not str or re.fullmatch(r'[A-Z0-9]{10}', team_id) is None
            or type(bundle_id) is not str or bundle_id != BUNDLE_ID):
        raise ValueError('signing_publisher_policy_required')
    return {'format': 'jae-required-publisher-policy-v1', 'team_id': team_id,
            'bundle_id': bundle_id, 'certificate_kind': 'Developer ID Application'}


def _policy(value):
    if type(value) is not dict:
        raise ValueError('signing_publisher_policy_required')
    checked = publisher_policy(value.get('team_id'), value.get('bundle_id'))
    if value != checked:
        raise ValueError('signing_publisher_policy_required')
    return checked


def _requirement(policy, identifier):
    # This is an independently requested requirement, not an observed Authority
    # string or a designated requirement read from an incoming signature.
    return ('anchor apple generic and '
            'certificate 1[field.1.2.840.113635.100.6.2.6] exists and '
            'certificate leaf[field.1.2.840.113635.100.6.1.13] exists and '
            f'certificate leaf[subject.OU] = "{policy["team_id"]}" and '
            f'identifier = "{identifier}"')


def _no_xattrs(fd):
    """This unsigned preparation supports no xattrs; never silently drop one."""
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        call = library.flistxattr
        call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        call.restype = ctypes.c_ssize_t
        size = call(fd, None, 0, 0)
        if size < 0:
            raise OSError(ctypes.get_errno(), 'signing_metadata_unavailable')
        if size:
            raise ValueError('signing_metadata_unsupported')
    elif sys.platform == 'linux':
        if os.listxattr(fd):
            raise ValueError('signing_metadata_unsupported')
    else:
        raise ValueError('signing_metadata_platform_unsupported')


def _capture(app, names):
    """Exact bounded file bytes/types/modes, anchored to existing copy helpers.

    Only the admitted paths (plus our one bridge) are eligible. No candidate code
    is loaded, and aliases are refused before reading through them.
    """
    from executor.autonomy.app_distribution import (
        MAX_DISTRIBUTION_EXPANDED_BYTES, MAX_DISTRIBUTION_MEMBERS, _no_alias_path)
    from executor.autonomy.bundle_copy import _open_relative, _signature
    _no_alias_path(app)
    names = set(names)
    if len(names) > MAX_DISTRIBUTION_MEMBERS:
        raise ValueError('signing_inventory_bound')
    def tree_entries():
        entries, normalized = {}, set()
        for path in chain((app,), app.rglob('*')):
            name = path.relative_to(app).as_posix()
            if name not in names:
                raise ValueError('signing_inventory_changed')
            key = unicodedata.normalize('NFC', name).casefold()
            if key in normalized:
                raise ValueError('signing_inventory_collision')
            normalized.add(key)
            current = path.lstat()
            if (not (stat.S_ISREG(current.st_mode) or stat.S_ISDIR(current.st_mode))
                    or current.st_uid != os.geteuid()
                    or (stat.S_ISREG(current.st_mode) and current.st_nlink != 1)):
                raise ValueError('signing_inventory_member_invalid')
            entries[name] = _signature(current)
        if set(entries) != names:
            raise ValueError('signing_inventory_changed')
        return entries
    entries = tree_entries()
    root = os.open(app, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        if _signature(os.fstat(root)) != entries['.']:
            raise ValueError('signing_inventory_changed')
        result, total = {}, 0
        for name in sorted(entries):
            fd = os.dup(root) if name == '.' else _open_relative(root, name, entries)
            try:
                before = os.fstat(fd)
                _no_xattrs(fd)
                item = {'mode': stat.S_IMODE(before.st_mode), 'xattrs': {},
                        'type': 'file' if stat.S_ISREG(before.st_mode) else 'directory'}
                if item['type'] == 'file':
                    digest, size, header = hashlib.sha256(), 0, b''
                    while data := os.read(fd, 1024 * 1024):
                        if not header:
                            header = data[:4]
                        size += len(data)
                        total += len(data)
                        if total > MAX_DISTRIBUTION_EXPANDED_BYTES:
                            raise ValueError('signing_inventory_bound')
                        digest.update(data)
                    if size != before.st_size:
                        raise ValueError('signing_inventory_changed')
                    item.update(bytes=size, sha256=digest.hexdigest(),
                                macho_header=header in MACHO_MAGIC)
                _no_xattrs(fd)
                if _signature(os.fstat(fd)) != entries[name]:
                    raise ValueError('signing_inventory_changed')
                result[name] = item
            finally:
                os.close(fd)
        # Recheck the complete visible tree, not only streamed files.
        observed = tree_entries()
        _no_alias_path(app)
        if observed != entries:
            raise ValueError('signing_inventory_changed')
        return result
    finally:
        os.close(root)


def _write_at(directory_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o644, dir_fd=directory_fd)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError('signing_write_incomplete')
            view = view[count:]
        os.fchmod(fd, 0o644)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)


def _input_bridge(receipt, expected_receipt_sha256, before, policy):
    """Deterministic original-input statement, also reconstructed by finalization."""
    planned = sorted((name for name, item in before.items() if item.get('macho_header')),
                     key=lambda name: (-len(Path(name).parts), name))
    return {
        'format': 'jae-signing-input-bridge-v1',
        'phase': 'UNSIGNED_SIGNING_PREPARATION',
        'unsigned_receipt_sha256': expected_receipt_sha256,
        'unsigned_distribution': receipt, 'unsigned_inventory': before,
        'unsigned_inventory_sha256': _digest(_encoded(before)),
        'required_publisher_policy': policy, 'publisher_validation': 'NOT_PERFORMED',
        'signing_order': [{
            'path': name, 'input_sha256': before[name]['sha256'],
            'classification': 'MACHO_HEADER_CANDIDATE',
            'required_identifier': policy['bundle_id'] + '.component.' + _digest(name.encode()),
            'required_requirement': _requirement(policy, policy['bundle_id'] + '.component.' + _digest(name.encode())),
        } for name in planned],
        'outer_seal': {'path': '.', 'required_identifier': policy['bundle_id'],
            'required_requirement': _requirement(policy, policy['bundle_id']),
            'must_cover_bridge': BRIDGE, 'phase': 'AFTER_NESTED_SIGNING'},
        'consumer_admission': 'DISALLOWED', 'notarization': 'NOT_PERFORMED',
    }


def _prepared_identity(app_name, expected_receipt_sha256, bridge_bytes, prepared, policy):
    return {
        'format': 'jae-build-bundle-identity-v2',
        'phase': 'UNSIGNED_SIGNING_PREPARATION', 'app_name': app_name,
        'unsigned_receipt_sha256': expected_receipt_sha256,
        'bridge_path': BRIDGE, 'bridge_sha256': _digest(bridge_bytes),
        'required_publisher_policy': policy,
        'files': prepared, 'bundle_sha256': _digest(_encoded(prepared)),
        'signing': 'unsigned', 'publisher_validation': 'NOT_PERFORMED',
        'signed_output_identity': None, 'notarization': 'NOT_PERFORMED',
        'consumer_admission': 'DISALLOWED', 'certification': 'NOT_CERTIFIED',
    }


def prepare_signing_workspace(distribution, output, *, expected_receipt_sha256,
                              required_publisher_policy):
    """Produce a fresh unsigned preparation; identity receipt is committed last.

    The expected digest belongs to the invoking build's known input. Reading an
    arbitrary adjacent receipt and passing its hash is not publisher trust.
    Refusals preserve partial output; nothing is overwritten, installed or run.
    """
    from executor.autonomy.app_distribution import (
        RECEIPT_NAME, _bundle_members, _no_alias_path, _read_distribution_receipt,
        stage_macos_distribution)
    from executor.autonomy.bundle_copy import _inventory, _open_relative, copy_bundle_payload
    from executor.autonomy.consumer import _bundle_transaction_identity
    policy = _policy(required_publisher_policy)
    if (type(expected_receipt_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', expected_receipt_sha256) is None):
        raise ValueError('signing_input_identity_required')
    distribution, output = Path(distribution).expanduser().absolute(), Path(output).expanduser().absolute()
    for path in (distribution, output):
        if '..' in path.parts:
            raise ValueError('signing_path_noncanonical')
        _no_alias_path(path)
    if (output.exists() or not output.parent.is_dir()
            or output.is_relative_to(distribution) or distribution.is_relative_to(output)):
        raise ValueError('signing_output_unavailable')
    receipt, receipt_bytes = _read_distribution_receipt(distribution / RECEIPT_NAME)
    if _digest(receipt_bytes) != expected_receipt_sha256:
        raise ValueError('signing_input_identity_mismatch')
    if receipt.get('presentation') != 'native':
        raise ValueError('signing_native_distribution_required')
    with stage_macos_distribution(distribution) as source:
        if _read_distribution_receipt(distribution / RECEIPT_NAME) != (receipt, receipt_bytes):
            raise ValueError('signing_input_changed')
        names = {p.relative_to(source).as_posix() for p in _bundle_members(source)}
        before = _capture(source, names)
        identity = _bundle_transaction_identity(source)
        if identity is None:
            raise ValueError('signing_input_changed')
        planned = sorted((name for name, item in before.items() if item.get('macho_header')),
                         key=lambda name: (-len(Path(name).parts), name))
        if not {'Contents/Resources/runtime/bin/python',
                'Contents/Resources/native-host/AIApplicationWindow'} <= set(planned):
            raise ValueError('signing_macho_inventory_incomplete')
        # No directory is created until policy, input and metadata admission pass.
        output.mkdir(mode=0o700)
        created = output.lstat()
        workspace = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        try:
            def unchanged_workspace():
                _no_alias_path(output)
                for current in (os.fstat(workspace), output.lstat()):
                    if (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):
                        raise ValueError('signing_workspace_changed')
            unchanged_workspace()
            os.mkdir(source.name, mode=0o700, dir_fd=workspace)
            app = output / source.name
            target_entry = os.stat(source.name, dir_fd=workspace, follow_symlinks=False)
            target_identity = (target_entry.st_dev, target_entry.st_ino)
            copy_bundle_payload(source, app, identity, expected_target_identity=target_identity)
            def unchanged_app():
                unchanged_workspace()
                current = os.stat(source.name, dir_fd=workspace, follow_symlinks=False)
                if ((current.st_dev, current.st_ino) != target_identity
                        or not stat.S_ISDIR(current.st_mode)):
                    raise ValueError('signing_app_changed')
            unchanged_app()
            if _capture(app, names) != before or _capture(source, names) != before:
                raise ValueError('signing_copy_changed')
            bridge = _input_bridge(receipt, expected_receipt_sha256, before, policy)
            bridge_bytes = _encoded(bridge)
            # The extra runtime entry deliberately invalidates the untouched v1
            # runtime manifest, so direct installers refuse before execution.
            # Reuse the descriptor-anchored path walker for the one added file.
            entries = _inventory(app)
            unchanged_app()
            root = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY,
                           dir_fd=workspace)
            try:
                current = os.fstat(root)
                if (current.st_dev, current.st_ino) != target_identity:
                    raise ValueError('signing_app_changed')
                resources = _open_relative(root, str(Path(BRIDGE).parent), entries)
                try:
                    _write_at(resources, Path(BRIDGE).name, bridge_bytes)
                finally:
                    os.close(resources)
            finally:
                os.close(root)
            unchanged_app()
            prepared = _capture(app, names | {BRIDGE})
            if ({key: value for key, value in prepared.items() if key != BRIDGE} != before
                    or prepared[BRIDGE]['sha256'] != _digest(bridge_bytes)):
                raise ValueError('signing_bridge_changed')
            result = _prepared_identity(source.name, expected_receipt_sha256, bridge_bytes, prepared, policy)
            unchanged_app()
            _write_at(workspace, ORIGINAL_RECEIPT, receipt_bytes)
            if (_capture(app, names | {BRIDGE}) != prepared
                    or _read_distribution_receipt(distribution / RECEIPT_NAME) != (receipt, receipt_bytes)):
                raise ValueError('signing_preparation_changed')
            unchanged_app()
            _write_at(workspace, IDENTITY, _encoded(result))
            unchanged_app()
            if _capture(app, names | {BRIDGE}) != prepared:
                # Retain the mismatching receipt/evidence. It is never trusted
                # without rehashing, and this invocation reports no completion.
                raise ValueError('signing_preparation_changed')
            unchanged_app()
            return result
        finally:
            os.close(workspace)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distribution', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-receipt-sha256', required=True)
    parser.add_argument('--publisher-team-id', required=True)
    parser.add_argument('--publisher-bundle-id', required=True)
    args = parser.parse_args(argv)
    result = prepare_signing_workspace(args.distribution, args.output,
        expected_receipt_sha256=args.expected_receipt_sha256,
        required_publisher_policy=publisher_policy(args.publisher_team_id, args.publisher_bundle_id))
    # Avoid dumping a large complete inventory to operator logs.
    print(json.dumps({key: value for key, value in result.items() if key != 'files'}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
