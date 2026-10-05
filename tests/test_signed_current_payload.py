"""Synthetic contract seams only; never a Developer ID or consumer PASS."""
from __future__ import annotations

import hashlib
import ast
import builtins
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_macos_signing_preparation import artifact, POLICY
from test_macos_signing_finalization import prepared, synthetic_transition
from scripts import finalize_macos_signing as finalization, prepare_macos_signing as preparation
from executor.autonomy import consumer, release, macos_host, signed_payload, publisher_policy
from executor.autonomy import standalone_runtime, runtime_provenance


@pytest.fixture
def current_transition(synthetic_transition):
    prepared, calls, bridge = synthetic_transition
    dist, workspace, identity, kwargs = prepared
    app = workspace / identity['app_name']
    shutil.rmtree(app / finalization.SIGNATURE_DIRECTORY)
    original_records = {p.relative_to(app).as_posix(): p.read_bytes() for p in app.rglob('RECORD')}
    original_runtime = (app / 'Contents/Resources/runtime/release-runtime-manifest.json').read_bytes()
    original_native = (app / 'Contents/Resources/native-host/native-host-manifest.json').read_bytes()
    result = finalization.prepare_current_signed_payload(dist, workspace, **kwargs)
    assert result['phase'] == 'NESTED_SIGNED_CURRENT_PAYLOAD'
    assert result['consumer_admission'] == 'NOT_ADMITTED'
    assert not (app / finalization.SIGNATURE_DIRECTORY).exists()
    assert (app / 'Contents/Resources/runtime/release-runtime-manifest.json').read_bytes() == original_runtime
    assert (app / 'Contents/Resources/native-host/native-host-manifest.json').read_bytes() == original_native
    assert all((app / name).read_bytes() == data for name, data in original_records.items())
    directory = app / finalization.SIGNATURE_DIRECTORY
    directory.mkdir(mode=0o755)
    for name in finalization.SIGNATURE_FILES:
        (app / name).write_bytes(b'INERT SYNTHETIC ENVELOPE')
        (app / name).chmod(0o644)
    entries = {name.removeprefix('Contents/'): {'hash2': hashlib.sha256((app / name).read_bytes()).digest()}
               for name in (preparation.BRIDGE, signed_payload.RELATIVE_PATH)}
    (directory / 'CodeResources').write_bytes(plistlib.dumps({'files2': entries}))
    return prepared, app, calls


def inert_publisher_seam(monkeypatch):
    """Assert external policy forwarding, but do not claim Apple authentication."""
    calls = []
    def boundary(app, policy=None):
        accepted = publisher_policy.resolve_policy(policy)
        assert accepted == POLICY
        calls.append(app)
        return accepted
    monkeypatch.setattr(signed_payload, 'verify_publisher', boundary)
    return calls


def paths(app):
    resources = app / 'Contents/Resources'
    return resources / 'runtime', resources / 'release', resources / 'native-host'


def test_v3_build_path_and_original_v2_are_distinct(current_transition):
    prepared, app, _ = current_transition
    dist, workspace, _, kwargs = prepared
    with pytest.raises(ValueError, match='signing_output_layout_unsupported'):
        finalization.finalize_signing_workspace(dist, workspace, **kwargs)
    assert not (workspace / finalization.FINAL_IDENTITY).exists()
    result = finalization.finalize_current_signed_payload(dist, workspace, **kwargs)
    assert result['format'] == 'jae-build-bundle-identity-v3'
    assert result['current_payload_coverage']['path'] == signed_payload.RELATIVE_PATH
    assert result['consumer_admission'] == 'NOT_ADMITTED'
    assert (workspace / finalization.CURRENT_FINAL_IDENTITY).exists()
    assert not consumer._trusted_bundle(app)
    assert consumer._bundle_transaction_identity(app) is None


def test_default_refusal_precedes_any_candidate_execution(current_transition, monkeypatch, tmp_path):
    _, app, _ = current_transition
    runtime, source, native = paths(app)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('candidate must not execute'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('no configured publisher'))
    assert not release.verify_runtime_candidate(runtime, source)
    assert not macos_host.verify_native_host(native)
    assert not standalone_runtime.verify_standalone_runtime(runtime, source)
    assert not consumer._candidate_starts(runtime / 'bin/python', source)
    assert not consumer.install_macos_bundle(app, platform='darwin',
        destination=tmp_path / 'Applications', task_state_root=tmp_path / 'state')['ok']
    assert not (tmp_path / 'Applications').exists()
    assert not (tmp_path / 'state').exists()


@pytest.mark.parametrize('policy', [None, {}, {**POLICY, 'extra': True},
    {**POLICY, 'certificate_kind': 'Apple Development'}, {**POLICY, 'team_id': '-bad'}])
def test_policy_never_inferred_from_candidate_or_environment(current_transition, monkeypatch, policy):
    _, app, _ = current_transition
    monkeypatch.setenv('JAE_PUBLISHER_TEAM_ID', POLICY['team_id'])
    monkeypatch.setenv('JAE_PUBLISHER_POLICY', json.dumps(POLICY))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('bad policy before OS'))
    with pytest.raises(ValueError, match='publisher_policy_required'):
        signed_payload.read_current_payload(app, required_publisher_policy=policy)


def test_independent_requirements_sent_to_real_verifier_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(publisher_policy, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: calls.append((a, k)) or SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match='publisher_refused'):
        publisher_policy.verify_publisher(tmp_path / 'app', POLICY)
    argv = calls[0][0][0]
    assert argv[:5] == ['/usr/bin/codesign', '--verify', '--strict', '--all-architectures', '--test-requirement']
    assert 'certificate leaf[subject.OU] = "SYNTHETIC1"' in argv[5]
    assert '1.2.840.113635.100.6.1.13' in argv[5]
    assert calls[0][1]['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}


def test_postverifier_seam_connects_existing_cold_readers(current_transition, monkeypatch):
    _, app, _ = current_transition
    calls = inert_publisher_seam(monkeypatch)
    runtime, source, native = paths(app)
    original = json.loads((runtime / release.RUNTIME_MANIFEST_NAME).read_text())
    assert original != release.runtime_manifest(runtime, source)
    current = release.read_runtime_candidate(runtime, source, required_publisher_policy=POLICY)
    assert current == release.runtime_manifest(runtime, source, include_manifest=True)
    assert release.RUNTIME_MANIFEST_NAME in {entry['path'] for entry in current['files']}
    assert macos_host.verify_native_host(native,
        expected_source_sha256=hashlib.sha256(b'SYNTHETIC HOST SOURCE').hexdigest(),
        required_publisher_policy=POLICY)
    assert not macos_host.verify_native_host(native, expected_source_sha256='0' * 64,
        required_publisher_policy=POLICY)
    assert consumer._trusted_bundle(app, required_publisher_policy=POLICY)
    identity = consumer._bundle_transaction_identity(app, required_publisher_policy=POLICY)
    assert signed_payload.RELATIVE_PATH in {entry[0] for entry in identity[2]}
    report = runtime_provenance.packaged_provenance(source, required_publisher_policy=POLICY)
    assert report['runtime_sha256'] == current['runtime_sha256'] != original['runtime_sha256']
    assert report['signed_distribution_certified'] is False
    assert calls


def test_current_dependency_bytes_keep_original_record_and_lock_checks(current_transition, monkeypatch):
    _, app, _ = current_transition
    inert_publisher_seam(monkeypatch)
    runtime, source, _ = paths(app)
    distributions = list(importlib.metadata.distributions(path=[str(runtime / 'lib/python3.12/site-packages')]))
    assert len(distributions) == 1
    distribution = distributions[0]
    assert not release._distribution_payload_owned(distribution, runtime)
    monkeypatch.setattr(release, 'sys', SimpleNamespace(prefix=str(runtime), version_info=sys.version_info))
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda name: distribution)
    assert release.installed_dependencies_match(source, required_publisher_policy=POLICY)
    assert not release.installed_dependencies_match(source)
    monkeypatch.setattr(release, 'sys', SimpleNamespace(prefix=str(runtime.parent), version_info=sys.version_info))
    assert not release.installed_dependencies_match(source, required_publisher_policy=POLICY)


@pytest.mark.parametrize('damage', ['native', 'python', 'record', 'original_runtime', 'manifest',
    'manifest_missing_coverage', 'extra_runtime_file', 'duplicate_manifest'])
def test_current_payload_damage_fails_existing_readers(current_transition, monkeypatch, damage):
    _, app, _ = current_transition
    inert_publisher_seam(monkeypatch)
    runtime, source, native = paths(app)
    target = {'native': native / 'AIApplicationWindow', 'python': runtime / 'bin/python',
        'record': next(runtime.rglob('RECORD')), 'original_runtime': runtime / release.RUNTIME_MANIFEST_NAME,
        'manifest': app / signed_payload.RELATIVE_PATH, 'extra_runtime_file': runtime / 'unplanned'}
    if damage in target:
        with target[damage].open('ab') as handle:
            handle.write(b'tamper')
    elif damage == 'manifest_missing_coverage':
        (app / 'Contents/_CodeSignature/CodeResources').write_bytes(plistlib.dumps({'files2': {}}))
    else:
        (app / signed_payload.RELATIVE_PATH).write_bytes(b'{"format":1,"format":2}')
    assert not release.verify_runtime_candidate(runtime, source, required_publisher_policy=POLICY)
    assert not macos_host.verify_native_host(native, required_publisher_policy=POLICY)
    assert not consumer._trusted_bundle(app, required_publisher_policy=POLICY)


def test_payload_mutation_during_publisher_verification_is_refused(current_transition, monkeypatch):
    _, app, _ = current_transition
    def mutation(app, policy):
        publisher_policy.resolve_policy(policy)
        path = app / signed_payload.RELATIVE_PATH
        path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(signed_payload, 'verify_publisher', mutation)
    runtime, source, _ = paths(app)
    assert not release.verify_runtime_candidate(runtime, source, required_publisher_policy=POLICY)


def test_original_unsigned_path_remains_available(artifact):
    _, app, _ = artifact
    runtime, source, _ = paths(app)
    assert release.verify_runtime_candidate(runtime, source)
    assert consumer._trusted_bundle(app)


def test_inventory_names_match_posix_relative_to_for_lexical_boundaries():
    # The helper must preserve lexical names, including unresolved '..', and
    # refuse siblings/anchor mismatches without resolving or normalizing Unicode.
    prefixes = ['', '.', '..', 'a', 'a/b', 'a/..', '/', '//', '/a', '//a', '/a/b']
    suffixes = ['', '.', '..', 'b', 'b/c', 'b/../c', 'b//./c', 'a-b',
                'AI 投递经理.app', 'caf\u00e9', 'cafe\u0301', 'with space', 'line\nbreak', r'back\slash']
    paths = {PurePosixPath(prefix) / suffix for prefix in prefixes for suffix in suffixes}
    for root in map(PurePosixPath, prefixes):
        for path in paths:
            try:
                expected = path.relative_to(root).as_posix()
            except ValueError:
                with pytest.raises(ValueError):
                    release._inventory_relative_name(path, root)
            else:
                assert release._inventory_relative_name(path, root) == expected


def test_inventory_names_never_resolve_aliases_or_read_targets(tmp_path, monkeypatch):
    root = tmp_path / 'bundle'
    root.mkdir()
    outside = tmp_path / 'private'
    outside.mkdir()
    (outside / 'canary').write_bytes(b'SYNTHETIC PRIVATE CANARY')
    alias = root / 'alias'
    alias.symlink_to(outside, target_is_directory=True)
    def no_filesystem(*args, **kwargs):
        pytest.fail('lexical inventory naming accessed the filesystem')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', no_filesystem)
        patch.setattr(Path, 'resolve', no_filesystem)
        patch.setattr(Path, 'open', no_filesystem)
        assert release._inventory_relative_name(alias / 'canary', root) == 'alias/canary'
    # Naming is not admission: the existing complete tree fence rejects aliases.
    with pytest.raises(ValueError, match='signed_payload_tree_invalid'):
        signed_payload._tree_fence(root)
    assert (outside / 'canary').read_bytes() == b'SYNTHETIC PRIVATE CANARY'


def test_inventory_naming_preserves_signed_evidence_and_every_full_read(
        current_transition, monkeypatch):
    from executor.autonomy import app_distribution
    _, app, _ = current_transition
    calls = inert_publisher_seam(monkeypatch)

    def capture():
        calls.clear()
        result = (
            signed_payload._tree_fence(app),
            signed_payload.read_current_payload(app, required_publisher_policy=POLICY),
            app_distribution._signed_bundle_members(app, required_publisher_policy=POLICY),
            consumer._bundle_transaction_identity(app, required_publisher_policy=POLICY),
        )
        assert result[-1] is not None
        return result, list(calls)

    optimized = capture()
    with monkeypatch.context() as patch:
        original_naming = lambda path, root: path.relative_to(root).as_posix()
        for module in (release, signed_payload, app_distribution):
            patch.setattr(module, '_inventory_relative_name', original_naming)
        original = capture()
    assert optimized == original
    # One explicit current read, one inventory, and all four identity reads.
    assert optimized[1] == [app] * 6


@pytest.mark.parametrize('policy', [{}, POLICY, {**POLICY, 'team_id': 'WRONGTEAM1'}])
def test_explicit_policy_cannot_downgrade_to_unsigned(artifact, policy):
    _, app, _ = artifact
    runtime, source, native = paths(app)
    assert not release.verify_runtime_candidate(runtime, source, required_publisher_policy=policy)
    assert not release.installed_dependencies_match(source, required_publisher_policy=policy)
    assert not macos_host.verify_native_host(native, required_publisher_policy=policy)
    assert not consumer._trusted_bundle(app, required_publisher_policy=policy)
    assert consumer._bundle_transaction_identity(app, required_publisher_policy=policy) is None
    assert not runtime_provenance.packaged_provenance(source, required_publisher_policy=policy)['runtime_verified_now']
    assert not consumer._candidate_starts(runtime / 'bin/python', source, required_publisher_policy=policy)


def test_publisher_refusal_precedes_third_party_import(current_transition, monkeypatch):
    _, app, _ = current_transition
    runtime, source, _ = paths(app)
    monkeypatch.setattr(release, 'sys', SimpleNamespace(prefix=str(runtime), version_info=sys.version_info))
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == 'packaging' or name.startswith('packaging.'):
            pytest.fail('third-party import before publisher authentication')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded_import)
    assert not release.installed_dependencies_match(source)


@pytest.mark.parametrize('policy', [None, {}, POLICY])
@pytest.mark.parametrize('layout', ['legacy', 'source_only'])
def test_current_marker_cannot_enter_legacy_or_source_only_branch(artifact, monkeypatch, policy, layout):
    _, app, _ = artifact
    runtime, source, native = paths(app)
    shutil.rmtree(runtime)
    shutil.rmtree(native)
    if layout == 'legacy':
        shutil.rmtree(source)
        launcher = consumer._legacy_launcher(Path('/synthetic/Job-Application-Executor'))
    else:
        launcher = consumer._packaged_launcher_v1(Path('/synthetic/Job-Application-Executor'))
    (app / 'Contents/MacOS/AIApplicationManager').write_text(launcher)
    assert consumer._trusted_bundle(app)
    (app / signed_payload.RELATIVE_PATH).write_text('{}')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('incomplete signed layout'))
    assert not consumer._trusted_bundle(app, required_publisher_policy=policy)
    assert consumer._bundle_transaction_identity(app, required_publisher_policy=policy) is None


def test_real_probe_and_health_scripts_preserve_old_candidate_function_signature(artifact, monkeypatch):
    """Execute the dependency assertions extracted from both actual scripts.

    A one-argument function models the retained 7e6f19d/older source contract;
    this is argument compatibility evidence, not candidate runtime health.
    """
    _, app, _ = artifact
    runtime, source, _ = paths(app)
    scripts = [standalone_runtime.PROBE]
    def capture(argv, **kwargs):
        scripts.append(argv[4])
        raise OSError('inert test boundary; candidate never runs')
    monkeypatch.setattr(subprocess, 'Popen', capture)
    assert not consumer._candidate_starts(runtime / 'bin/python', source)
    assert len(scripts) == 2
    for script in scripts:
        assertions = [node for node in ast.walk(ast.parse(script))
            if isinstance(node, ast.Assert) and any(isinstance(item, ast.Name)
                and item.id == 'installed_dependencies_match' for item in ast.walk(node))]
        assert len(assertions) == 1
        calls = []
        def old_version(root):
            calls.append(root)
            return True
        namespace = {'installed_dependencies_match': old_version, 'policy': None,
                     'release': source, 'root': source}
        exec(compile(ast.fix_missing_locations(ast.Module(body=assertions, type_ignores=[])),
                     '<actual dependency assertion>', 'exec'), namespace)
        assert calls == [source]


def test_unsigned_release_reader_needs_no_signed_helper_modules(tmp_path):
    """The retained release-only test/bootstrap contract remains executable."""
    repo = tmp_path / 'unsigned-source'
    repo.mkdir()
    module = repo / 'release.py'
    module.write_bytes(Path(release.__file__).read_bytes())
    (repo / 'requirements.txt').write_bytes((Path(__file__).parents[1] / 'requirements.txt').read_bytes())
    script = '''import importlib.util,json,pathlib,sys
path=pathlib.Path(sys.argv[1]); source=path.parent
spec=importlib.util.spec_from_file_location('retained_unsigned_release',path)
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
assert module.installed_dependencies_match(source)
runtime=source/'runtime'; (runtime/'bin').mkdir(parents=True)
python=runtime/'bin/python'; python.write_bytes(b'INERT'); python.chmod(0o755)
(runtime/module.RUNTIME_MANIFEST_NAME).write_text(json.dumps(module.runtime_manifest(runtime,source)))
assert module.verify_runtime_candidate(runtime,source)
assert not module.verify_runtime_candidate(runtime,source,required_publisher_policy={})
print('unsigned release-only compatibility verified')
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(module)],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'unsigned release-only compatibility verified'


def test_preparation_rejects_original_payload_tamper(synthetic_transition):
    prepared, _, _ = synthetic_transition
    dist, workspace, identity, kwargs = prepared
    app = workspace / identity['app_name']
    shutil.rmtree(app / finalization.SIGNATURE_DIRECTORY)
    next(app.rglob('RECORD')).write_bytes(b'forged input')
    with pytest.raises(ValueError, match='original_payload_changed'):
        finalization.prepare_current_signed_payload(dist, workspace, **kwargs)
    assert not (app / signed_payload.RELATIVE_PATH).exists()


@pytest.mark.parametrize('refuse_at', ['first', 'last'])
def test_actual_prepare_requires_every_nested_publisher_before_current_write(
        synthetic_transition, monkeypatch, refuse_at):
    """Entry wiring only; the Mac oracle separately exercises Apple's refusal."""
    prepared, _, bridge = synthetic_transition
    dist, workspace, identity, kwargs = prepared
    app = workspace / identity['app_name']
    shutil.rmtree(app / finalization.SIGNATURE_DIRECTORY)
    calls = []
    refused = 1 if refuse_at == 'first' else len(bridge['signing_order'])
    def reject_nested(path, policy, identifier, deadline):
        planned = bridge['signing_order'][len(calls)]
        assert path == app / planned['path'] and policy == POLICY
        assert identifier == planned['required_identifier']
        calls.append(path)
        if len(calls) == refused:
            raise ValueError('test_nested_publisher_refused')
    monkeypatch.setattr(finalization, '_verify_static', reject_nested)
    monkeypatch.setattr(signed_payload, 'current_payload',
        lambda *a, **k: pytest.fail('current payload before every publisher verified'))
    monkeypatch.setattr(preparation, '_write_at',
        lambda *a, **k: pytest.fail('output write after publisher refusal'))
    with pytest.raises(ValueError, match='test_nested_publisher_refused'):
        finalization.prepare_current_signed_payload(dist, workspace, **kwargs)
    assert len(calls) == refused
    assert not (app / signed_payload.RELATIVE_PATH).exists()
    assert not (workspace / finalization.CURRENT_FINAL_IDENTITY).exists()
