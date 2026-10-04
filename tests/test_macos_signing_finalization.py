"""Synthetic refusal/structural contracts, never an Apple signing success claim."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import struct
import time
from types import SimpleNamespace

import pytest

from test_macos_signing_preparation import artifact, prepare, POLICY
from scripts import finalize_macos_signing as finalization
from scripts import prepare_macos_signing as preparation
from executor.autonomy import app_distribution as distribution
from executor.autonomy.consumer import BUNDLE_ID


@pytest.fixture
def prepared(artifact, tmp_path):
    workspace = tmp_path / 'workspace'
    identity = prepare(artifact, workspace)
    kwargs = {
        'expected_receipt_sha256': identity['unsigned_receipt_sha256'],
        'expected_prepared_identity_sha256': preparation._digest((workspace / preparation.IDENTITY).read_bytes()),
        'expected_policy_sha256': preparation._digest(preparation._encoded(POLICY)),
        'required_publisher_policy': POLICY,
    }
    return artifact[2], workspace, identity, kwargs


def finish(prepared, **changes):
    dist, workspace, _identity, kwargs = prepared
    return finalization.finalize_signing_workspace(dist, workspace, **{**kwargs, **changes})


def no_output(prepared):
    assert not (prepared[1] / finalization.FINAL_IDENTITY).exists()


@pytest.mark.parametrize('pin', ['expected_receipt_sha256', 'expected_prepared_identity_sha256', 'expected_policy_sha256'])
@pytest.mark.parametrize('value', [None, '', 'x' * 64, 'F' * 64, '0' * 64])
def test_all_external_pins_are_required_and_bound(prepared, monkeypatch, pin, value):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must refuse before Apple'))
    with pytest.raises(ValueError):
        finish(prepared, **{pin: value})
    no_output(prepared)


def test_policy_cannot_be_taken_from_claimed_signer_or_bridge(prepared, monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must refuse before Apple'))
    for policy in (None, {}, {**POLICY, 'team_id': 'ATTACKER01'},
                   {**POLICY, 'certificate_kind': 'Apple Development'}, {**POLICY, 'Authority': 'Developer ID'}):
        with pytest.raises(ValueError):
            finish(prepared, required_publisher_policy=policy)
    no_output(prepared)


@pytest.mark.parametrize('damage', ['phase', 'extra_field', 'inventory', 'receipt', 'bridge', 'bridge_mode'])
def test_mixed_phases_and_rehashed_forged_preparation_are_refused(prepared, monkeypatch, damage):
    _, workspace, identity, kwargs = prepared
    app = workspace / identity['app_name']
    if damage in ('phase', 'extra_field', 'inventory'):
        forged = dict(identity)
        if damage == 'phase':
            forged['phase'] = 'STATIC_SIGNED_BUILD_IDENTITY'
        elif damage == 'extra_field':
            forged['publisher_validation'] = 'VERIFIED'
        else:
            forged['files'] = {}
        raw = preparation._encoded(forged)
        (workspace / preparation.IDENTITY).write_bytes(raw)
        # Even a caller pinning malformed bytes cannot evade the original-build reconstruction.
        kwargs['expected_prepared_identity_sha256'] = preparation._digest(raw)
    elif damage == 'receipt':
        (workspace / preparation.ORIGINAL_RECEIPT).write_bytes(b'{}\n')
    elif damage == 'bridge':
        (app / preparation.BRIDGE).write_bytes(b'{}\n')
    else:
        (app / preparation.BRIDGE).chmod(0o755)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must refuse before Apple'))
    with pytest.raises(ValueError):
        finish(prepared)
    no_output(prepared)


@pytest.mark.parametrize('damage', ['alias', 'hardlink', 'unplanned', 'xattr', 'workspace_alias'])
def test_finalization_rejects_aliases_extra_paths_and_metadata(prepared, tmp_path, monkeypatch, damage):
    _, workspace, identity, _ = prepared
    app = workspace / identity['app_name']
    bridge = app / preparation.BRIDGE
    if damage in ('alias', 'hardlink'):
        private = tmp_path / 'private'; private.write_bytes(bridge.read_bytes())
        bridge.unlink()
        bridge.symlink_to(private) if damage == 'alias' else os.link(private, bridge)
    elif damage == 'unplanned':
        (app / 'Contents/Resources/unplanned').write_bytes(b'not signed content')
    elif damage == 'xattr':
        if not hasattr(os, 'setxattr'):
            pytest.skip('Linux attribute injection fixture')
        os.setxattr(bridge, 'user.synthetic', b'')
    else:
        actual = workspace.with_name('actual'); workspace.rename(actual); workspace.symlink_to(actual)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must refuse before Apple'))
    with pytest.raises((ValueError, OSError)):
        finish(prepared)
    no_output(prepared)


def test_real_nonapple_host_refuses_without_spawning_or_output(prepared, monkeypatch):
    monkeypatch.setattr(finalization.sys, 'platform', 'linux')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('Apple tool unavailable'))
    with pytest.raises(ValueError, match='signing_static_verifier_requires_macos'):
        finish(prepared)
    no_output(prepared)


@pytest.mark.parametrize('failure', ['nonzero', 'timeout', 'missing'])
def test_static_verifier_failures_are_closed_with_fixed_external_requirements(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(finalization.sys, 'platform', 'darwin')
    path = tmp_path / 'candidate.app'
    def command(argv, **kwargs):
        assert argv == ['/usr/bin/codesign', '--verify', '--strict', '--all-architectures',
            '--test-requirement', '=' + preparation._requirement(POLICY, BUNDLE_ID), str(path)]
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
        assert 0 < kwargs['timeout'] <= 30
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, 30)
        if failure == 'missing':
            raise FileNotFoundError('fixed system tool missing')
        return SimpleNamespace(returncode=1, stderr='TeamIdentifier=SYNTHETIC1\nAuthority=Developer ID')
    monkeypatch.setattr(subprocess, 'run', command)
    with pytest.raises(ValueError, match='signing_static_'):
        finalization._verify_static(path, POLICY, BUNDLE_ID, time.monotonic() + 120)


def test_static_deadline_refuses_before_spawn(monkeypatch, tmp_path):
    monkeypatch.setattr(finalization.sys, 'platform', 'darwin')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('budget exhausted'))
    with pytest.raises(ValueError, match='signing_static_verifier_timeout'):
        finalization._verify_static(tmp_path / 'app', POLICY, BUNDLE_ID, 0)


def test_os_return_zero_alone_cannot_finalize_unsigned_bundle(prepared, monkeypatch):
    # Deliberate incomplete proof seam, not a simulated successful publisher.
    monkeypatch.setattr(finalization, '_verify_static', lambda *a: None)
    with pytest.raises(ValueError, match='signing_output_layout_unsupported'):
        finish(prepared)
    no_output(prepared)


def envelope(bridge, entry=None):
    return plistlib.dumps({'files2': {preparation.BRIDGE.removeprefix('Contents/'):
        {'hash2': hashlib.sha256(bridge).digest()} if entry is None else entry}})


@pytest.mark.parametrize('entry', [{}, {'hash2': b'x' * 32}, {'hash2': 'not bytes'},
    {'hash': b'x' * 20}, {'hash2': 123}, {'hash2': b'x' * 32, 'optional': True}])
def test_missing_or_wrong_bridge_coverage_refused(entry):
    with pytest.raises(ValueError, match='signing_bridge_not_sealed'):
        finalization._bridge_coverage(envelope(b'bridge', entry), b'bridge')


@pytest.mark.parametrize('field', ['optional', 'symlink', 'cdhash', 'requirement', 'future_unknown'])
def test_even_matching_hash_cannot_claim_ambiguous_bridge_coverage(field):
    bridge = b'original bridge'
    entry = {'hash2': hashlib.sha256(bridge).digest(), field: True}
    with pytest.raises(ValueError, match='signing_bridge_not_sealed'):
        finalization._bridge_coverage(envelope(bridge, entry), bridge)


def test_explicit_bridge_digest_contract_is_only_structural_not_publisher_evidence():
    bridge = b'original bridge'
    result = finalization._bridge_coverage(envelope(bridge), bridge)
    assert result['sha256'] == hashlib.sha256(bridge).hexdigest()
    assert 'publisher_validation' not in result
    dual = {'hash2': hashlib.sha256(bridge).digest(), 'hash': hashlib.sha1(bridge).digest()}
    assert finalization._bridge_coverage(envelope(bridge, dual), bridge) == result


@pytest.mark.parametrize('raw', [b'bad plist', plistlib.dumps({'files': {}}),
    b'<?xml version="1.0"?><plist version="1.0"><dict><key>files2</key><dict/>'
    b'<key>files2</key><dict/></dict></plist>'])
def test_malformed_v1_or_duplicate_envelope_refused(raw):
    with pytest.raises(ValueError):
        finalization._bridge_coverage(raw, b'bridge')


def test_external_input_bounds_and_existing_output_are_closed(prepared, monkeypatch):
    _, workspace, _, _ = prepared
    with pytest.raises(ValueError, match='signing_finalization_input_invalid'):
        finalization._read(workspace / preparation.IDENTITY, 1)
    destination = workspace / finalization.FINAL_IDENTITY
    destination.write_bytes(b'prior retained evidence')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must refuse before Apple'))
    with pytest.raises(ValueError, match='signing_finalization_workspace_invalid'):
        finish(prepared)
    assert destination.read_bytes() == b'prior retained evidence'


def test_build_preparation_report_feeds_actual_finalizer_cli(artifact, tmp_path, monkeypatch, capsys):
    from scripts import build_macos_app
    source = artifact[2]
    receipt = json.loads((source / distribution.RECEIPT_NAME).read_text())
    def build(repo, **kwargs):
        shutil.copytree(source, kwargs['output_dir'])
        return receipt
    monkeypatch.setattr(distribution, 'build_macos_distribution', build)
    output, workspace = tmp_path / 'built', tmp_path / 'workspace'
    assert build_macos_app.main(['--standalone-runtime', str(tmp_path / 'unused'),
        '--output', str(output), '--signing-workspace', str(workspace),
        '--publisher-team-id', POLICY['team_id'], '--publisher-bundle-id', BUNDLE_ID]) == 0
    pins = json.loads(capsys.readouterr().out)['signing_preparation']['finalization_inputs']
    assert pins.pop('entry') == 'scripts/finalize_macos_signing.py'
    monkeypatch.setattr(finalization.sys, 'platform', 'linux')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no Apple on Linux'))
    args = ['--distribution', str(output), '--workspace', str(workspace),
            '--publisher-team-id', POLICY['team_id'], '--publisher-bundle-id', BUNDLE_ID]
    for key, value in pins.items():
        args.extend(['--' + key.replace('_', '-'), value])
    with pytest.raises(ValueError, match='signing_static_verifier_requires_macos'):
        finalization.main(args)
    assert not (workspace / finalization.FINAL_IDENTITY).exists()


@pytest.fixture
def synthetic_transition(request, monkeypatch):
    """Inert test bytes and a replaced verification boundary, never OS proof.

    This pytest-only workspace cannot pass real codesign. It exists solely to
    cover consumer denial and mutation/commit contracts beyond that boundary.
    """
    import test_macos_signing_preparation as fixtures
    from test_macos_signing_delta import synthetic_macho
    monkeypatch.setattr(fixtures, 'MACHO', synthetic_macho())
    prepared = request.getfixturevalue('prepared')
    _, workspace, identity, _ = prepared
    app = workspace / identity['app_name']
    bridge = json.loads((app / preparation.BRIDGE).read_text())
    for item in bridge['signing_order']:
        (app / item['path']).write_bytes(synthetic_macho(payload=b'SYNTHETIC NEW', linkedit_vm_size=16384))
    directory = app / finalization.SIGNATURE_DIRECTORY
    directory.mkdir(mode=0o755)
    for name in finalization.SIGNATURE_FILES:
        (app / name).write_bytes(b'SYNTHETIC NON-SIGNATURE TEST DATA')
        (app / name).chmod(0o644)
    (directory / 'CodeResources').write_bytes(envelope((app / preparation.BRIDGE).read_bytes()))
    calls = []
    def inert_boundary(path, policy, identifier, deadline):
        assert policy == POLICY and deadline > time.monotonic()
        calls.append((path, identifier))
    monkeypatch.setattr(finalization, '_verify_static', inert_boundary)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no real OS result in synthetic seam'))
    return prepared, calls, bridge


def test_synthetic_postverifier_contract_still_never_admits_consumer(synthetic_transition):
    prepared, calls, bridge = synthetic_transition
    _, workspace, identity, _ = prepared
    app = workspace / identity['app_name']
    original_prepared = (workspace / preparation.IDENTITY).read_bytes()
    result = finish(prepared)
    assert calls == [(app, BUNDLE_ID)] + [(app / item['path'], item['required_identifier'])
                                         for item in bridge['signing_order']]
    assert result['consumer_admission'] == 'NOT_ADMITTED'
    assert result['certification'] == 'NOT_CERTIFIED'
    for name in ('hardened_runtime_validation', 'entitlements_validation', 'notarization',
                 'gatekeeper_validation', 'online_revocation_validation', 'release_authentication'):
        assert result[name] == 'NOT_PERFORMED'
    assert (workspace / preparation.IDENTITY).read_bytes() == original_prepared
    assert result['files'] == preparation._capture(app, result['files'])
    assert finalization.FINAL_IDENTITY not in result['files']
    assert (workspace / finalization.FINAL_IDENTITY).read_bytes() == preparation._encoded(result)
    from executor.autonomy.consumer import _trusted_bundle
    assert not _trusted_bundle(app)


@pytest.mark.parametrize('damage', ['record', 'native_code', 'mode', 'extra_signature_file', 'missing_signature_file'])
def test_synthetic_seam_never_allows_unplanned_payload_changes(synthetic_transition, damage):
    prepared, _, bridge = synthetic_transition
    app = prepared[1] / prepared[2]['app_name']
    if damage == 'record':
        path = next(app.rglob('RECORD')); path.write_bytes(path.read_bytes() + b'forged')
    elif damage == 'native_code':
        path = app / bridge['signing_order'][0]['path']
        data = bytearray(path.read_bytes()); data[0x400] ^= 1; path.write_bytes(data)
    elif damage == 'mode':
        (app / bridge['signing_order'][0]['path']).chmod(0o777)
    elif damage == 'extra_signature_file':
        (app / finalization.SIGNATURE_DIRECTORY / 'Unreviewed').write_bytes(b'extra')
    else:
        (app / next(iter(finalization.SIGNATURE_FILES))).unlink()
    with pytest.raises(ValueError):
        finish(prepared)
    no_output(prepared)


@pytest.mark.parametrize('damage', ['native', 'bridge', 'envelope', 'prepared', 'receipt', 'app_swap', 'workspace_swap'])
def test_mutation_during_static_boundary_never_commits(synthetic_transition, monkeypatch, damage):
    prepared, _, bridge = synthetic_transition
    _, workspace, identity, _ = prepared
    app = workspace / identity['app_name']
    original = finalization._verify_static
    count = []
    def mutate(*args):
        original(*args)
        count.append(True)
        if len(count) != 2:
            return
        if damage in ('native', 'bridge', 'envelope'):
            relative = {'native': bridge['signing_order'][0]['path'], 'bridge': preparation.BRIDGE,
                        'envelope': finalization.SIGNATURE_DIRECTORY + '/CodeResources'}[damage]
            path = app / relative; path.write_bytes(path.read_bytes() + b'changed')
        elif damage in ('prepared', 'receipt'):
            path = workspace / (preparation.IDENTITY if damage == 'prepared' else preparation.ORIGINAL_RECEIPT)
            path.write_bytes(path.read_bytes() + b'\n')
        else:
            path = app if damage == 'app_swap' else workspace
            moved = path.with_name(path.name + '-retained'); path.rename(moved); shutil.copytree(moved, path)
    monkeypatch.setattr(finalization, '_verify_static', mutate)
    with pytest.raises(ValueError, match='signing_finalization_'):
        finish(prepared)
    no_output(prepared)


def test_postcommit_mutation_returns_error_and_retains_untrusted_evidence(synthetic_transition, monkeypatch):
    prepared, _, bridge = synthetic_transition
    app = prepared[1] / prepared[2]['app_name']
    write = preparation._write_at
    def mutate(fd, name, data):
        write(fd, name, data)
        if name == finalization.FINAL_IDENTITY:
            path = app / bridge['signing_order'][0]['path']
            path.write_bytes(path.read_bytes() + b'changed')
    monkeypatch.setattr(preparation, '_write_at', mutate)
    with pytest.raises(ValueError, match='signing_finalization_input_changed'):
        finish(prepared)
    stale = json.loads((prepared[1] / finalization.FINAL_IDENTITY).read_text())
    assert stale['consumer_admission'] == 'NOT_ADMITTED'
    assert stale['files'][bridge['signing_order'][0]['path']]['sha256'] != preparation._digest(
        (app / bridge['signing_order'][0]['path']).read_bytes())


def test_refused_delta_names_object_and_structural_location_only(synthetic_transition):
    prepared, _, bridge = synthetic_transition
    name = bridge['signing_order'][0]['path']
    path = prepared[1] / prepared[2]['app_name'] / name
    from test_macos_signing_delta import SIGNATURE_OFFSET
    raw = bytearray(path.read_bytes())
    # Keep the nonzero byte INSIDE the declared SuperBlob, not allocation slack.
    struct.pack_into('>I', raw, SIGNATURE_OFFSET + 4, len(raw) - SIGNATURE_OFFSET)
    raw[-1] = 1; path.write_bytes(raw)
    with pytest.raises(ValueError, match='macho_delta_signature_unframed_bytes') as caught:
        finish(prepared)
    message = str(caught.value)
    assert f'signing_object={name!r}' in message and 'completed_objects=' in message
    assert 'signature_offset=' in message and 'signature_datasize=' in message
    assert 'region=superblob_internal_tail' in message
    assert 'SYNTHETIC NEW' not in message
    no_output(prepared)
