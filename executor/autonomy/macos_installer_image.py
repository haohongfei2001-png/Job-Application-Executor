"""Produce one Mac installer image from an admitted unsigned or signed app.

No release upload, enrollment, signing, owner installation or runtime download.
The image contains the complete app; users do not handle its inner manifests.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import struct
import tempfile


IMAGE_NAME = 'AIApplicationManager-AppleSilicon.dmg'
INSTALLER_RECEIPT = 'installer-manifest.json'


@contextmanager
def _image_source(distribution, signed_app, policy, *, parent=None):
    """Keep archive intake unchanged; signed input is copied, never repackaged."""
    from .app_distribution import stage_macos_distribution, _signed_bundle_members, _no_alias_path
    from .bundle_copy import copy_bundle_payload
    from .consumer import APP_NAME, _bundle_transaction_identity
    if signed_app is None:
        with stage_macos_distribution(distribution) as app:
            yield app
        return
    original = Path(signed_app).absolute()
    _no_alias_path(original)
    options = {'required_publisher_policy': policy}
    _signed_bundle_members(original, **options)
    identity = _bundle_transaction_identity(original, **options)
    if identity is None:
        raise ValueError('installer_entry_unavailable')
    with tempfile.TemporaryDirectory(prefix='jae-signed-installer-', dir=parent) as scratch:
        app = Path(scratch).resolve(strict=True) / (APP_NAME + '.app')
        app.mkdir(mode=0o700)
        entry = app.lstat()
        copy_bundle_payload(original, app, identity,
            expected_target_identity=(entry.st_dev, entry.st_ino), **options)
        yield app
        if (_bundle_transaction_identity(original, **options) != identity):
            raise ValueError('installer_source_changed')
        _signed_bundle_members(original, **options)


def build_installer_image(distribution=None, output=None, *, signed_app=None,
                          required_publisher_policy=None):
    from .app_distribution import _bundle_members, _signed_bundle_members, _sha256, _no_alias_path
    from .consumer import _bundle_transaction_identity, _native_packaged_launcher, _owned_bundle_text
    from .release import source_manifest, RUNTIME_MANIFEST_NAME
    from .consumer_installer import bundle_release_version
    if (distribution is None) == (signed_app is None) or output is None:
        raise ValueError('installer_input_required')
    if signed_app is None and required_publisher_policy is not None:
        raise ValueError('installer_unsigned_policy_invalid')
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('installer_build_requires_apple_silicon')
    options = {}
    if signed_app is not None:
        from .publisher_policy import resolve_policy
        options['required_publisher_policy'] = resolve_policy(required_publisher_policy)
    inventory = _signed_bundle_members if signed_app is not None else _bundle_members
    output = Path(output).absolute()
    _no_alias_path(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('installer_output_unavailable')
    # Signed input cannot enter or broaden the unsigned archive parser.
    with _image_source(distribution, signed_app, options.get('required_publisher_policy'),
                       parent=output.parent) as app:
        identity = _bundle_transaction_identity(app, **options)
        if (identity is None or _owned_bundle_text(app / 'Contents/MacOS/AIApplicationManager')
                != _native_packaged_launcher()):
            raise ValueError('installer_entry_unavailable')
        for relative in ('Contents/Resources/runtime/bin/python',
                         'Contents/Resources/native-host/AIApplicationWindow'):
            with (app / relative).open('rb') as handle:
                header = handle.read(8)
            if len(header) != 8 or struct.unpack('<II', header) != (0xFEEDFACF, 0x0100000C):
                raise ValueError('installer_payload_platform_invalid')
        members = inventory(app, **options)
        version = bundle_release_version(app, identity)
        source = source_manifest(app / 'Contents/Resources/release')
        if signed_app is None:
            runtime = json.loads(_owned_bundle_text(app / 'Contents/Resources/runtime' / RUNTIME_MANIFEST_NAME))
        else:
            from .signed_payload import read_current_payload
            runtime = read_current_payload(app, **options)['runtime']
        if set(app.parent.iterdir()) != {app}:
            raise ValueError('installer_image_inventory_invalid')
        with tempfile.TemporaryDirectory(prefix='.jae-installer-image-', dir=output.parent) as scratch:
            work = Path(scratch).resolve(strict=True)
            image = work / IMAGE_NAME
            subprocess.run(['/usr/bin/hdiutil', 'create', '-quiet', '-format', 'UDZO',
                '-fs', 'HFS+', '-volname', 'AI 投递经理安装', '-srcfolder', str(app.parent),
                str(image)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=180, check=True)
            subprocess.run(['/usr/bin/hdiutil', 'verify', '-quiet', str(image)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=60, check=True)
            # Verify the produced image itself, not just the source directory
            # observed before/after the external image builder streamed it.
            mount = work / 'mounted'; mount.mkdir()
            attached = subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse',
                '-owners', 'off', '-mountpoint', str(mount), '-plist', str(image)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=60, check=True)
            try:
                details = plistlib.loads(attached.stdout)
                if not any(item.get('mount-point') == str(mount) for item in details['system-entities']):
                    raise ValueError('installer_image_mount_unverified')
                if set(mount.iterdir()) != {mount / app.name}:
                    raise ValueError('installer_image_inventory_invalid')
                delivered = mount / app.name
                inventory(delivered, **options)
                delivered_identity = _bundle_transaction_identity(delivered, **options)
                if delivered_identity is None or delivered_identity[2] != identity[2]:
                    raise ValueError('installer_image_payload_changed')
            finally:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=60, check=True)
            if (_bundle_transaction_identity(app, **options) != identity
                    or inventory(app, **options) != members or set(app.parent.iterdir()) != {app}):
                raise ValueError('installer_source_changed')
            receipt = {'format': 'jae-macos-installer-v1', 'image': IMAGE_NAME,
                'image_sha256': _sha256(image), 'source_sha256': source['source_sha256'],
                'runtime_sha256': runtime['runtime_sha256'], 'architecture': 'arm64',
                'bundle_version': str(version),
                'minimum_macos': '13.0',
                'signing': 'unsigned' if signed_app is None else 'static_publisher_verified',
                'notarization': 'not_performed',
                'certification': 'NOT_CERTIFIED', 'task_state': 'excluded',
                'installation': 'explicit_native_user_choice'}
            if signed_app is not None:
                receipt['consumer_admission'] = 'NOT_ADMITTED'
            output.mkdir(mode=0o700)
            # Fresh output only. An uncertain publication is retained, never
            # overwritten or represented as a completed consumer release.
            with image.open('rb') as source_file, (output / IMAGE_NAME).open('xb') as destination:
                while data := source_file.read(1024 * 1024):
                    destination.write(data)
                destination.flush()
                os.fsync(destination.fileno())
            if _sha256(output / IMAGE_NAME) != receipt['image_sha256']:
                raise ValueError('installer_image_copy_incomplete')
            with (output / INSTALLER_RECEIPT).open('x', encoding='utf-8') as handle:
                json.dump(receipt, handle, ensure_ascii=False, sort_keys=True)
                handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            return receipt
