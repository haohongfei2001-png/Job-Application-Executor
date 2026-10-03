"""Produce one unsigned Mac installer image from an admitted distribution.

No release upload, enrollment, signing, owner installation or runtime download.
The image contains the complete app; users do not handle its inner manifests.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import struct
import tempfile


IMAGE_NAME = 'AIApplicationManager-AppleSilicon.dmg'
INSTALLER_RECEIPT = 'installer-manifest.json'


def build_installer_image(distribution, output):
    from .app_distribution import stage_macos_distribution, _bundle_members, _sha256, _no_alias_path
    from .consumer import _bundle_transaction_identity, _native_packaged_launcher, _owned_bundle_text
    from .release import source_manifest, RUNTIME_MANIFEST_NAME
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('installer_build_requires_apple_silicon')
    output = Path(output).absolute()
    _no_alias_path(output)
    if output.exists() or not output.parent.is_dir():
        raise ValueError('installer_output_unavailable')
    # Full existing archive admission remains mandatory; no unsigned signature
    # envelope is admitted by broadening the declared inventory.
    with stage_macos_distribution(distribution) as app:
        identity = _bundle_transaction_identity(app)
        if (identity is None or _owned_bundle_text(app / 'Contents/MacOS/AIApplicationManager')
                != _native_packaged_launcher()):
            raise ValueError('installer_entry_unavailable')
        for relative in ('Contents/Resources/runtime/bin/python',
                         'Contents/Resources/native-host/AIApplicationWindow'):
            with (app / relative).open('rb') as handle:
                header = handle.read(8)
            if len(header) != 8 or struct.unpack('<II', header) != (0xFEEDFACF, 0x0100000C):
                raise ValueError('installer_payload_platform_invalid')
        members = _bundle_members(app)
        source = source_manifest(app / 'Contents/Resources/release')
        runtime = json.loads(_owned_bundle_text(app / 'Contents/Resources/runtime' / RUNTIME_MANIFEST_NAME))
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
                _bundle_members(delivered)
                delivered_identity = _bundle_transaction_identity(delivered)
                if delivered_identity is None or delivered_identity[2] != identity[2]:
                    raise ValueError('installer_image_payload_changed')
            finally:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=60, check=True)
            if (_bundle_transaction_identity(app) != identity
                    or _bundle_members(app) != members or set(app.parent.iterdir()) != {app}):
                raise ValueError('installer_source_changed')
            receipt = {'format': 'jae-macos-installer-v1', 'image': IMAGE_NAME,
                'image_sha256': _sha256(image), 'source_sha256': source['source_sha256'],
                'runtime_sha256': runtime['runtime_sha256'], 'architecture': 'arm64',
                'minimum_macos': '13.0', 'signing': 'unsigned', 'notarization': 'not_performed',
                'certification': 'NOT_CERTIFIED', 'task_state': 'excluded',
                'installation': 'explicit_native_user_choice'}
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
