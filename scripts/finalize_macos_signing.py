"""Finalize a developer build identity after independently authorized signing.

Uses only Apple's static verifier; never signs or executes candidate code. The
result is NOT_ADMITTED. Use a trusted, quiescent local build workspace: static
verification plus before/after snapshots is not a hostile-writer isolation API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import time
from xml.parsers.expat import ExpatError

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import prepare_macos_signing as preparation

FINAL_IDENTITY = 'signed-build-identity-v2.json'
CURRENT_FINAL_IDENTITY = 'signed-build-identity-v3.json'
SIGNATURE_DIRECTORY = 'Contents/_CodeSignature'
# Current script-main envelope only; future formats must be explicitly reviewed.
SIGNATURE_FILES = frozenset(SIGNATURE_DIRECTORY + '/' + name for name in (
    'CodeDirectory', 'CodeRequirements', 'CodeResources', 'CodeSignature'))
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_MACHO_BYTES = 256 * 1024 * 1024


def _pin(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('signing_finalization_pin_required')
    return value


def _path(value):
    from executor.autonomy.app_distribution import _no_alias_path
    path = Path(value).expanduser().absolute()
    if '..' in path.parts:
        raise ValueError('signing_path_noncanonical')
    _no_alias_path(path)
    return path


def _read(path, limit=MAX_DOCUMENT_BYTES):
    """Bounded no-alias regular-file read, including external build evidence."""
    from executor.autonomy.bundle_copy import _signature
    path = _path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or not 0 < before.st_size <= limit):
            raise ValueError('signing_finalization_input_invalid')
        preparation._no_xattrs(fd)
        chunks, size = [], 0
        while data := os.read(fd, min(1024 * 1024, limit + 1 - size)):
            chunks.append(data)
            size += len(data)
            if size > limit:
                raise ValueError('signing_finalization_input_bound')
        preparation._no_xattrs(fd)
        _path(path)
        if (size != before.st_size or _signature(os.fstat(fd)) != _signature(before)
                or _signature(path.lstat()) != _signature(before)):
            raise ValueError('signing_finalization_input_changed')
        return b''.join(chunks)
    finally:
        os.close(fd)


def _tree_fence(app):
    from executor.autonomy.bundle_copy import _signature
    return {path.relative_to(app).as_posix(): _signature(path.lstat())
            for path in (app, *app.rglob('*'))}


def _verify_static(path, policy, identifier, deadline):
    """An external requirement is evaluated by Apple, never read from the app."""
    if sys.platform != 'darwin':
        raise ValueError('signing_static_verifier_requires_macos')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('signing_static_verifier_timeout')
    requirement = preparation._requirement(policy, identifier)
    try:
        result = subprocess.run([
            '/usr/bin/codesign', '--verify', '--strict', '--all-architectures',
            '--test-requirement', '=' + requirement, str(path),
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'},
            timeout=min(30, remaining), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('signing_static_verifier_unavailable') from exc
    if result.returncode != 0:
        raise ValueError('signing_static_requirement_refused')


class _UniqueDict(dict):
    def __setitem__(self, key, value):
        if key in self:
            raise ValueError('signing_resource_envelope_ambiguous')
        super().__setitem__(key, value)


def _bridge_coverage(envelope_bytes, bridge_bytes, *, resource_path=preparation.BRIDGE):
    """Only meaningful alongside unchanged-snapshot outer static verification.

    CodeDirectory authenticates CodeResources (special slot -3). Require the
    explicit ordinary v2 resource hash rather than infer coverage from rc=0.
    Apple's envelope format is an implementation detail: unknown forms refuse.
    """
    try:
        envelope = plistlib.loads(envelope_bytes, dict_type=_UniqueDict)
        entry = envelope['files2'][resource_path.removeprefix('Contents/')]
        if (not isinstance(entry, dict) or not {'hash2'} <= set(entry)
                or set(entry) - {'hash', 'hash2'}
                or type(entry['hash2']) is not bytes
                or entry['hash2'] != hashlib.sha256(bridge_bytes).digest()
                or ('hash' in entry and (type(entry['hash']) is not bytes
                    or entry['hash'] != hashlib.sha1(bridge_bytes).digest()))):
            raise ValueError('signing_bridge_not_sealed')
    except (KeyError, TypeError, plistlib.InvalidFileException, RecursionError, ExpatError) as exc:
        raise ValueError('signing_bridge_not_sealed') from exc
    return {'path': resource_path, 'resource_key': resource_path.removeprefix('Contents/'),
            'sha256': preparation._digest(bridge_bytes), 'envelope': SIGNATURE_DIRECTORY + '/CodeResources'}


def _validate_delta(source, app, original, current, bridge, *, nested_only=False, current_payload=False):
    from scripts.macos_signing_delta import verify_signature_only_change
    planned = {item['path']: item for item in bridge['signing_order']}
    expected = set(original) | {preparation.BRIDGE}
    if not nested_only:
        expected |= {SIGNATURE_DIRECTORY} | SIGNATURE_FILES
    if current_payload:
        from executor.autonomy.signed_payload import RELATIVE_PATH
        expected.add(RELATIVE_PATH)
    if set(current) != expected:
        raise ValueError('signing_output_layout_unsupported')
    for name in (() if nested_only else {SIGNATURE_DIRECTORY} | SIGNATURE_FILES):
        item = current[name]
        if (item['type'] != ('directory' if name == SIGNATURE_DIRECTORY else 'file')
                or item['mode'] != (0o755 if name == SIGNATURE_DIRECTORY else 0o644)):
            raise ValueError('signing_output_layout_unsupported')
    transitions = {}
    for name, before in original.items():
        after = current[name]
        if name not in planned:
            if before != after:
                raise ValueError('signing_original_payload_changed')
            continue
        if ({k: v for k, v in before.items() if k not in ('sha256', 'bytes')}
                != {k: v for k, v in after.items() if k not in ('sha256', 'bytes')}):
            raise ValueError('signing_original_payload_changed')
        original_bytes, signed_bytes = _read(source / name, MAX_MACHO_BYTES), _read(app / name, MAX_MACHO_BYTES)
        if (preparation._digest(original_bytes) != before['sha256']
                or preparation._digest(signed_bytes) != after['sha256']):
            raise ValueError('signing_finalization_input_changed')
        try:
            transitions[name] = verify_signature_only_change(original_bytes, signed_bytes)
        except ValueError as exc:
            raise ValueError(f'{exc}; signing_object={name!r}, '
                             f'completed_objects={len(transitions)}') from exc
    return transitions


def _process_signing_workspace(distribution, workspace, *, expected_receipt_sha256,
                               expected_prepared_identity_sha256, expected_policy_sha256,
                               required_publisher_policy, current_payload_phase=None):
    """Consume build→prepare output and emit an external build-only identity.

    All three pins and the policy come from the invoking trusted build, not from
    an arbitrary downloaded sidecar. No result authorizes consumer installation.
    """
    from executor.autonomy.app_distribution import (
        RECEIPT_NAME, _read_distribution_receipt, stage_macos_distribution)
    from executor.autonomy.signed_payload import RELATIVE_PATH, current_payload
    if current_payload_phase not in (None, 'prepare', 'finalize'):
        raise ValueError('signing_payload_phase_invalid')
    output_name = FINAL_IDENTITY if current_payload_phase is None else CURRENT_FINAL_IDENTITY
    policy = preparation._policy(required_publisher_policy)
    for value in (expected_receipt_sha256, expected_prepared_identity_sha256, expected_policy_sha256):
        _pin(value)
    if preparation._digest(preparation._encoded(policy)) != expected_policy_sha256:
        raise ValueError('signing_publisher_policy_mismatch')
    distribution, workspace = _path(distribution), _path(workspace)
    if (workspace.is_relative_to(distribution) or distribution.is_relative_to(workspace)
            or (workspace / output_name).exists()):
        raise ValueError('signing_finalization_workspace_invalid')
    receipt, receipt_bytes = _read_distribution_receipt(distribution / RECEIPT_NAME)
    if preparation._digest(receipt_bytes) != expected_receipt_sha256 or receipt.get('presentation') != 'native':
        raise ValueError('signing_input_identity_mismatch')
    saved_receipt = _read(workspace / preparation.ORIGINAL_RECEIPT)
    prepared_bytes = _read(workspace / preparation.IDENTITY)
    if (saved_receipt != receipt_bytes
            or preparation._digest(prepared_bytes) != expected_prepared_identity_sha256):
        raise ValueError('signing_prepared_identity_mismatch')
    app = _path(workspace / receipt['app_name'])
    # The output must be alongside the app; no caller-controlled in-app location.
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        roots = {p: (p.lstat().st_dev, p.lstat().st_ino) for p in (app, workspace, *workspace.parents)}
        def unchanged_roots():
            for path, identity in roots.items():
                _path(path)
                observed = path.lstat()
                if (observed.st_dev, observed.st_ino) != identity:
                    raise ValueError('signing_finalization_workspace_changed')
            held = os.fstat(workspace_fd)
            if (held.st_dev, held.st_ino) != roots[workspace]:
                raise ValueError('signing_finalization_workspace_changed')
        with stage_macos_distribution(distribution) as source:
            names = {'.'} | {p.relative_to(source).as_posix() for p in source.rglob('*')}
            original = preparation._capture(source, names)
            bridge = preparation._input_bridge(receipt, expected_receipt_sha256, original, policy)
            bridge_bytes = preparation._encoded(bridge)
            prepared_files = {**original, preparation.BRIDGE: {'type': 'file', 'mode': 0o644,
                'xattrs': {}, 'bytes': len(bridge_bytes), 'sha256': preparation._digest(bridge_bytes),
                'macho_header': False}}
            expected = preparation._prepared_identity(source.name, expected_receipt_sha256,
                bridge_bytes, prepared_files, policy)
            if prepared_bytes != preparation._encoded(expected):
                raise ValueError('signing_prepared_identity_mismatch')
            if _read(app / preparation.BRIDGE) != bridge_bytes:
                raise ValueError('signing_bridge_changed')
            allowed = names | {preparation.BRIDGE, SIGNATURE_DIRECTORY} | SIGNATURE_FILES
            if current_payload_phase == 'finalize':
                allowed.add(RELATIVE_PATH)
            observed_names = {'.'} | {p.relative_to(app).as_posix() for p in app.rglob('*')}
            if not observed_names <= allowed:
                raise ValueError('signing_output_layout_unsupported')
            unchanged_roots()
            fence = _tree_fence(app)
            current = preparation._capture(app, observed_names)
            if current.get(preparation.BRIDGE) != prepared_files[preparation.BRIDGE]:
                raise ValueError('signing_bridge_changed')
            deadline = time.monotonic() + 120
            # Verify outer first: the normal unsigned build must reach Apple's
            # real rejection, not an invented signature-presence shortcut.
            if current_payload_phase != 'prepare':
                _verify_static(app, policy, policy['bundle_id'], deadline)
            transitions = _validate_delta(source, app, original, current, bridge,
                nested_only=current_payload_phase == 'prepare', current_payload=current_payload_phase == 'finalize')
            coverage = (None if current_payload_phase == 'prepare' else
                _bridge_coverage(_read(app / (SIGNATURE_DIRECTORY + '/CodeResources')), bridge_bytes))
            for item in bridge['signing_order']:
                _verify_static(app / item['path'], policy, item['required_identifier'], deadline)
            def unchanged():
                unchanged_roots()
                if (preparation._capture(app, observed_names) != current or _tree_fence(app) != fence
                        or preparation._capture(source, names) != original
                        or _read(workspace / preparation.IDENTITY) != prepared_bytes
                        or _read(workspace / preparation.ORIGINAL_RECEIPT) != receipt_bytes
                        or _read_distribution_receipt(distribution / RECEIPT_NAME) != (receipt, receipt_bytes)):
                    raise ValueError('signing_finalization_input_changed')
            unchanged()
            if current_payload_phase is not None:
                payload = current_payload(app, unsigned_receipt_sha256=expected_receipt_sha256)
                payload_bytes = preparation._encoded(payload)
                if current_payload_phase == 'prepare':
                    # This explicit v3 path runs AFTER independently verified
                    # nested signing and BEFORE the outer seal. v2 is unchanged.
                    resources_fd = os.open(app / 'Contents/Resources', os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
                    try:
                        preparation._write_at(resources_fd, Path(RELATIVE_PATH).name, payload_bytes)
                    finally:
                        os.close(resources_fd)
                    expected_current = {**current, RELATIVE_PATH: {
                        'type': 'file', 'mode': 0o644, 'xattrs': {}, 'bytes': len(payload_bytes),
                        'sha256': preparation._digest(payload_bytes), 'macho_header': False}}
                    if (preparation._capture(app, set(expected_current)) != expected_current
                            or preparation._capture(source, names) != original
                            or _read(workspace / preparation.IDENTITY) != prepared_bytes
                            or _read(workspace / preparation.ORIGINAL_RECEIPT) != receipt_bytes
                            or _read_distribution_receipt(distribution / RECEIPT_NAME) != (receipt, receipt_bytes)
                            or _read(app / RELATIVE_PATH) != payload_bytes):
                        raise ValueError('signing_current_payload_changed')
                    unchanged_roots()
                    return {'format': 'jae-current-payload-preparation-v1',
                        'phase': 'NESTED_SIGNED_CURRENT_PAYLOAD',
                        'current_payload_sha256': preparation._digest(payload_bytes),
                        'outer_signature': 'REQUIRED_AFTER_THIS_STAGE',
                        'consumer_admission': 'NOT_ADMITTED', 'certification': 'NOT_CERTIFIED'}
                if _read(app / RELATIVE_PATH) != payload_bytes:
                    raise ValueError('signing_current_payload_changed')
                payload_coverage = _bridge_coverage(
                    _read(app / (SIGNATURE_DIRECTORY + '/CodeResources')), payload_bytes,
                    resource_path=RELATIVE_PATH)
            result = {
                'format': 'jae-build-bundle-identity-v2', 'phase': 'STATIC_SIGNED_BUILD_IDENTITY',
                'app_name': app.name, 'unsigned_receipt_sha256': expected_receipt_sha256,
                'prepared_identity_sha256': expected_prepared_identity_sha256,
                'required_publisher_policy': policy, 'publisher_policy_sha256': expected_policy_sha256,
                'bridge_coverage': coverage, 'files': current,
                'bundle_sha256': preparation._digest(preparation._encoded(current)),
                'signing_deltas': transitions, 'signing': 'Developer ID Application',
                'publisher_validation': 'VERIFIED_STATIC_EXTERNAL_REQUIREMENTS',
                'hardened_runtime_validation': 'NOT_PERFORMED', 'entitlements_validation': 'NOT_PERFORMED',
                'notarization': 'NOT_PERFORMED', 'gatekeeper_validation': 'NOT_PERFORMED',
                'online_revocation_validation': 'NOT_PERFORMED', 'release_authentication': 'NOT_PERFORMED',
                'consumer_admission': 'NOT_ADMITTED', 'certification': 'NOT_CERTIFIED',
            }
            if current_payload_phase == 'finalize':
                result.update(format='jae-build-bundle-identity-v3',
                    current_payload_coverage=payload_coverage)
            preparation._write_at(workspace_fd, output_name, preparation._encoded(result))
            # A failed final recheck can leave retained, mismatching evidence.
            # An external identity is always point-in-time and must be rehashed.
            unchanged()
            if _read(workspace / output_name) != preparation._encoded(result):
                raise ValueError('signing_finalization_input_changed')
            return result
    finally:
        os.close(workspace_fd)


def finalize_signing_workspace(distribution, workspace, *, expected_receipt_sha256,
                               expected_prepared_identity_sha256, expected_policy_sha256,
                               required_publisher_policy):
    """Original v2 contract: the additional current-payload resource is refused."""
    return _process_signing_workspace(distribution, workspace,
        expected_receipt_sha256=expected_receipt_sha256,
        expected_prepared_identity_sha256=expected_prepared_identity_sha256,
        expected_policy_sha256=expected_policy_sha256,
        required_publisher_policy=required_publisher_policy)


def prepare_current_signed_payload(distribution, workspace, **kwargs):
    return _process_signing_workspace(distribution, workspace, current_payload_phase='prepare', **kwargs)


def finalize_current_signed_payload(distribution, workspace, **kwargs):
    return _process_signing_workspace(distribution, workspace, current_payload_phase='finalize', **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distribution', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--expected-receipt-sha256', required=True)
    parser.add_argument('--expected-prepared-identity-sha256', required=True)
    parser.add_argument('--expected-policy-sha256', required=True)
    parser.add_argument('--publisher-team-id', required=True)
    parser.add_argument('--publisher-bundle-id', required=True)
    parser.add_argument('--current-payload-stage', choices=('prepare', 'finalize'))
    args = parser.parse_args(argv)
    entry = {'prepare': prepare_current_signed_payload, 'finalize': finalize_current_signed_payload}.get(
        args.current_payload_stage, finalize_signing_workspace)
    result = entry(args.distribution, args.workspace,
        expected_receipt_sha256=args.expected_receipt_sha256,
        expected_prepared_identity_sha256=args.expected_prepared_identity_sha256,
        expected_policy_sha256=args.expected_policy_sha256,
        required_publisher_policy=preparation.publisher_policy(args.publisher_team_id, args.publisher_bundle_id))
    print(json.dumps({key: value for key, value in result.items() if key not in ('files', 'signing_deltas')}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
