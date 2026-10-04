"""Synthetic probe contracts. These tests never claim an Apple signature PASS."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tarfile
import subprocess

import pytest

from scripts import probe_macos_signature as probe


def set_attribute(path, name, data):
    if os.sys.platform == 'darwin':
        subprocess.run(['/usr/bin/xattr', '-w', '-x', name, data.hex(), str(path)],
                       check=True, timeout=10)
    else:
        os.setxattr(path, name, data)


def remove_attribute(path, name):
    if os.sys.platform == 'darwin':
        subprocess.run(['/usr/bin/xattr', '-d', name, str(path)], check=True, timeout=10)
    else:
        os.removexattr(path, name)


def tree(tmp_path):
    app = tmp_path / 'Synthetic.app'; app.mkdir()
    (app / 'Contents').mkdir()
    leaf = app / 'Contents' / 'payload'
    leaf.write_bytes(b'public synthetic payload')
    return app, leaf


def test_inventory_records_exact_bytes_modes_without_contents(tmp_path):
    app, leaf = tree(tmp_path)
    before = probe.inventory(app)
    assert before['Contents/payload']['sha256'] == probe.digest(leaf)
    assert b'public synthetic payload' not in json.dumps(before).encode()
    leaf.chmod(0o700)
    assert probe.changes(before, probe.inventory(app))['changed'] == ['Contents/payload']
    leaf.write_bytes(b'changed bytes')
    assert probe.inventory_digest(before) != probe.inventory_digest(probe.inventory(app))


@pytest.mark.parametrize('alias', ['file', 'directory', 'root', 'ancestor', 'hardlink'])
def test_inventory_refuses_aliases_without_following(tmp_path, alias):
    app, leaf = tree(tmp_path)
    outside = tmp_path / 'outside'; outside.mkdir()
    secret = outside / 'private'; secret.write_bytes(b'NEVER_READ_CANARY')
    if alias == 'file':
        leaf.unlink(); leaf.symlink_to(secret)
    elif alias == 'directory':
        (app / 'alias').symlink_to(outside, target_is_directory=True)
    elif alias == 'root':
        link = tmp_path / 'linked'; link.symlink_to(app, target_is_directory=True); app = link
    elif alias == 'ancestor':
        link = tmp_path / 'linked'; link.symlink_to(tmp_path, target_is_directory=True); app = link / app.name
    else:
        leaf.unlink(); os.link(secret, leaf)
    with pytest.raises(ValueError, match='inventory_'):
        probe.inventory(app)
    assert secret.read_bytes() == b'NEVER_READ_CANARY'


@pytest.mark.parametrize('limit,value', [('MAX_FILES', 2), ('MAX_BYTES', 2)])
def test_inventory_bounds_are_enforced(tmp_path, monkeypatch, limit, value):
    app, _ = tree(tmp_path)
    monkeypatch.setattr(probe, limit, value)
    with pytest.raises(ValueError, match='inventory_bound'):
        probe.inventory(app)


def test_bytes_only_copy_drops_xattrs_but_keeps_modes_and_bytes(tmp_path):
    app, leaf = tree(tmp_path)
    attribute = 'user.jae-test' if os.sys.platform != 'darwin' else 'org.jae.test'
    set_attribute(leaf, attribute, b'synthetic attribute')
    leaf.chmod(0o755)
    copied = tmp_path / 'copy'
    probe.byte_copy(app, copied)
    before, after = probe.inventory(app), probe.inventory(copied)
    assert before['Contents/payload']['sha256'] == after['Contents/payload']['sha256']
    assert after['Contents/payload']['mode'] == 0o755
    assert after['Contents/payload']['xattrs'] == {}
    assert probe.changes(before, after)['changed'] == ['Contents/payload']


def test_control_archive_preserves_sidecars_and_has_no_xattr_transport(tmp_path):
    app, leaf = tree(tmp_path)
    signature = app / 'Contents/_CodeSignature'; signature.mkdir()
    (signature / 'CodeResources').write_bytes(b'SYNTHETIC NOT A SIGNATURE')
    attribute = 'user.jae-test' if os.sys.platform != 'darwin' else 'org.jae.test'
    set_attribute(leaf, attribute, b'synthetic attribute')
    archive = tmp_path / 'control.tar.gz'
    probe.raw_archive(app, archive)
    output = tmp_path / 'output'; output.mkdir()
    with tarfile.open(archive) as source:
        assert all(not member.pax_headers for member in source.getmembers())
        source.extractall(output, filter='data')
    assert (output / app.name / 'Contents/_CodeSignature/CodeResources').read_bytes() == b'SYNTHETIC NOT A SIGNATURE'
    assert probe.inventory(output / app.name)['Contents/payload']['xattrs'] == {}


def test_existing_production_inventory_still_refuses_signature_envelope(tmp_path):
    from executor.autonomy.app_distribution import _bundle_members
    app = tmp_path / 'Synthetic.app'; (app / 'Contents/_CodeSignature').mkdir(parents=True)
    (app / 'Contents/_CodeSignature/CodeResources').write_bytes(b'SYNTHETIC')
    with pytest.raises(ValueError, match='distribution_private_payload'):
        _bundle_members(app)


def test_command_cannot_execute_candidate(tmp_path):
    with pytest.raises(ValueError, match='probe_tool_invalid'):
        probe.command([str(tmp_path / 'candidate')])


def test_ad_hoc_signing_never_uses_key_or_deep(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, 'command', lambda argv: calls.append(argv) or {'returncode': 0})
    probe.seal(Path('Synthetic.app'))
    probe.seal(Path('Synthetic.app/component'), identifier='org.jae.preflight.component.0')
    assert all(c[:5] == ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none'] for c in calls)
    assert all('--deep' not in c and '--keychain' not in c and '--entitlements' not in c for c in calls)


def test_observed_refusal_is_not_boolean_success():
    def refuse():
        raise ValueError('fixed_refusal')
    assert probe.observed(refuse) == {'refused': 'ValueError', 'reason': 'fixed_refusal'}
    assert probe.observed(lambda: False) == {'returned': False}


def test_macho_catalog_uses_magic_not_filename(tmp_path):
    app, leaf = tree(tmp_path)
    leaf.write_bytes(b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01' + b'synthetic header')
    (app / 'fake.dylib').write_text('not an executable')
    result = probe.inventory(app)
    assert result['Contents/payload']['macho'] is True
    assert result['fake.dylib']['macho'] is False


@pytest.mark.parametrize('failure', ['xattr_loss', 'invalid_os_signature'])
def test_tamper_requires_exact_valid_baseline(tmp_path, monkeypatch, failure):
    app, leaf = tree(tmp_path)
    attribute = 'user.jae-test' if os.sys.platform != 'darwin' else 'org.jae.test'
    set_attribute(leaf, attribute, b'synthetic signature metadata')
    pinned = probe.inventory(app)
    copy = probe.metadata_copy
    def copy_then_lose_metadata(source, target):
        result = copy(source, target)
        remove_attribute(target / 'Contents/payload', attribute)
        return result
    if failure == 'xattr_loss':
        monkeypatch.setattr(probe, 'metadata_copy', copy_then_lose_metadata)
    monkeypatch.setattr(probe, 'verify', lambda *a: {'returncode': int(failure == 'invalid_os_signature')})
    monkeypatch.setattr(probe, 'seal', lambda *a, **k: pytest.fail('no tamper or re-sign before a proven baseline'))
    results = probe.tamper_cases(app, tmp_path / 'tamper', pinned)
    assert results
    assert all(v['status'] == 'INCONCLUSIVE_BASELINE_NOT_ESTABLISHED' for v in results.values())
    assert all('pinned_inventory_matches' not in v and 'os_verify' not in v for v in results.values())
    assert probe.inventory(app) == pinned


def test_metadata_control_reports_exact_or_observed_empty_attribute_loss(tmp_path):
    app, leaf = tree(tmp_path)
    prefix = 'user.' if os.sys.platform != 'darwin' else 'org.jae.'
    set_attribute(leaf, prefix + 'nonempty', b'synthetic signature metadata')
    set_attribute(leaf, prefix + 'empty', b'')
    pinned = probe.inventory(app)
    target = tmp_path / 'copy'
    result = probe.metadata_copy(app, target)
    actual = probe.inventory(target)
    if actual == pinned:
        assert result == {'status': 'PASS_EXACT', 'delta': {'added': [], 'removed': [], 'changed': []}}
    else:
        # Hosted macOS ditto was observed to drop the zero-length attribute.
        # Preserve this as a failed transport control, never an exact-copy PASS.
        assert os.sys.platform == 'darwin'
        expected = json.loads(json.dumps(pinned))
        del expected['Contents/payload']['xattrs'][prefix + 'empty']
        assert actual == expected
        assert result == {'status': 'FAIL_INVENTORY_LOSS',
                          'delta': {'added': [], 'removed': [], 'changed': ['Contents/payload']}}
    assert probe.inventory(app) == pinned


def test_attribute_transport_failure_is_kept_in_report(tmp_path, monkeypatch):
    # Pure report contract: mock every platform boundary. In the previous Mac
    # run a mocked command disabled ditto, then real inventory read its absent
    # destination. Real xattr/ditto behavior belongs to the separate control.
    calls = []
    monkeypatch.setattr(probe, 'command', lambda argv: calls.append(argv) or {'returncode': 0})
    before = {'synthetic': {'xattrs': {'empty': 'digest'}}}
    after = {'synthetic': {'xattrs': {}}}
    monkeypatch.setattr(probe, 'inventory', lambda path: after if path.name == 'copy' else before)
    monkeypatch.setattr(probe, 'metadata_copy', lambda *a: {
        'status': 'FAIL_INVENTORY_LOSS', 'delta': {'changed': ['synthetic']}})
    report = probe.attribute_transport_control(tmp_path / 'control')
    assert report['status'] == 'FAIL_INVENTORY_LOSS'
    assert report['before'] == before and report['after'] == after
    assert len(calls) == 2 and all(argv[:3] == ['/usr/bin/xattr', '-w', '-x'] for argv in calls)
    assert not (tmp_path / 'control/copy').exists()


@pytest.mark.parametrize('failure', ['read_error', 'growth', 'bound', 'duplicate'])
def test_darwin_xattrs_refuse_errors_changes_and_oversize(tmp_path, monkeypatch, failure):
    class Library:
        def flistxattr(self, fd, buffer, size, options):
            assert options == 0 and fd >= 0
            if failure == 'read_error':
                return -1
            if failure == 'bound':
                return 65_537
            if failure == 'growth':
                return 2 if buffer is None else 3
            data = b'dup\0dup\0'
            if buffer is not None:
                probe.ctypes.memmove(buffer, data, len(data))
            return len(data)
    monkeypatch.setattr(probe.sys, 'platform', 'darwin')
    monkeypatch.setattr(probe, '_darwin_xattr_api', lambda: Library())
    with pytest.raises((ValueError, OSError), match='probe_xattr_'):
        probe.xattrs(tmp_path)


def test_production_install_not_called_if_intake_unexpectedly_accepts(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from executor.autonomy import app_distribution, bundle_copy, consumer, macos_installer_image
    app, _ = tree(tmp_path)
    monkeypatch.setattr(app_distribution, '_bundle_members', lambda app: [])
    monkeypatch.setattr(app_distribution, '_archive_app', lambda *a: None)
    monkeypatch.setattr(bundle_copy, 'copy_bundle_payload', lambda *a: None)
    monkeypatch.setattr(consumer, '_bundle_transaction_identity', lambda *a: None)
    monkeypatch.setattr(macos_installer_image, 'build_installer_image', lambda *a: {})
    monkeypatch.setattr(probe, 'verify', lambda *a: {'returncode': 0})
    @contextmanager
    def admit(*args):
        yield app
    monkeypatch.setattr(app_distribution, 'stage_macos_distribution', admit)
    def forbidden(*args, **kwargs):
        pytest.fail('installer must not run after unexpected signed intake acceptance')
    monkeypatch.setattr(app_distribution, 'install_macos_distribution', forbidden)
    with pytest.raises(ValueError, match='signed_intake_unexpectedly_admitted'):
        probe.production_routes(app, {}, tmp_path / 'routes')


@pytest.mark.parametrize('outcome', ['valid', 'seal_refused', 'initial_verify_refused',
                                     'baseline_changed', 'tamper_accepted'])
def test_platform_control_keeps_reports_and_requires_valid_tamper_baseline(tmp_path, monkeypatch, outcome):
    # Pure orchestration test. Native commands are forbidden, rather than
    # selectively mocked while filesystem consumers still expect their effects.
    monkeypatch.setattr(probe, 'command', lambda *a, **k: pytest.fail('unexpected platform command'))
    loss = {'status': 'FAIL_INVENTORY_LOSS', 'delta': {'changed': ['synthetic']}}
    monkeypatch.setattr(probe, 'attribute_transport_control', lambda *a: loss)
    monkeypatch.setattr(probe, 'metadata_copy', lambda source, target: (
        probe.byte_copy(source, target) or {'status': 'PASS_EXACT'}))
    def synthetic_seal(app):
        directory = app / 'Contents/_CodeSignature'; directory.mkdir()
        (directory / 'CodeResources').write_bytes(b'SYNTHETIC NOT A SIGNATURE')
        return {'returncode': int(outcome == 'seal_refused')}
    monkeypatch.setattr(probe, 'seal', synthetic_seal)
    original_verifies = 0
    def synthetic_verify(app):
        nonlocal original_verifies
        if app == tmp_path / 'Synthetic.app':
            original_verifies += 1
            if outcome == 'initial_verify_refused' or (outcome == 'baseline_changed' and original_verifies == 2):
                return {'returncode': 1}
        modified = b'JAE SYNTHETIC TAMPER' in (app / 'Contents/MacOS/Synthetic').read_bytes()
        return {'returncode': int(modified and outcome != 'tamper_accepted')}
    monkeypatch.setattr(probe, 'verify', synthetic_verify)
    report, saved = {}, []
    admitted = probe.platform_control(tmp_path, report, lambda: saved.append(json.loads(json.dumps(report))))
    assert admitted == (outcome == 'valid')
    assert report['empty_attribute_transport_control'] == loss
    assert saved and saved[0]['empty_attribute_transport_control'] == loss
    if outcome in ('seal_refused', 'initial_verify_refused'):
        assert 'metadata_copy' not in report and 'tamper' not in report
        assert report['decision'] == 'SYNTHETIC_SCRIPT_SEAL_REFUSED_PRODUCT_NOT_RUN'
    elif outcome == 'baseline_changed':
        assert report['tamper']['status'] == 'INCONCLUSIVE_BASELINE_NOT_ESTABLISHED'
        assert 'pinned_inventory_matches' not in report['tamper']
        assert b'JAE SYNTHETIC TAMPER' not in (tmp_path / 'Synthetic.app/Contents/MacOS/Synthetic').read_bytes()
    else:
        assert report['tamper']['baseline_inventory_matches'] is True
        assert report['tamper']['baseline_verify']['returncode'] == 0
        assert report['tamper']['status'] == 'MEASURED'
        assert report['tamper']['pinned_inventory_matches'] is False
        assert report['tamper']['verify']['returncode'] == int(outcome == 'valid')


def test_platform_control_precedes_runtime_download_and_product_build():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/macos-signature-preflight.yml').read_text()
    contracts = workflow.index('python -m pytest -q tests/test_macos_signature_probe.py')
    platform_control = workflow.index('python scripts/probe_macos_signature.py --platform-control')
    download = workflow.index('python scripts/prepare_standalone_runtime.py')
    product = workflow.index('python scripts/probe_macos_signature.py --standalone-runtime')
    assert contracts < platform_control < download < product
    assert 'continue-on-error' not in workflow
    assert '${{ runner.temp }}/jae-platform-control/report.json' in workflow
    assert '${{ runner.temp }}/jae-adhoc-preflight/report.json' in workflow
