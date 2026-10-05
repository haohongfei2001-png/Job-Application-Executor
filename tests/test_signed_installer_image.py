"""Signed image wiring seams; only the separate hosted oracle proves a DMG."""
from __future__ import annotations

import json
from pathlib import Path
import plistlib
import shutil
from types import SimpleNamespace

import pytest

from test_macos_signing_preparation import artifact, POLICY
from test_macos_signing_finalization import prepared, synthetic_transition
from test_signed_current_payload import current_transition, inert_publisher_seam
from executor.autonomy import app_distribution, consumer, macos_installer_image as image
from executor.autonomy import publisher_policy, signed_payload
from scripts import build_macos_installer


def build_platform(monkeypatch):
    monkeypatch.setattr(image.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(image.platform, 'machine', lambda: 'arm64')


@pytest.mark.parametrize('distribution,signed', [(None, None), ('one', 'two')])
def test_image_requires_exactly_one_input_before_any_external_tool(tmp_path, monkeypatch, distribution, signed):
    monkeypatch.setattr(image.subprocess, 'run', lambda *a, **k: pytest.fail('invalid input executed'))
    with pytest.raises(ValueError, match='installer_input_required'):
        image.build_installer_image(distribution, tmp_path / 'output', signed_app=signed)
    assert not (tmp_path / 'output').exists()


def test_unsigned_input_cannot_receive_signing_policy(tmp_path):
    with pytest.raises(ValueError, match='installer_unsigned_policy_invalid'):
        image.build_installer_image(tmp_path / 'unsigned', tmp_path / 'output',
                                    required_publisher_policy=POLICY)
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('policy', [None, {}, {**POLICY, 'certificate_kind': 'Apple Development'}])
def test_signed_builder_requires_independent_policy_before_source_or_output(tmp_path, monkeypatch, policy):
    build_platform(monkeypatch)
    monkeypatch.setenv('JAE_PUBLISHER_POLICY', json.dumps(POLICY))
    monkeypatch.setattr(image.subprocess, 'run', lambda *a, **k: pytest.fail('policy refusal executed'))
    with pytest.raises(ValueError, match='publisher_policy_required'):
        image.build_installer_image(output=tmp_path / 'output', signed_app=tmp_path / 'missing',
                                    required_publisher_policy=policy)
    assert publisher_policy.BUILT_IN_PUBLISHER_POLICY is None
    assert not (tmp_path / 'output').exists()


def test_signed_source_is_one_exact_preserved_app_without_unsigned_parser(current_transition, monkeypatch):
    _, app, _ = current_transition
    inert_publisher_seam(monkeypatch)
    monkeypatch.setattr(app_distribution, 'stage_macos_distribution',
                        lambda *a, **k: pytest.fail('signed app entered unsigned parser'))
    before = consumer._bundle_transaction_identity(app, required_publisher_policy=POLICY)
    snapshot = {p.relative_to(app): p.read_bytes() for p in app.rglob('*') if p.is_file()}
    with image._image_source(None, app, POLICY) as staged:
        assert set(staged.parent.iterdir()) == {staged}
        assert staged.name == consumer.APP_NAME + '.app' and staged != app
        copied = consumer._bundle_transaction_identity(staged, required_publisher_policy=POLICY)
        assert copied[:2] != before[:2] and copied[2] == before[2]
        assert {p.relative_to(staged): p.read_bytes() for p in staged.rglob('*') if p.is_file()} == snapshot
    assert not staged.exists()
    assert consumer._bundle_transaction_identity(app, required_publisher_policy=POLICY) == before


@pytest.mark.parametrize('fault', ['missing_current', 'extra_file', 'runtime_tamper'])
def test_signed_source_refusal_leaves_original_and_no_output(current_transition, monkeypatch, tmp_path, fault):
    _, app, _ = current_transition
    inert_publisher_seam(monkeypatch)
    build_platform(monkeypatch)
    if fault == 'missing_current':
        (app / signed_payload.RELATIVE_PATH).unlink()
    elif fault == 'extra_file':
        (app / 'Contents/Resources/extra.txt').write_text('retain this evidence')
    else:
        (app / 'Contents/Resources/runtime/bin/python').write_bytes(b'TAMPERED')
    monkeypatch.setattr(image.subprocess, 'run', lambda *a, **k: pytest.fail('invalid signed source executed'))
    snapshot = {p.relative_to(app): p.read_bytes() for p in app.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        image.build_installer_image(output=tmp_path / 'output', signed_app=app, required_publisher_policy=POLICY)
    assert not (tmp_path / 'output').exists()
    assert {p.relative_to(app): p.read_bytes() for p in app.rglob('*') if p.is_file()} == snapshot


@pytest.mark.parametrize('tampered_mount', [False, True])
def test_signed_image_rechecks_delivered_bytes_and_uses_current_runtime_receipt(
        current_transition, monkeypatch, tmp_path, tampered_mount):
    _, app, _ = current_transition
    inert_publisher_seam(monkeypatch)
    build_platform(monkeypatch)
    observed = []
    source = [None]
    def hdiutil(argv, **kwargs):
        assert argv[0] == '/usr/bin/hdiutil'
        observed.append(argv[1])
        if argv[1] == 'create':
            source[0] = Path(argv[argv.index('-srcfolder') + 1])
            Path(argv[-1]).write_bytes(b'INERT IMAGE TOOL SEAM')
        elif argv[1] == 'attach':
            assert '-readonly' in argv and '-nobrowse' in argv
            mount = Path(argv[argv.index('-mountpoint') + 1])
            delivered = mount / app.name
            shutil.copytree(source[0] / app.name, delivered)
            if tampered_mount:
                (delivered / 'Contents/Resources/runtime/bin/python').write_bytes(b'WRONG MOUNT BYTES')
            return SimpleNamespace(stdout=plistlib.dumps({'system-entities': [{'mount-point': str(mount)}]}))
        return SimpleNamespace(stdout=b'')
    monkeypatch.setattr(image.subprocess, 'run', hdiutil)
    output = tmp_path / 'image-output'
    if tampered_mount:
        with pytest.raises(ValueError):
            image.build_installer_image(output=output, signed_app=app, required_publisher_policy=POLICY)
        assert not output.exists()
    else:
        result = image.build_installer_image(output=output, signed_app=app, required_publisher_policy=POLICY)
        current = signed_payload.read_current_payload(app, required_publisher_policy=POLICY)
        original = json.loads((app / 'Contents/Resources/runtime/release-runtime-manifest.json').read_text())
        assert result['runtime_sha256'] == current['runtime']['runtime_sha256'] != original['runtime_sha256']
        assert result['signing'] == 'static_publisher_verified'
        assert result['consumer_admission'] == 'NOT_ADMITTED'
        assert result['certification'] == 'NOT_CERTIFIED' and result['notarization'] == 'not_performed'
        assert json.loads((output / image.INSTALLER_RECEIPT).read_text()) == result
        assert set(p.name for p in output.iterdir()) == {image.IMAGE_NAME, image.INSTALLER_RECEIPT}
    assert observed == ['create', 'verify', 'attach', 'detach']


@pytest.mark.parametrize('arguments', [[], ['--distribution', 'u', '--signed-app', 's'],
    ['--signed-app', 's'], ['--signed-app', 's', '--team-id', 'SYNTHETIC1'],
    ['--distribution', 'u', '--team-id', 'SYNTHETIC1']])
def test_build_cli_refuses_ambiguous_or_unscoped_policy(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(image, 'build_installer_image', lambda *a, **k: pytest.fail('invalid build reached image'))
    with pytest.raises(SystemExit) as failure:
        build_macos_installer.main([*arguments, '--output', str(tmp_path / 'out')])
    assert failure.value.code == 2


def test_build_cli_forwards_only_explicit_operator_policy(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(image, 'build_installer_image',
                        lambda *a, **k: calls.append((a, k)) or {'ok': True})
    assert build_macos_installer.main(['--signed-app', 's', '--output', 'o',
        '--team-id', 'SYNTHETIC1', '--bundle-id', consumer.BUNDLE_ID]) == 0
    assert calls == [((), {'output': Path('o'), 'signed_app': Path('s'),
                           'required_publisher_policy': POLICY})]
    assert publisher_policy.BUILT_IN_PUBLISHER_POLICY is None
