"""One disposable ad-hoc signing experiment; never a production trust gate.

Builds only this checkout's declared source in a private synthetic HOME. Existing
archive, copy, image and install admission are observed without changing them.
No candidate code is run after signing or tampering. Only the JSON report may be
uploaded: signed bundles, archives, images and synthetic HOME remain disposable.
"""
from __future__ import annotations

import argparse
import ctypes
from functools import cache
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


MAX_FILES = 100_000
MAX_BYTES = 4 * 1024 ** 3
MACHO_MAGIC = {b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe',
               b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
               b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
               b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


@cache
def _darwin_xattr_api():
    # CPython's os.*xattr API is Linux-only. Use Apple's descriptor API instead
    # of shelling out once per bundle member or treating unavailable as empty.
    library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    library.flistxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    library.flistxattr.restype = ctypes.c_ssize_t
    library.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    library.fgetxattr.restype = ctypes.c_ssize_t
    return library


def xattrs(path):
    if sys.platform != 'darwin':
        return {name: os.getxattr(path, name, follow_symlinks=False)
                for name in sorted(os.listxattr(path, follow_symlinks=False))}
    library = _darwin_xattr_api()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        def read(call, maximum):
            size = call(None, 0)
            if size < 0:
                raise OSError(ctypes.get_errno(), 'probe_xattr_read_failed')
            if size > maximum:
                raise ValueError('probe_xattr_bound')
            buffer = ctypes.create_string_buffer(max(1, size))
            actual = call(buffer, size)
            if actual < 0:
                raise OSError(ctypes.get_errno(), 'probe_xattr_read_failed')
            if actual != size:
                raise ValueError('probe_xattr_changed')
            return buffer.raw[:actual]
        raw = read(lambda b, n: library.flistxattr(fd, b, n, 0), 65_536)
        if raw and not raw.endswith(b'\0'):
            raise ValueError('probe_xattr_names_invalid')
        names = raw[:-1].split(b'\0') if raw else []
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError('probe_xattr_names_invalid')
        return {name.decode('utf-8'): read(
            lambda b, n: library.fgetxattr(fd, name, b, n, 0, 0), 8 * 1024 ** 2)
            for name in sorted(names)}
    finally:
        os.close(fd)


def digest(path):
    hashed = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            hashed.update(block)
    return hashed.hexdigest()


def inventory(root):
    """Bounded exact bytes/modes/xattrs; no payload values enter the report."""
    root = Path(root).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('inventory_alias')
    entries, total = {}, 0
    for path in [root, *sorted(root.rglob('*'))]:
        before = path.lstat()
        if (not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode))
                or (stat.S_ISREG(before.st_mode) and before.st_nlink != 1)):
            raise ValueError('inventory_member_invalid')
        total += before.st_size if path.is_file() else 0
        if len(entries) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('inventory_bound')
        attrs = {name: hashlib.sha256(value).hexdigest() for name, value in xattrs(path).items()}
        item = {'mode': stat.S_IMODE(before.st_mode), 'xattrs': attrs,
                'type': 'file' if stat.S_ISREG(before.st_mode) else 'directory'}
        if item['type'] == 'file':
            item.update(bytes=before.st_size, sha256=digest(path))
            with path.open('rb') as handle:
                header = handle.read(8)
            item['macho'] = header[:4] in MACHO_MAGIC
        after = path.lstat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after):
            raise ValueError('inventory_changed')
        entries[path.relative_to(root).as_posix()] = item
    return entries


def inventory_digest(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def changes(before, after):
    return {'added': sorted(set(after) - set(before)),
            'removed': sorted(set(before) - set(after)),
            'changed': sorted(k for k in before.keys() & after.keys() if before[k] != after[k])}


def command(argv, *, timeout=90):
    # Fixed Apple tools only. No shell, keychain, certificate, network or payload
    # execution. Bounded disk capture avoids an unbounded PIPE allocation.
    if argv[0] not in {'/usr/bin/codesign', '/usr/bin/hdiutil', '/usr/bin/sw_vers',
                       '/usr/bin/ditto', '/usr/bin/xattr'}:
        raise ValueError('probe_tool_invalid')
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, timeout=timeout)
        output.seek(0)
        data = output.read(32_769)
    return {'returncode': result.returncode,
            'output': data[:32_768].decode('utf-8', errors='replace'),
            'truncated': len(data) > 32_768}


def seal(path, *, identifier=None):
    argv = ['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none']
    if identifier is not None:
        argv += ['--identifier', identifier]
    return command([*argv, str(path)])


def verify(path):
    return command(['/usr/bin/codesign', '--verify', '--strict', '--verbose=4', str(path)])


def observed(call):
    try:
        result = call()
        # Boolean provenance checks and controlled install results stay useful;
        # exceptions retain fixed diagnostic class/reason, never a traceback.
        return {'returned': result if isinstance(result, (bool, dict)) else True}
    except (OSError, ValueError, TypeError) as error:
        return {'refused': type(error).__name__, 'reason': str(error)[:300]}


def records(app):
    from executor.autonomy.release import _distribution_payload_owned
    runtime = app / 'Contents/Resources/runtime'
    found = list(importlib.metadata.distributions(path=[str(p) for p in runtime.glob('lib/python*/site-packages')]))
    return {str(d.metadata['Name']): _distribution_payload_owned(d, runtime) for d in found}


def production_static(app):
    from executor.autonomy.consumer import _trusted_bundle, _bundle_transaction_identity
    from executor.autonomy.release import verify_runtime_candidate, verify_source_candidate
    release = app / 'Contents/Resources/release'
    return {'trusted_unsigned_bundle': _trusted_bundle(app),
            'transaction_identity_available': _bundle_transaction_identity(app) is not None,
            'source_manifest_valid': verify_source_candidate(release),
            'runtime_manifest_valid': verify_runtime_candidate(app / 'Contents/Resources/runtime', release),
            'wheel_records': records(app)}


def raw_archive(app, archive):
    """Experiment-only control, deliberately NOT the production packager.

    Mirrors canonical bytes/modes-only transport so the real intake can report
    its refusal. Does not assert publisher trust or rewrite inner manifests.
    """
    with tarfile.open(archive, 'w:gz', compresslevel=1) as bundle:
        for path in [app, *sorted(app.rglob('*'))]:
            info = bundle.gettarinfo(str(path), arcname=str(Path(app.name) / path.relative_to(app)))
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ''
            info.pax_headers = {}
            info.mode = 0o755 if path.is_dir() or path.stat().st_mode & stat.S_IXUSR else 0o644
            if path.is_file():
                with path.open('rb') as handle:
                    bundle.addfile(info, handle)
            else:
                bundle.addfile(info)


def production_routes(app, receipt, work):
    from executor.autonomy.app_distribution import (_archive_app, _bundle_members,
        ARCHIVE_NAME, RECEIPT_NAME, stage_macos_distribution, install_macos_distribution)
    from executor.autonomy.bundle_copy import copy_bundle_payload
    from executor.autonomy.consumer import _bundle_transaction_identity
    from executor.autonomy.macos_installer_image import build_installer_image
    work.mkdir()
    target = work / 'copy'; target.mkdir()
    answer = {'inventory': observed(lambda: _bundle_members(app)),
              'archive': observed(lambda: _archive_app(app, work / 'production.tar.gz')),
              'copy': observed(lambda: copy_bundle_payload(app, target, _bundle_transaction_identity(app)))}
    fixture = work / 'CONTROL-NOT-A-DELIVERY'; fixture.mkdir()
    raw_archive(app, fixture / ARCHIVE_NAME)
    # This extraction consumes only the freshly created controlled archive.
    # It isolates tar transport from the unchanged production intake refusal.
    unpacked = work / 'raw-roundtrip'; unpacked.mkdir()
    with tarfile.open(fixture / ARCHIVE_NAME) as archive:
        archive.extractall(unpacked, filter='data')
    transported = unpacked / app.name
    answer['raw_archive_control'] = {'delta': changes(inventory(app), inventory(transported)),
                                    'verify': verify(transported)}
    control_receipt = dict(receipt, archive_sha256=digest(fixture / ARCHIVE_NAME))
    (fixture / RECEIPT_NAME).write_text(json.dumps(control_receipt))
    def intake():
        with stage_macos_distribution(fixture):
            return True
    answer['intake'] = observed(intake)
    answer['image'] = observed(lambda: build_installer_image(fixture, work / 'image'))
    # Only call the actual installer after its unchanged static intake refused.
    # If signed admission unexpectedly changes, stop instead of executing it.
    if 'refused' not in answer['intake']:
        raise ValueError('signed_intake_unexpectedly_admitted')
    answer['install'] = install_macos_distribution(fixture,
        destination=work / 'Applications', task_state_root=work / 'state')
    answer['install_created_authority'] = (work / 'Applications').exists() or (work / 'state').exists()
    if answer['install'].get('ok') is not False or answer['install_created_authority']:
        raise ValueError('signed_install_unexpected_effect')
    return answer


def byte_copy(source, target):
    # Control deliberately drops xattrs, like current production archive/copy.
    # Never use copy2, which can silently preserve metadata and hide the gap.
    shutil.copytree(source, target, copy_function=shutil.copyfile)
    for path in [source, *source.rglob('*')]:
        (target / path.relative_to(source)).chmod(stat.S_IMODE(path.stat().st_mode))


def metadata_copy(source, target):
    if target.exists() or target.is_symlink():
        raise ValueError('probe_copy_target_exists')
    before = inventory(source)
    if sys.platform == 'darwin':
        result = command(['/usr/bin/ditto', '--rsrc', '--extattr', str(source), str(target)])
        if result['returncode']:
            raise ValueError('probe_metadata_copy_failed')
    else:
        shutil.copytree(source, target, copy_function=shutil.copy2)
    after = inventory(target)
    if inventory(source) != before:
        raise ValueError('probe_copy_source_changed')
    return {'status': 'PASS_EXACT' if after == before else 'FAIL_INVENTORY_LOSS',
            'delta': changes(before, after)}


def attribute_transport_control(work):
    """Retain the observed empty-xattr failure; do not repair transport."""
    work.mkdir()
    source = work / 'source'; source.mkdir()
    leaf = source / 'synthetic'; leaf.write_bytes(b'JAE synthetic transport control')
    for name, value in (('org.jae.empty', ''), ('org.jae.nonempty', '010200ff')):
        result = command(['/usr/bin/xattr', '-w', '-x', name, value, str(leaf)])
        if result['returncode']:
            return {'status': 'INCONCLUSIVE_FIXTURE_FAILED', 'command': result}
    before = inventory(source)
    result = metadata_copy(source, work / 'copy')
    return {**result, 'before': before, 'after': inventory(work / 'copy')}


def image_roundtrip(app, work):
    work.mkdir()
    image, mount = work / 'CONTROL-NOT-A-DELIVERY.dmg', work / 'mount'
    mount.mkdir()
    made = command(['/usr/bin/hdiutil', 'create', '-quiet', '-format', 'UDZO', '-fs', 'HFS+',
                    '-volname', 'JAE ADHOC EXPERIMENT', '-srcfolder', str(app.parent), str(image)], timeout=180)
    if made['returncode']:
        return {'create': made}
    attached = command(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-owners', 'off',
                        '-mountpoint', str(mount), '-plist', str(image)])
    try:
        if attached['returncode']:
            return {'create': made, 'attach': attached}
        details = plistlib.loads(attached['output'].encode())
        if not any(v.get('mount-point') == str(mount) for v in details['system-entities']):
            raise ValueError('probe_mount_unverified')
        delivered = mount / app.name
        answer = {'create': made, 'inventory': inventory(delivered), 'verify': verify(delivered)}
        answer['image_sha256'] = digest(image)
        return answer
    finally:
        # Exact owned mount only; no force or global detach.
        if attached['returncode'] == 0:
            detached = command(['/usr/bin/hdiutil', 'detach', str(mount)])
            if detached['returncode']:
                raise ValueError('probe_detach_failed')


def tamper_cases(app, work, pinned):
    results = {}
    cases = {'source': 'Contents/Resources/release/executor/__init__.py',
             'version': 'Contents/Info.plist', 'launcher': 'Contents/MacOS/AIApplicationManager',
             'native': 'Contents/Resources/native-host/AIApplicationWindow',
             'extra_signature_entry': 'Contents/_CodeSignature/EXTRA',
             'resource': 'Contents/Resources/EXTRA'}
    signatures = [p for p in pinned if p.startswith('Contents/_CodeSignature/') and pinned[p]['type'] == 'file']
    cases.update({'signature_' + str(i): p for i, p in enumerate(signatures)})
    work.mkdir()
    # Sequential disposable copies bound disk use; only our new copies are removed.
    for name, relative in cases.items():
        target = work / app.name
        # Tamper controls must begin from the complete signed identity, including
        # xattrs. A failed transport is inconclusive, never tamper detection.
        copy_result = metadata_copy(app, target)
        try:
            baseline_matches = inventory(target) == pinned
            baseline_verify = verify(target)
            results[name] = {'copy_control': copy_result, 'baseline_inventory_matches': baseline_matches,
                             'baseline_os_verify': baseline_verify}
            if not baseline_matches or baseline_verify['returncode'] != 0:
                results[name]['status'] = 'INCONCLUSIVE_BASELINE_NOT_ESTABLISHED'
                continue
            path = target / relative
            if name == 'version':
                value = plistlib.loads(path.read_bytes()); value['CFBundleVersion'] = '999999'
                path.write_bytes(plistlib.dumps(value))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('ab') as handle:
                    handle.write(b'\nJAE SYNTHETIC TAMPER\n')
            after = inventory(target)
            results[name].update(status='MEASURED', pinned_inventory_matches=after == pinned,
                                 os_verify=verify(target))
            if after == pinned:
                raise ValueError('tamper_not_detected')
            if name == 'source':
                resigned = seal(target)
                results[name]['resigned'] = resigned
                results[name]['resigned_verify'] = verify(target)
                results[name]['resigned_inventory_matches'] = inventory(target) == pinned
        finally:
            shutil.rmtree(target)
    return results


def experiment(repo, runtime, work, report, save):
    from executor.autonomy.app_distribution import build_macos_distribution, stage_macos_distribution
    report['empty_attribute_transport_control'] = attribute_transport_control(work / 'attribute-control')
    save()
    distribution = work / 'unsigned'
    receipt = build_macos_distribution(repo, standalone_runtime=runtime,
        output_dir=distribution, native_presentation=True)
    report['unsigned_receipt'] = receipt; save()
    with stage_macos_distribution(distribution) as staged:
        base = work / 'baseline' / staged.name; base.parent.mkdir()
        shutil.copytree(staged, base)
    baseline = inventory(base)
    report['baseline'] = {'inventory': baseline, 'static': production_static(base)}; save()
    outer = work / 'outer' / base.name; outer.parent.mkdir(); shutil.copytree(base, outer)
    report['outer_seal'] = seal(outer)
    report['outer_verify'] = verify(outer)
    report['outer_inventory'] = inventory(outer)
    report['outer_delta'] = changes(baseline, report['outer_inventory']); save()
    if report['outer_seal']['returncode'] or report['outer_verify']['returncode']:
        report['decision'] = 'OUTER_ADHOC_SEAL_REFUSED_NO_PRODUCTION_CHANGE'; return
    report['outer_production_static'] = production_static(outer)
    report['outer_production_routes'] = production_routes(outer, receipt, work / 'routes')
    copied = work / 'bytes-only' / outer.name; copied.parent.mkdir(); byte_copy(outer, copied)
    copied_inventory = inventory(copied)
    report['bytes_only_transport'] = {'delta': changes(report['outer_inventory'], copied_inventory),
        'inventory_sha256': inventory_digest(copied_inventory), 'verify': verify(copied)}; save()
    report['dmg_control'] = image_roundtrip(outer, work / 'dmg')
    if 'inventory' in report['dmg_control']:
        report['dmg_control']['delta'] = changes(report['outer_inventory'], report['dmg_control']['inventory'])
    save()
    report['tamper'] = tamper_cases(outer, work / 'tamper', report['outer_inventory']); save()
    nested = work / 'inside-out' / base.name; nested.parent.mkdir(); shutil.copytree(base, nested)
    catalog = sorted((p for p, value in baseline.items() if value.get('macho')), key=lambda p: (-len(Path(p).parts), p))
    if not catalog:
        raise ValueError('probe_no_macho')
    report['nested_signatures'] = []
    for index, relative in enumerate(catalog):
        # Explicit synthetic identifiers guarantee this is not Developer ID evidence.
        signed = seal(nested / relative, identifier='org.jae.preflight.component.' + str(index))
        report['nested_signatures'].append({'path': relative, 'sign': signed, 'verify': verify(nested / relative)})
        if signed['returncode'] or report['nested_signatures'][-1]['verify']['returncode']:
            report['decision'] = 'NESTED_ADHOC_SEAL_REFUSED_NO_LAYOUT_OR_ENTITLEMENT_CHANGE'; save(); return
    report['nested_outer_seal'] = seal(nested)
    report['nested_outer_verify'] = verify(nested)
    report['nested_inventory'] = inventory(nested)
    report['nested_delta'] = changes(baseline, report['nested_inventory'])
    report['nested_production_static'] = production_static(nested)
    # Signing an inner executable after the outer seal must be treated as a
    # different release even if either individual signature is internally valid.
    if report['nested_outer_seal']['returncode'] or report['nested_outer_verify']['returncode']:
        report['late_inner_resign'] = {'status': 'INCONCLUSIVE_BASELINE_NOT_ESTABLISHED'}
    else:
        chosen = catalog[0]
        late = seal(nested / chosen, identifier='org.jae.preflight.late.component')
        report['late_inner_resign'] = {'status': 'MEASURED', 'path': chosen, 'sign': late,
            'outer_verify': verify(nested),
            'pinned_inventory_matches': inventory(nested) == report['nested_inventory']}
    report['decision'] = 'MEASURED_ONLY_SIGNED_PROVENANCE_AND_INDEPENDENT_PUBLISHER_POLICY_STILL_REQUIRED'
    save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standalone-runtime', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Apple Silicon hosted Mac required')
    repo = Path(__file__).resolve().parents[1]
    runtime = args.standalone_runtime.resolve(strict=True)
    work = args.work.absolute()
    if any(p.is_symlink() for p in (work, *work.parents)) or work.exists():
        raise SystemExit('fresh canonical work directory required')
    work.mkdir(mode=0o700)
    home, temporary = work / 'home', work / 'tmp'
    home.mkdir(mode=0o700); temporary.mkdir(mode=0o700)
    # Strip hosted job credentials before build/probe subprocesses. This process
    # only publishes a sanitized report to its already-authorized CI artifact.
    allowed = {key: os.environ[key] for key in ('PATH', 'LANG') if key in os.environ}
    allowed.update(HOME=str(home), TMPDIR=str(temporary), PYTHONDONTWRITEBYTECODE='1',
                   APPLICATION_EXECUTOR_BROWSER_MODE='isolated')
    os.environ.clear(); os.environ.update(allowed)
    tempfile.tempdir = str(temporary)
    report = {'format': 'jae-adhoc-signature-experiment-v1', 'status': 'INCOMPLETE',
        'publisher_trust': 'ABSENT_ADHOC_ONLY', 'notarization': 'NOT_PERFORMED',
        'certification': 'NOT_CERTIFIED', 'production_admission_changed': False,
        'platform': platform.platform(), 'interpreter': platform.python_version(),
        'os_version': command(['/usr/bin/sw_vers']),
        'codesign_version': command(['/usr/bin/codesign', '--version'])}
    def save():
        # Output paths refer only to repository-owned/synthetic files, sanitized
        # even in fixed-tool diagnostics. No file contents, HOME or token state.
        text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        for original, replacement in ((str(work), '$WORK'), (str(repo), '$SOURCE'), (str(runtime), '$RUNTIME')):
            text = text.replace(original, replacement)
        (work / 'report.json').write_text(text + '\n', encoding='utf-8')
    save()
    try:
        experiment(repo, runtime, work, report, save)
        report['status'] = 'OBSERVATION_COMPLETE'
        save()
    except BaseException as error:
        report['failure_type'] = type(error).__name__
        report['status'] = 'INCOMPLETE'
        save()
        raise
    print(json.dumps({k: report[k] for k in ('status', 'decision', 'publisher_trust', 'notarization')}))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
