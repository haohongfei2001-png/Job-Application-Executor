"""Signed installer transaction contracts using inert, non-Apple test bytes.

Only publisher verification and runtime execution are synthetic boundaries.
Passing these tests does not establish Developer ID, notarization or Mac health.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

import pytest

from test_macos_signing_preparation import artifact, POLICY
from test_macos_signing_finalization import prepared, synthetic_transition
from test_signed_current_payload import current_transition, inert_publisher_seam
from executor.autonomy import app_distribution, bundle_copy, consumer
from executor.autonomy import consumer_installer, publisher_policy, signed_payload


@pytest.fixture
def admitted_signed(current_transition, monkeypatch):
    _, app, _ = current_transition
    inert_publisher_seam(monkeypatch)
    # Test-only simulation of a separately reviewed verifier build. No fixture
    # metadata, environment variable or caller-selected JSON establishes trust.
    monkeypatch.setattr(publisher_policy, 'BUILT_IN_PUBLISHER_POLICY', dict(POLICY))
    assert consumer._bundle_transaction_identity(app) is not None
    return app


def snapshot(app):
    return {path.relative_to(app).as_posix(): (
        stat.S_IMODE(path.lstat().st_mode),
        path.read_bytes() if path.is_file() else None)
        for path in (app, *app.rglob('*'))}


def xattr(path):
    if not hasattr(os, 'setxattr'):
        pytest.skip('Host does not expose the xattr injection seam')
    try:
        os.setxattr(path, 'user.synthetic_boundary', b'')
    except OSError as exc:
        pytest.skip(f'Filesystem cannot store test xattrs: {exc}')


def no_rebuild(monkeypatch):
    def refused(*args, **kwargs):
        pytest.fail('A signed bundle must never enter source/runtime reconstruction')
    for name in ('copy_source_candidate', 'copy_runtime_candidate',
                 'copy_standalone_runtime_candidate', '_copy_native_host_candidate',
                 '_stage_native_host'):
        monkeypatch.setattr(consumer, name, refused)


def inert_execution(monkeypatch):
    monkeypatch.setattr(consumer, 'verify_standalone_runtime', lambda *a, **k: True)
    monkeypatch.setattr(consumer, '_candidate_starts', lambda *a, **k: True)
    monkeypatch.setattr(consumer, '_prepare_task_state_release', lambda *a, **k: True)
    no_rebuild(monkeypatch)


def install(app, apps, state):
    return consumer.install_macos_bundle(app, destination=apps,
        task_state_root=state, platform='darwin')


@pytest.mark.parametrize('entry', ['inventory', 'copy', 'install'])
def test_missing_fixed_policy_refuses_before_execution_or_staging(
        current_transition, monkeypatch, tmp_path, entry):
    _, app, _ = current_transition
    assert publisher_policy.BUILT_IN_PUBLISHER_POLICY is None
    monkeypatch.setenv('JAE_PUBLISHER_TEAM_ID', POLICY['team_id'])
    monkeypatch.setenv('JAE_PUBLISHER_POLICY', json.dumps(POLICY))
    (app.parent / 'publisher-policy.json').write_text(json.dumps(POLICY))
    monkeypatch.setattr(consumer, 'verify_standalone_runtime',
        lambda *a, **k: pytest.fail('No configured publisher must refuse before execution'))
    target = tmp_path / 'copy.app'
    target.mkdir()
    if entry == 'inventory':
        with pytest.raises(ValueError, match='publisher_policy_required'):
            app_distribution._signed_bundle_members(app)
    elif entry == 'copy':
        with pytest.raises(ValueError, match='publisher_policy_required'):
            bundle_copy.copy_bundle_payload(app, target, (0, 0, ()))
    else:
        assert install(app, tmp_path / 'Applications', tmp_path / 'state')['ok'] is False
    assert list(target.iterdir()) == []
    assert not (tmp_path / 'Applications').exists()
    assert not (tmp_path / 'state').exists()


def test_signed_copy_preserves_all_bytes_and_modes(admitted_signed, tmp_path):
    source = admitted_signed
    before = snapshot(source)
    identity = consumer._bundle_transaction_identity(source)
    target = tmp_path / 'copied.app'
    target.mkdir(mode=0o700)
    root = target.stat()
    bundle_copy.copy_bundle_payload(source, target, identity,
        expected_target_identity=(root.st_dev, root.st_ino))
    assert snapshot(source) == snapshot(target) == before
    assert consumer._bundle_transaction_identity(source) == identity
    assert consumer._bundle_transaction_identity(target) is not None


@pytest.mark.parametrize('where', ['.', 'Contents', 'Contents/_CodeSignature',
    'Contents/_CodeSignature/CodeSignature', signed_payload.RELATIVE_PATH])
def test_signed_inventory_refuses_even_empty_xattrs(admitted_signed, where):
    app = admitted_signed
    xattr(app / where)
    with pytest.raises(ValueError, match='signing_metadata_unsupported'):
        app_distribution._signed_bundle_members(app)


@pytest.mark.parametrize('fault', ['root_mode', 'root_xattr', 'directory_mode',
    'directory_xattr', 'signature_xattr', 'extra_signature_file', 'extra_outer_file'])
def test_transaction_identity_rejects_signed_layout_drift(admitted_signed, fault):
    app = admitted_signed
    identity = consumer._bundle_transaction_identity(app)
    assert identity is not None
    if fault == 'root_mode':
        app.chmod(0o700)
    elif fault == 'directory_mode':
        (app / 'Contents/_CodeSignature').chmod(0o700)
    elif fault == 'root_xattr':
        xattr(app)
    elif fault == 'directory_xattr':
        xattr(app / 'Contents/_CodeSignature')
    elif fault == 'signature_xattr':
        xattr(app / 'Contents/_CodeSignature/CodeSignature')
    elif fault == 'extra_signature_file':
        (app / 'Contents/_CodeSignature/unplanned').write_bytes(b'foreign evidence')
    else:
        (app / 'unplanned').write_bytes(b'foreign evidence')
    assert consumer._bundle_transaction_identity(app) is None


@pytest.mark.parametrize('name', ['CodeDirectory', 'CodeRequirements', 'CodeResources', 'CodeSignature'])
def test_selected_signature_bytes_are_in_transaction_identity(admitted_signed, name):
    app = admitted_signed
    identity = consumer._bundle_transaction_identity(app)
    path = app / 'Contents/_CodeSignature' / name
    path.write_bytes(path.read_bytes() + b'changed selected signature')
    assert consumer._bundle_transaction_identity(app) != identity


@pytest.mark.parametrize('fault', ['source_xattr', 'source_bytes', 'source_symlink',
    'target_xattr', 'target_extra', 'target_replacement'])
def test_signed_copy_refuses_midstream_changes_and_preserves_evidence(
        admitted_signed, tmp_path, monkeypatch, fault):
    source = admitted_signed
    identity = consumer._bundle_transaction_identity(source)
    target = tmp_path / 'copied.app'
    target.mkdir()
    private = tmp_path / 'private'
    private.write_bytes(b'PRIVATE_CANARY')
    private_before = (private.read_bytes(), private.stat().st_ino)
    read = bundle_copy.os.read
    triggered = []
    def race(fd, count):
        data = read(fd, count)
        if data and not triggered:
            triggered.append(True)
            if fault == 'source_xattr':
                xattr(source)
            elif fault == 'source_bytes':
                path = source / 'Contents/_CodeSignature/CodeSignature'
                path.write_bytes(path.read_bytes() + b'raced')
            elif fault == 'source_symlink':
                path = source / 'Contents/_CodeSignature/CodeSignature'
                path.unlink()
                path.symlink_to(private)
            elif fault == 'target_xattr':
                xattr(target)
            elif fault == 'target_extra':
                (target / 'unplanned').write_bytes(b'RETAIN_EXTRA_EVIDENCE')
            else:
                target.rename(tmp_path / 'retained-copy.app')
                target.mkdir()
                (target / 'keep').write_bytes(b'RETAIN_REPLACEMENT')
        return data
    monkeypatch.setattr(bundle_copy.os, 'read', race)
    with pytest.raises((OSError, ValueError)):
        bundle_copy.copy_bundle_payload(source, target, identity)
    assert triggered
    assert source.exists() and target.exists()
    assert (private.read_bytes(), private.stat().st_ino) == private_before
    if fault == 'target_extra':
        assert (target / 'unplanned').read_bytes() == b'RETAIN_EXTRA_EVIDENCE'
    if fault == 'target_replacement':
        assert (target / 'keep').read_bytes() == b'RETAIN_REPLACEMENT'
        assert (tmp_path / 'retained-copy.app').exists()


def test_signed_fresh_install_update_and_rollback_keep_exact_bundles(
        admitted_signed, monkeypatch, tmp_path):
    source = admitted_signed
    inert_execution(monkeypatch)
    apps, state = tmp_path / 'Applications', tmp_path / 'state'
    first_bytes = snapshot(source)
    first = install(source, apps, state)
    assert first['ok'] is True and first['replaced'] is False
    active = apps / (consumer.APP_NAME + '.app')
    assert snapshot(active) == first_bytes
    candidate = tmp_path / 'second.app'
    shutil.copytree(source, candidate)
    # The test verifier admits a distinct synthetic envelope. Nothing here
    # simulates a real Apple signature or alters sealed runtime provenance.
    (candidate / 'Contents/_CodeSignature/CodeSignature').write_bytes(b'SECOND INERT SEAL')
    second_bytes = snapshot(candidate)
    second = install(candidate, apps, state)
    assert second['ok'] is True and second['replaced'] is True
    previous = apps / ('.' + consumer.APP_NAME + '.app.previous')
    assert snapshot(active) == second_bytes
    assert snapshot(previous) == first_bytes
    result = consumer.rollback_macos_app(apps, task_state_root=state)
    assert result['ok'] is True and result['restored'] is True
    assert snapshot(active) == first_bytes
    assert snapshot(apps / ('.' + consumer.APP_NAME + '.app.failed')) == second_bytes
    assert snapshot(source) == first_bytes and snapshot(candidate) == second_bytes


@pytest.mark.parametrize('phase', ['health', 'state_compatibility'])
def test_failed_signed_health_never_deletes_a_replacement_staging_directory(
        admitted_signed, monkeypatch, tmp_path, phase):
    inert_execution(monkeypatch)
    apps, state = tmp_path / 'Applications', tmp_path / 'state'
    staging = apps / ('.' + consumer.APP_NAME + '.app.installing')
    saved = apps / 'retained-original.app'
    calls = []
    def replace_during_health(*args, **kwargs):
        calls.append(True)
        staging.rename(saved)
        staging.mkdir()
        (staging / 'private-canary').write_bytes(b'UNRELATED_USER_DATA')
        return False
    hook = '_candidate_starts' if phase == 'health' else '_prepare_task_state_release'
    monkeypatch.setattr(consumer, hook, replace_during_health)
    result = install(admitted_signed, apps, state)
    assert result['ok'] is False and calls == [True]
    assert (staging / 'private-canary').read_bytes() == b'UNRELATED_USER_DATA'
    assert snapshot(saved) == snapshot(admitted_signed)
    assert not (apps / (consumer.APP_NAME + '.app')).exists()


@pytest.mark.parametrize('candidate_signed', [False, True])
def test_downloaded_cross_kind_refuses_before_prompt_locks_or_service_stop(
        admitted_signed, artifact, monkeypatch, tmp_path, candidate_signed):
    home = tmp_path / 'home'
    monkeypatch.setenv('HOME', str(home))
    target = home / 'Applications' / (consumer.APP_NAME + '.app')
    signed = admitted_signed
    unsigned = artifact[1]
    candidate, existing = (signed, unsigned) if candidate_signed else (unsigned, signed)
    shutil.copytree(existing, target)
    before = snapshot(target)
    from executor.autonomy.runtime_paths import default_runtime
    state = Path(default_runtime(target / 'Contents/Resources/release')).absolute()
    for name in ('_capture', '_retire', '_app_lock'):
        monkeypatch.setattr(consumer_installer, name,
            lambda *a, **k: pytest.fail('Cross-kind update reached an authority boundary'))
    result = consumer_installer.run_downloaded_installer(candidate, target, state, 8765,
        consumer._bundle_transaction_identity(candidate))
    assert result['ok'] is False and result['reason'] == 'installer_bundle_kind_mismatch'
    assert snapshot(target) == before
    assert not state.exists()


@pytest.mark.parametrize('fault', ['root_mode', 'root_xattr', 'extra_signature_file'])
def test_signed_rollback_refuses_drift_during_health_without_moving_either_bundle(
        admitted_signed, monkeypatch, tmp_path, fault):
    inert_execution(monkeypatch)
    apps, state = tmp_path / 'Applications', tmp_path / 'state'
    active = apps / (consumer.APP_NAME + '.app')
    previous = apps / ('.' + consumer.APP_NAME + '.app.previous')
    shutil.copytree(admitted_signed, active)
    shutil.copytree(admitted_signed, previous)
    active_before = snapshot(active)
    inodes = active.stat().st_ino, previous.stat().st_ino
    calls = []
    def drift(*args, **kwargs):
        calls.append(True)
        if fault == 'root_mode':
            previous.chmod(0o700)
        elif fault == 'root_xattr':
            xattr(previous)
        else:
            (previous / 'Contents/_CodeSignature/unplanned').write_bytes(b'PRESERVE_EVIDENCE')
        return True
    monkeypatch.setattr(consumer, '_candidate_starts', drift)
    result = consumer.rollback_macos_app(apps, task_state_root=state)
    assert result['ok'] is False and result['reason'] == 'rollback_identity_changed'
    assert calls == [True]
    assert (active.stat().st_ino, previous.stat().st_ino) == inodes
    assert snapshot(active) == active_before
    assert not (apps / ('.' + consumer.APP_NAME + '.app.failed')).exists()
    if fault == 'extra_signature_file':
        assert (previous / 'Contents/_CodeSignature/unplanned').read_bytes() == b'PRESERVE_EVIDENCE'


def test_signed_release_cannot_use_source_reconstruction_entry(
        admitted_signed, monkeypatch, tmp_path):
    no_rebuild(monkeypatch)
    resources = admitted_signed / 'Contents/Resources'
    result = consumer.install_macos_app(resources / 'release',
        standalone_runtime=resources / 'runtime', destination=tmp_path / 'Applications',
        task_state_root=tmp_path / 'state', platform='darwin')
    assert result['ok'] is False and result['reason'] == 'signed_bundle_copy_required'
    assert not (tmp_path / 'Applications' / ('.' + consumer.APP_NAME + '.app.installing')).exists()


@pytest.mark.parametrize('entry', ['identity', 'install', 'source_install'])
def test_signature_without_current_manifest_never_downgrades_to_unsigned(
        artifact, monkeypatch, tmp_path, entry):
    app = artifact[1]
    # An otherwise valid unsigned snapshot models a stripped/resealed runtime.
    # Remaining signing bytes must not fall through to unsigned reconstruction.
    directory = app / 'Contents/_CodeSignature'
    directory.mkdir()
    for name in ('CodeDirectory', 'CodeRequirements', 'CodeResources', 'CodeSignature'):
        (directory / name).write_bytes(b'UNVERIFIED RETAINED SIGNATURE')
    assert not signed_payload.has_current_payload(app)
    if entry == 'identity':
        assert consumer._bundle_transaction_identity(app) is None
    elif entry == 'install':
        monkeypatch.setattr(consumer, 'verify_standalone_runtime',
            lambda *a, **k: pytest.fail('Missing current manifest entered runtime execution'))
        no_rebuild(monkeypatch)
        result = install(app, tmp_path / 'Applications', tmp_path / 'state')
        assert result['ok'] is False
        assert not (tmp_path / 'Applications').exists()
    else:
        no_rebuild(monkeypatch)
        resources = app / 'Contents/Resources'
        result = consumer.install_macos_app(resources / 'release',
            standalone_runtime=resources / 'runtime', destination=tmp_path / 'Applications',
            task_state_root=tmp_path / 'state', platform='darwin')
        assert result['ok'] is False and result['reason'] == 'signed_bundle_manifest_required'
        assert not (tmp_path / 'Applications' / ('.' + consumer.APP_NAME + '.app.installing')).exists()
    assert directory.is_dir()


def test_signed_copy_final_verifier_must_still_bind_original_destination_inode(
        admitted_signed, monkeypatch, tmp_path):
    source = admitted_signed
    expected = consumer._bundle_transaction_identity(source)
    target = tmp_path / 'copied.app'
    target.mkdir()
    pinned = target.stat()
    retained = tmp_path / 'retained-copy.app'
    identity = consumer._bundle_transaction_identity
    swapped = []
    def substitute_at_final_verifier(app, **kwargs):
        if Path(app) == target and not swapped:
            swapped.append(True)
            target.rename(retained)
            shutil.copytree(source, target)
            (target / 'Contents/_CodeSignature/CodeSignature').write_bytes(b'DIFFERENT INERT SEAL')
        return identity(app, **kwargs)
    monkeypatch.setattr(consumer, '_bundle_transaction_identity', substitute_at_final_verifier)
    with pytest.raises((ValueError, OSError)):
        bundle_copy.copy_bundle_payload(source, target, expected,
            expected_target_identity=(pinned.st_dev, pinned.st_ino))
    assert swapped == [True]
    assert snapshot(retained) == snapshot(source)
    assert (target / 'Contents/_CodeSignature/CodeSignature').read_bytes() == b'DIFFERENT INERT SEAL'


def test_strict_signed_first_install_preserves_bundle_and_pending_fence(
        admitted_signed, monkeypatch, tmp_path):
    from executor.autonomy import first_install
    inert_execution(monkeypatch)
    apps, state = tmp_path / 'Applications', tmp_path / 'state'
    identity = consumer._bundle_transaction_identity(admitted_signed)
    before = snapshot(admitted_signed)
    activations = []
    def inert_exclusive_move(staging, active):
        # Only the unavailable Darwin rename primitive is replaced. This
        # validates orchestration, not OS-level no-replace atomicity.
        assert not active.exists() and not active.is_symlink()
        activations.append((staging, active))
        staging.rename(active)
    monkeypatch.setattr(consumer, '_activate_first_install_exclusive', inert_exclusive_move)
    result = consumer.install_macos_bundle(admitted_signed, destination=apps,
        task_state_root=state, platform='darwin', _first_install=True,
        _approved_identity=identity)
    assert result['ok'] is True and result['replaced'] is False
    active = apps / (consumer.APP_NAME + '.app')
    assert activations == [(apps / ('.' + consumer.APP_NAME + '.app.installing'), active)]
    assert snapshot(active) == snapshot(admitted_signed) == before
    active_identity = consumer._bundle_transaction_identity(active)
    assert first_install._read_first_fence(apps) == {
        'format': 'jae-first-install-fence-v1', 'status': 'pending',
        'bundle_tag': first_install._bundle_tag(active_identity)}


@pytest.mark.parametrize('fault', ['hardlink', 'directory_alias', 'case_collision', 'signature_mode'])
def test_signed_copy_rejects_unadmitted_source_before_destination_writes(
        admitted_signed, monkeypatch, tmp_path, fault):
    source = admitted_signed
    identity = consumer._bundle_transaction_identity(source)
    target = tmp_path / 'copied.app'
    target.mkdir()
    private = tmp_path / 'private'
    private.mkdir()
    canary = private / 'keep'
    canary.write_bytes(b'PRIVATE_CANARY')
    signature = source / 'Contents/_CodeSignature/CodeSignature'
    if fault == 'hardlink':
        signature.unlink()
        os.link(canary, signature)
    elif fault == 'directory_alias':
        directory = source / 'Contents/_CodeSignature'
        directory.rename(tmp_path / 'retained-signature')
        directory.symlink_to(private, target_is_directory=True)
    elif fault == 'case_collision':
        signature.with_name('codesignature').write_bytes(b'COLLIDING INERT NAME')
    else:
        signature.chmod(0o755)
    with pytest.raises((ValueError, OSError)):
        bundle_copy.copy_bundle_payload(source, target, identity)
    assert list(target.iterdir()) == []
    assert canary.read_bytes() == b'PRIVATE_CANARY'
    assert source.exists()
