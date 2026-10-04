"""Synthetic preparation contracts; no Apple signing or runtime success claim."""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess

import pytest

from executor.autonomy import app_distribution as distribution
from executor.autonomy import consumer, release
from scripts import prepare_macos_signing as preparation

POLICY = preparation.publisher_policy('SYNTHETIC1', consumer.BUNDLE_ID)
MACHO = struct.pack('<8I', 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0) + b'NOT EXECUTABLE: SYNTHETIC'


def synthetic_bundle(app, repo):
    """Static v1 fixture accepted by real stager; payload must never execute."""
    resources = app / 'Contents/Resources'
    source = resources / 'release'
    release.copy_source_candidate(repo, source)
    runtime = resources / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin/python').write_bytes(MACHO)
    (runtime / 'bin/python').chmod(0o755)
    package = runtime / 'lib/python3.12/site-packages'
    native = package / 'synthetic/native.so'
    native.parent.mkdir(parents=True)
    native.write_bytes(MACHO)
    meta = package / 'synthetic-1.0.dist-info'
    meta.mkdir()
    (meta / 'METADATA').write_text('Metadata-Version: 2.1\nName: synthetic\nVersion: 1.0\n')
    (meta / 'WHEEL').write_text('Wheel-Version: 1.0\nGenerator: synthetic-test\n')
    record = []
    for path in (native, meta / 'METADATA', meta / 'WHEEL'):
        content = path.read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
        record.append(f'{path.relative_to(package)},sha256={digest},{len(content)}')
    (meta / 'RECORD').write_text('\n'.join(record) + '\nsynthetic-1.0.dist-info/RECORD,,\n')
    runtime_identity = release.runtime_manifest(runtime, source)
    (runtime / release.RUNTIME_MANIFEST_NAME).write_text(json.dumps(runtime_identity))
    host = resources / 'native-host'
    host.mkdir()
    (host / 'AIApplicationWindow').write_bytes(MACHO)
    (host / 'AIApplicationWindow').chmod(0o755)
    (host / 'native-host-manifest.json').write_text(json.dumps({
        'format': 'jae-native-host-v1',
        'source_sha256': hashlib.sha256(b'SYNTHETIC HOST SOURCE').hexdigest(),
        'executable_sha256': hashlib.sha256(MACHO).hexdigest()}))
    macos = app / 'Contents/MacOS'
    macos.mkdir()
    (macos / 'AIApplicationManager').write_text(consumer._native_packaged_launcher())
    (macos / 'AIApplicationManager').chmod(0o755)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': consumer.BUNDLE_ID, 'CFBundleExecutable': 'AIApplicationManager',
        'CFBundleVersion': '6', 'LSMinimumSystemVersion': '13.0'}))
    assert consumer._trusted_bundle(app)
    return runtime_identity


@pytest.fixture
def artifact(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'executor/autonomy').mkdir(parents=True)
    (repo / 'executor/__init__.py').write_text('')
    (repo / 'executor/autonomy/macos_host.py').write_text("HOST_SOURCE = 'SYNTHETIC HOST SOURCE'\n")
    (repo / 'requirements.txt').write_text('synthetic==1.0\n')
    app = tmp_path / 'input' / (consumer.APP_NAME + '.app')
    runtime = synthetic_bundle(app, repo)
    output = tmp_path / 'distribution'
    output.mkdir()
    distribution._archive_app(app, output / distribution.ARCHIVE_NAME)
    receipt = {'format': 'jae-macos-distribution-v1', 'archive': distribution.ARCHIVE_NAME,
        'archive_sha256': distribution._sha256(output / distribution.ARCHIVE_NAME),
        'app_name': app.name, 'source_sha256': release.source_manifest(repo)['source_sha256'],
        'runtime_sha256': runtime['runtime_sha256'], 'requirements_sha256': runtime['requirements_sha256'],
        'signing': 'unsigned', 'certification': 'NOT_CERTIFIED', 'final_click_actor': 'user',
        'task_state': 'excluded', 'build_host_metadata': 'excluded', 'presentation': 'native'}
    (output / distribution.RECEIPT_NAME).write_bytes(preparation._encoded(receipt))
    return repo, app, output


def prepare(artifact, output, **kwargs):
    _repo, _app, source = artifact
    return preparation.prepare_signing_workspace(source, output,
        expected_receipt_sha256=kwargs.pop('expected_receipt_sha256',
            distribution._sha256(source / distribution.RECEIPT_NAME)),
        required_publisher_policy=kwargs.pop('required_publisher_policy', POLICY), **kwargs)


def test_real_stager_copy_and_bridge_keep_original_manifest_and_wheel_bytes(artifact, tmp_path, monkeypatch):
    _, original, source = artifact
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('candidate/tool execution forbidden'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('candidate/tool execution forbidden'))
    original_files = {p.relative_to(original).as_posix(): p.read_bytes()
                      for p in original.rglob('*') if p.is_file()}
    result = prepare(artifact, tmp_path / 'workspace')
    app = tmp_path / 'workspace' / original.name
    assert result['signed_output_identity'] is None
    assert result['consumer_admission'] == 'DISALLOWED'
    assert result['publisher_validation'] == result['notarization'] == 'NOT_PERFORMED'
    assert result['phase'] == 'UNSIGNED_SIGNING_PREPARATION'
    assert json.loads((app.parent / preparation.IDENTITY).read_text()) == result
    assert (app.parent / preparation.ORIGINAL_RECEIPT).read_bytes() == (source / distribution.RECEIPT_NAME).read_bytes()
    for path, data in original_files.items():
        assert (app / path).read_bytes() == data
        assert (original / path).read_bytes() == data
    bridge = json.loads((app / preparation.BRIDGE).read_text())
    assert bridge['outer_seal']['must_cover_bridge'] == preparation.BRIDGE
    assert bridge['outer_seal']['phase'] == 'AFTER_NESTED_SIGNING'
    assert preparation.BRIDGE not in bridge['unsigned_inventory']
    assert preparation.IDENTITY not in result['files']
    assert result['bridge_sha256'] == distribution._sha256(app / preparation.BRIDGE)
    assert result['bundle_sha256'] == preparation._digest(preparation._encoded(result['files']))
    plan = bridge['signing_order']
    assert len(plan) == 3
    assert plan[0]['path'].endswith('synthetic/native.so')
    assert all('anchor apple generic' in item['required_requirement'] for item in plan)
    assert all('SYNTHETIC1' in item['required_requirement'] for item in plan)
    runtime = app / 'Contents/Resources/runtime'
    wheel = next(importlib.metadata.distributions(path=[str(runtime / 'lib/python3.12/site-packages')]))
    assert release._distribution_payload_owned(wheel, runtime)
    assert consumer._trusted_bundle(app) is False
    assert consumer._bundle_transaction_identity(app) is None
    monkeypatch.setattr(consumer, 'verify_standalone_runtime',
                        lambda *a: pytest.fail('prepared runtime must not execute'))
    assert consumer.install_macos_bundle(app, platform='darwin',
        destination=tmp_path / 'Applications', task_state_root=tmp_path / 'state')['ok'] is False
    assert not (tmp_path / 'Applications').exists() and not (tmp_path / 'state').exists()
    # Even re-archiving with a freshly updated archive hash cannot hide the
    # deliberate mismatch with the untouched original runtime manifest.
    repacked = tmp_path / 'repacked'; repacked.mkdir()
    distribution._archive_app(app, repacked / distribution.ARCHIVE_NAME)
    forged = json.loads((source / distribution.RECEIPT_NAME).read_text())
    forged['archive_sha256'] = distribution._sha256(repacked / distribution.ARCHIVE_NAME)
    (repacked / distribution.RECEIPT_NAME).write_text(json.dumps(forged))
    with pytest.raises(ValueError, match='distribution_intake_refused:payload'):
        with distribution.stage_macos_distribution(repacked):
            pytest.fail('preparation must not become a consumer distribution')



@pytest.mark.parametrize('policy', [None, {}, {'team_id': 'SYNTHETIC1'},
    {**POLICY, 'team_id': ''}, {**POLICY, 'team_id': 'SYNTHETIC1" or true'},
    {**POLICY, 'bundle_id': 'attacker.other'}, {**POLICY, 'Authority': 'Developer ID'},
    {**POLICY, 'certificate_kind': 'Apple Development'}])
def test_explicit_independent_policy_is_required_before_any_staging(artifact, tmp_path, monkeypatch, policy):
    monkeypatch.setattr(distribution, 'stage_macos_distribution', lambda *a: pytest.fail('must refuse first'))
    with pytest.raises(ValueError, match='signing_publisher_policy_required'):
        prepare(artifact, tmp_path / 'workspace', required_publisher_policy=policy)
    assert not (tmp_path / 'workspace').exists()


@pytest.mark.parametrize('digest', [None, '', 'f' * 64, 'F' * 64])
def test_caller_pinned_input_is_mandatory(artifact, tmp_path, digest):
    with pytest.raises(ValueError, match='signing_input_identity_'):
        prepare(artifact, tmp_path / 'workspace', expected_receipt_sha256=digest)
    assert not (tmp_path / 'workspace').exists()


def test_output_must_be_fresh_nonaliased_and_outside_input(artifact, tmp_path):
    existing = tmp_path / 'workspace'
    existing.mkdir()
    (existing / 'keep').write_text('existing evidence')
    alias = tmp_path / 'alias'
    alias.symlink_to(existing, target_is_directory=True)
    for target in (existing, alias, artifact[2] / 'nested'):
        with pytest.raises(ValueError):
            prepare(artifact, target)
    assert (existing / 'keep').read_text() == 'existing evidence'


@pytest.mark.parametrize('damage', ['record', 'new_file', 'alias', 'hardlink', 'mode', 'case_collision', 'xattr'])
def test_preparation_refuses_copy_drift_without_committing_identity(artifact, tmp_path, monkeypatch, damage):
    from executor.autonomy import bundle_copy
    copy = bundle_copy.copy_bundle_payload
    def damaged(source, target, identity, **kwargs):
        copy(source, target, identity, **kwargs)
        record = target / 'Contents/Resources/runtime/lib/python3.12/site-packages/synthetic-1.0.dist-info/RECORD'
        if damage == 'record':
            record.write_text('changed provenance')
        elif damage == 'new_file':
            (target / 'Contents/Resources/unplanned').write_text('new')
        elif damage == 'alias':
            record.unlink(); record.symlink_to(tmp_path / 'private')
        elif damage == 'hardlink':
            record.unlink(); os.link(tmp_path / 'private', record)
        elif damage == 'mode':
            record.chmod(0o755)
        elif damage == 'case_collision':
            (record.parent / 'record').write_text('collision')
        else:
            if not hasattr(os, 'setxattr'):
                pytest.skip('Linux attribute injection contract')
            os.setxattr(record, 'user.synthetic', b'')
    (tmp_path / 'private').write_text('PRIVATE_CANARY')
    monkeypatch.setattr(bundle_copy, 'copy_bundle_payload', damaged)
    with pytest.raises((ValueError, OSError)):
        prepare(artifact, tmp_path / 'workspace')
    assert not (tmp_path / 'workspace' / preparation.IDENTITY).exists()
    assert (tmp_path / 'private').read_text() == 'PRIVATE_CANARY'


def test_workspace_replacement_never_writes_into_replacement(artifact, tmp_path, monkeypatch):
    from executor.autonomy import bundle_copy
    copy = bundle_copy.copy_bundle_payload
    destination = tmp_path / 'workspace'
    def replaced(source, target, identity, **kwargs):
        copy(source, target, identity, **kwargs)
        destination.rename(tmp_path / 'preserved')
        destination.mkdir()
        (destination / 'keep').write_text('unrelated')
    monkeypatch.setattr(bundle_copy, 'copy_bundle_payload', replaced)
    with pytest.raises((ValueError, OSError)):
        prepare(artifact, destination)
    assert {p.name for p in destination.iterdir()} == {'keep'}
    assert (tmp_path / 'preserved').is_dir()


def test_prepare_cli_requires_policy_and_emits_only_small_unsigned_summary(artifact, tmp_path, capsys):
    source = artifact[2]
    assert preparation.main(['--distribution', str(source), '--output', str(tmp_path / 'workspace'),
        '--expected-receipt-sha256', distribution._sha256(source / distribution.RECEIPT_NAME),
        '--publisher-team-id', 'SYNTHETIC1', '--publisher-bundle-id', consumer.BUNDLE_ID]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['signing'] == 'unsigned' and result['consumer_admission'] == 'DISALLOWED'
    assert 'files' not in result


def test_existing_build_entry_wires_its_actual_receipt_into_real_preparation(artifact, tmp_path, monkeypatch, capsys):
    from scripts import build_macos_app
    source = artifact[2]
    receipt = json.loads((source / distribution.RECEIPT_NAME).read_text())
    build_calls = []
    def synthetic_build(repo, **kwargs):
        build_calls.append(kwargs)
        shutil.copytree(source, kwargs['output_dir'])
        return receipt
    # Only the expensive producer is a synthetic boundary. The CLI, its receipt
    # binding, real strict stager, exact copy and preparation all execute.
    monkeypatch.setattr(distribution, 'build_macos_distribution', synthetic_build)
    assert build_macos_app.main(['--standalone-runtime', str(tmp_path / 'unused'),
        '--output', str(tmp_path / 'built'), '--signing-workspace', str(tmp_path / 'workspace'),
        '--publisher-team-id', 'SYNTHETIC1', '--publisher-bundle-id', consumer.BUNDLE_ID]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(build_calls) == 1
    assert result['signing_preparation']['consumer_admission'] == 'DISALLOWED'
    assert json.loads((tmp_path / 'built' / distribution.RECEIPT_NAME).read_text()) == receipt


def test_build_entry_refuses_missing_policy_before_build(artifact, tmp_path, monkeypatch):
    from scripts import build_macos_app
    monkeypatch.setattr(distribution, 'build_macos_distribution', lambda *a, **k: pytest.fail('policy before build'))
    with pytest.raises(ValueError, match='signing_publisher_policy_required'):
        build_macos_app.main(['--standalone-runtime', str(tmp_path / 'unused'),
            '--output', str(tmp_path / 'built'), '--signing-workspace', str(tmp_path / 'workspace')])


def test_dotdot_cannot_bypass_input_output_separation(artifact, tmp_path):
    (tmp_path / 'other').mkdir()
    hidden_overlap = tmp_path / 'other/../distribution/workspace'
    with pytest.raises(ValueError, match='signing_path_noncanonical'):
        prepare(artifact, hidden_overlap)
    assert not (artifact[2] / 'workspace').exists()


@pytest.mark.parametrize('point', ['before_copy', 'after_copy'])
def test_same_named_replacement_app_never_receives_payload_or_bridge(artifact, tmp_path, monkeypatch, point):
    from executor.autonomy import bundle_copy
    copy = bundle_copy.copy_bundle_payload
    destination = tmp_path / 'workspace'
    def replaced(source, target, identity, **kwargs):
        if point == 'after_copy':
            copy(source, target, identity, **kwargs)
        destination.rename(tmp_path / 'preserved')
        destination.mkdir()
        if point == 'after_copy':
            shutil.copytree(tmp_path / 'preserved' / source.name, destination / source.name)
        else:
            (destination / source.name).mkdir()
        if point == 'before_copy':
            return copy(source, target, identity, **kwargs)
    monkeypatch.setattr(bundle_copy, 'copy_bundle_payload', replaced)
    with pytest.raises(ValueError, match='installer_stage_unverified|signing_workspace_changed'):
        prepare(artifact, destination)
    replacement = destination / artifact[1].name
    assert not (replacement / preparation.BRIDGE).exists()
    if point == 'before_copy':
        assert list(replacement.iterdir()) == []
    assert not (destination / preparation.IDENTITY).exists()
    assert not (destination / preparation.ORIGINAL_RECEIPT).exists()


@pytest.mark.parametrize('wrong', [(), (1,), (True, 1), ('1', 1), [1, 2], (-1, 2), (0, 0)])
def test_copy_optional_target_fence_refuses_before_writes(artifact, tmp_path, wrong):
    from executor.autonomy.bundle_copy import copy_bundle_payload
    source = artifact[1]
    target = tmp_path / 'copy'; target.mkdir()
    with pytest.raises(ValueError, match='installer_stage_unverified'):
        copy_bundle_payload(source, target, consumer._bundle_transaction_identity(source),
                            expected_target_identity=wrong)
    assert list(target.iterdir()) == []


def test_original_copy_call_without_new_fence_still_preserves_complete_payload(artifact, tmp_path):
    from executor.autonomy.bundle_copy import copy_bundle_payload
    source = artifact[1]; target = tmp_path / 'copy'; target.mkdir()
    copy_bundle_payload(source, target, consumer._bundle_transaction_identity(source))
    names = {path.relative_to(source).as_posix() for path in distribution._bundle_members(source)}
    assert preparation._capture(source, names) == preparation._capture(target, names)
    assert consumer._trusted_bundle(target)


@pytest.mark.parametrize('bound', ['MAX_DISTRIBUTION_MEMBERS', 'MAX_DISTRIBUTION_EXPANDED_BYTES'])
def test_preparation_inventory_bounds_refuse_before_output(artifact, tmp_path, monkeypatch, bound):
    source = artifact[1]
    names = {path.relative_to(source).as_posix() for path in distribution._bundle_members(source)}
    monkeypatch.setattr(distribution, bound, 1)
    with pytest.raises(ValueError, match='signing_inventory_bound'):
        preparation._capture(source, names)


def test_capture_refuses_midstream_payload_change(artifact, monkeypatch):
    source = artifact[1]
    names = {path.relative_to(source).as_posix() for path in distribution._bundle_members(source)}
    read = preparation.os.read
    native = source / 'Contents/Resources/native-host/AIApplicationWindow'
    changed = []
    def mutate(fd, count):
        data = read(fd, count)
        if data == MACHO and not changed:
            changed.append(True)
            native.write_bytes(MACHO + b' changed')
        return data
    monkeypatch.setattr(preparation.os, 'read', mutate)
    with pytest.raises(ValueError, match='signing_inventory_changed'):
        preparation._capture(source, names)
    assert changed


def test_input_receipt_change_during_preparation_never_commits_identity(artifact, tmp_path, monkeypatch):
    capture = preparation._capture
    changed = []
    receipt = artifact[2] / distribution.RECEIPT_NAME
    def mutate(app, names):
        result = capture(app, names)
        if not changed:
            changed.append(True)
            receipt.write_bytes(receipt.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(preparation, '_capture', mutate)
    with pytest.raises(ValueError, match='signing_preparation_changed'):
        prepare(artifact, tmp_path / 'workspace')
    assert not (tmp_path / 'workspace' / preparation.IDENTITY).exists()


@pytest.mark.parametrize('size', [-1, 1])
def test_darwin_metadata_unavailable_or_nonempty_never_looks_empty(monkeypatch, size):
    class Call:
        def __call__(self, fd, buffer, length, options):
            assert (fd, buffer, length, options) == (123, None, 0, 0)
            return size
    class Library:
        flistxattr = Call()
    monkeypatch.setattr(preparation.sys, 'platform', 'darwin')
    monkeypatch.setattr(preparation.ctypes, 'CDLL', lambda *a, **k: Library())
    with pytest.raises((ValueError, OSError), match='signing_metadata_'):
        preparation._no_xattrs(123)


def test_normalized_case_collision_is_refused_even_when_both_paths_are_declared(artifact):
    source = artifact[1]
    path = source / 'Contents/Resources/runtime/bin/PYTHON'
    if path.exists():
        pytest.skip('Distinct case-collision paths require a case-sensitive filesystem')
    path.write_bytes(MACHO)
    names = {p.relative_to(source).as_posix() for p in distribution._bundle_members(source)}
    with pytest.raises(ValueError, match='signing_inventory_collision'):
        preparation._capture(source, names)


def test_mutation_during_final_receipt_commit_never_returns_completion(artifact, tmp_path, monkeypatch):
    write = preparation._write_at
    output = tmp_path / 'workspace'
    def mutate(fd, name, data):
        write(fd, name, data)
        if name == preparation.IDENTITY:
            app = output / artifact[1].name
            (app / 'Contents/Resources/runtime/bin/python').write_bytes(MACHO + b'changed')
    monkeypatch.setattr(preparation, '_write_at', mutate)
    with pytest.raises(ValueError, match='signing_preparation_changed'):
        prepare(artifact, output)
    # The failed output is retained, not repaired or published as a success.
    saved = json.loads((output / preparation.IDENTITY).read_text())
    python = output / artifact[1].name / 'Contents/Resources/runtime/bin/python'
    assert saved['files']['Contents/Resources/runtime/bin/python']['sha256'] != distribution._sha256(python)
    assert saved['consumer_admission'] == 'DISALLOWED'


def test_build_entry_matches_builders_expanded_home_output(artifact, tmp_path, monkeypatch, capsys):
    from scripts import build_macos_app
    source = artifact[2]
    receipt = json.loads((source / distribution.RECEIPT_NAME).read_text())
    def synthetic_build(repo, **kwargs):
        shutil.copytree(source, kwargs['output_dir'].expanduser().absolute())
        return receipt
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(distribution, 'build_macos_distribution', synthetic_build)
    assert build_macos_app.main(['--standalone-runtime', str(tmp_path / 'unused'),
        '--output', '~/built', '--signing-workspace', '~/workspace',
        '--publisher-team-id', 'SYNTHETIC1', '--publisher-bundle-id', consumer.BUNDLE_ID]) == 0
    assert json.loads(capsys.readouterr().out)['signing_preparation']['consumer_admission'] == 'DISALLOWED'
    assert (tmp_path / 'workspace' / preparation.IDENTITY).is_file()
