from __future__ import annotations

import os
import shutil
import sys
import venv
from pathlib import Path

import pytest

from executor.autonomy.consumer import (APP_NAME, _candidate_starts, install_macos_app,
                                        rollback_macos_app)
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.release import copy_source_candidate, source_manifest, verify_runtime_candidate
from executor.autonomy.standalone_runtime import (
    copy_standalone_runtime_candidate, verify_standalone_runtime,
)


def release(tmp_path):
    source = Path(__file__).resolve().parents[1]
    repo = tmp_path / "checkout"
    copy_source_candidate(source, repo)
    return repo


def test_standalone_snapshot_refuses_host_virtualenv_before_copy(tmp_path):
    repo = release(tmp_path)
    source = tmp_path / "host-venv"
    venv.EnvBuilder(with_pip=False).create(source)
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="standalone_runtime_source_invalid"):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()
    assert (source / "pyvenv.cfg").is_file()


@pytest.mark.parametrize("defect", ["private-file", "directory", "fifo", "loop"])
def test_standalone_snapshot_refuses_aliases_and_nonregular_payload(
    tmp_path, defect
):
    repo = release(tmp_path)
    source = tmp_path / "runtime-source"
    (source / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, source / "bin" / "python")
    private = tmp_path / "private"
    private.mkdir()
    canary = private / "key"
    canary.write_text("CANARY_PRIVATE_KEY", encoding="utf-8")
    if defect == "private-file":
        (source / "alias").symlink_to(canary)
    elif defect == "directory":
        (source / "alias").symlink_to(private, target_is_directory=True)
    elif defect == "loop":
        (source / "alias").symlink_to(source / "alias")
    else:
        os.mkfifo(source / "pipe")
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="standalone_runtime_"):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()
    assert canary.read_text() == "CANARY_PRIVATE_KEY"


def test_copied_host_interpreter_is_not_standalone_provenance(tmp_path):
    repo = release(tmp_path)
    source = tmp_path / "runtime-source"
    (source / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, source / "bin" / "python")
    target = tmp_path / "candidate"
    with pytest.raises(ValueError, match="standalone_runtime_provenance_failed"):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()


def test_real_standalone_app_preserves_journal_and_runs_after_build_sources_removed(
    tmp_path, monkeypatch
):
    # Required cloud input, prepared once with a fixed archive digest. Missing
    # preparation is a failed gate, never a skipped or synthetic positive test.
    source = Path(os.environ["JAE_STANDALONE_RUNTIME"])
    assert source.is_dir()
    repo = release(tmp_path)
    prepared = tmp_path / "prepared-runtime"
    copy_standalone_runtime_candidate(source, prepared, repo)
    state = tmp_path / "private-state"
    queue = TaskQueue(state)
    task = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
        target_url="https://example.invalid/jobs/standalone-fixture",
        profile_ref="synthetic-profile.json"))
    before = queue.get(task["task_id"])
    apps = tmp_path / "Applications"
    result = install_macos_app(repo, destination=apps, platform="darwin",
        task_state_root=state, standalone_runtime=prepared)
    assert result["ok"] is True, result
    app = apps / (APP_NAME + ".app")
    owned = app / "Contents" / "Resources" / "runtime"
    app_source = app / "Contents" / "Resources" / "release"
    assert not (owned / "pyvenv.cfg").exists()
    assert verify_standalone_runtime(owned, app_source)
    assert queue.get(task["task_id"]) == before
    repo.rename(tmp_path / "checkout-removed")
    prepared.rename(tmp_path / "prepared-runtime-removed")
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "sitecustomize.py").write_text("raise RuntimeError('unowned startup')")
    monkeypatch.setenv("PYTHONHOME", str(poison))
    monkeypatch.setenv("PYTHONPATH", str(poison))
    assert verify_standalone_runtime(owned, app_source)
    assert _candidate_starts(owned / "bin" / "python", app_source)
    assert verify_runtime_candidate(owned, app_source)
    assert queue.get(task["task_id"]) == before
    first_identity = source_manifest(app_source)
    # Exercise the same complete runtime in a real second activation and the
    # production rollback transaction, rather than a mocked health positive.
    replacement = tmp_path / "replacement-checkout"
    copy_source_candidate(Path(__file__).resolve().parents[1], replacement)
    initializer = replacement / "executor" / "__init__.py"
    initializer.write_text(initializer.read_text() + "\n# replacement fixture\n")
    updated = install_macos_app(replacement, destination=apps, platform="darwin",
        task_state_root=state, standalone_runtime=source)
    assert updated["ok"] is True and updated["replaced"] is True, updated
    assert source_manifest(app_source) != first_identity
    assert verify_standalone_runtime(owned, app_source)
    assert queue.get(task["task_id"]) == before
    restored = rollback_macos_app(apps, task_state_root=state)
    assert restored["ok"] is True and restored["restored"] is True, restored
    assert source_manifest(app_source) == first_identity
    assert verify_standalone_runtime(owned, app_source)
    assert _candidate_starts(owned / "bin" / "python", app_source)
    failed = apps / ("." + APP_NAME + ".app.failed")
    assert failed.is_dir()
    assert verify_runtime_candidate(failed / "Contents" / "Resources" / "runtime",
        failed / "Contents" / "Resources" / "release")
    assert queue.get(task["task_id"]) == before
    # A complete runtime checksum alone is not base/stdlib independence.
    marker = owned / "release-standalone-runtime.json"
    marker.write_text('{"format":"tampered"}')
    assert not verify_standalone_runtime(owned, app_source)
    assert not _candidate_starts(owned / "bin" / "python", app_source)
    assert queue.get(task["task_id"]) == before


def test_installer_cli_routes_explicit_standalone_runtime_without_other_effects(
    tmp_path, monkeypatch, capsys
):
    from executor.autonomy import cli, consumer

    calls = []
    runtime = tmp_path / "prepared-runtime"
    monkeypatch.setattr(consumer, "install_macos_app",
        lambda repo, **kwargs: calls.append((repo, kwargs)) or {"ok": True})
    monkeypatch.setattr(sys, "argv", ["executor", "install-app",
        "--standalone-runtime", str(runtime)])
    cli.main()
    assert len(calls) == 1
    assert calls[0][1] == {"standalone_runtime": runtime}
    assert '"ok": true' in capsys.readouterr().out.lower()


@pytest.mark.parametrize("placement", ["direct", "missing-parents", "ancestor-alias"])
def test_standalone_snapshot_refuses_recursive_destination_before_any_write(
    tmp_path, placement
):
    repo = release(tmp_path)
    source = tmp_path / "runtime-source"
    (source / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, source / "bin" / "python")
    (source / "dependency-provenance.json").write_text(
        '{"fixture":"owned-source-unchanged"}\n', encoding="utf-8")
    if placement == "direct":
        target = source / "candidate"
    elif placement == "missing-parents":
        target = source / "not-created" / "deep" / "candidate"
    else:
        alias = tmp_path / "runtime-source-alias"
        alias.symlink_to(source, target_is_directory=True)
        target = alias / "candidate"
    before = {
        path.relative_to(source).as_posix(): (
            "directory" if path.is_dir() else path.read_bytes()
        ) for path in source.rglob("*")
    }
    release_before = source_manifest(repo)
    with pytest.raises(
        ValueError, match="standalone_runtime_destination_overlaps_source"
    ):
        copy_standalone_runtime_candidate(source, target, repo)
    assert not target.exists()
    assert {
        path.relative_to(source).as_posix(): (
            "directory" if path.is_dir() else path.read_bytes()
        ) for path in source.rglob("*")
    } == before
    assert source_manifest(repo) == release_before
    if placement == "ancestor-alias":
        assert alias.is_symlink() and alias.resolve() == source.resolve()


def test_actual_loaded_runtime_provenance_reports_owned_process_and_refuses_contamination(
    tmp_path, monkeypatch
):
    """Real isolated app Python; no mocked positive provenance or smaller runtime."""
    import json
    import subprocess

    source = Path(os.environ["JAE_STANDALONE_RUNTIME"])
    assert source.is_dir()
    app = tmp_path / "LoadedProcess.app"
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True)
    repo = resources / "release"
    copy_source_candidate(Path(__file__).resolve().parents[1], repo)
    runtime = resources / "runtime"
    copy_standalone_runtime_candidate(source, runtime, repo)
    assert verify_runtime_candidate(runtime, repo)
    private = tmp_path / "PRIVATE_PROCESS_ORIGIN"
    private.mkdir()
    (private / "not-imported.py").write_text("PRIVATE_CREDENTIAL_CANARY")
    private_before = (private / "not-imported.py").read_bytes()
    (private / "synthetic-import.py").write_text("VALUE = 42\n", encoding="utf-8")
    (private / "synthetic_namespace").mkdir()
    # Real imported product/dependencies; an explicit diagnostic does not load
    # a provider, credential process, browser driver or applicant journal.
    script = r"""import json,pathlib,sys,types
release=pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0,str(release))
from executor.autonomy import diagnostics
from executor.autonomy.release import read_release_identity
from executor.autonomy.runtime_provenance import loaded_process_provenance
import executor.resolver as resolver
resolver._keychain_key=lambda *_a,**_k: (_ for _ in ()).throw(AssertionError("no credential read"))
mode=sys.argv[2]
private=pathlib.Path(sys.argv[3]).resolve()
identity=read_release_identity(release)
restore=None
if mode=="external_module":
    injected=types.ModuleType("synthetic_external_loaded")
    injected.__file__=str(private/"not-imported.py")
    sys.modules[injected.__name__]=injected
elif mode in ("masked_loader_origin","missing_advertised_origin"):
    import importlib.util
    spec=importlib.util.spec_from_file_location("synthetic_loader_import",private/"synthetic-import.py")
    injected=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=injected
    spec.loader.exec_module(injected)
    assert injected.VALUE==42
    if mode=="masked_loader_origin":
        injected.__file__=str(release/"executor"/"__init__.py")
    else:
        del injected.__file__
elif mode=="masked_namespace_spec":
    import importlib
    sys.path.append(str(private))
    try:
        injected=importlib.import_module("synthetic_namespace")
    finally:
        sys.path.pop()
    # Preserve real loader-observed search locations independently of the
    # subsequently mutable advertised namespace path.
    observed=tuple(injected.__spec__.submodule_search_locations)
    assert observed==(str(private/"synthetic_namespace"),)
    injected.__spec__.submodule_search_locations=list(observed)
    injected.__path__=[str(release/"executor")]
elif mode=="external_search":
    sys.path.append(str(private))
elif mode=="empty_search":
    sys.path.append("")
elif mode=="namespace":
    injected=types.ModuleType("synthetic_external_namespace")
    injected.__path__=[str(private)]
    sys.modules[injected.__name__]=injected
elif mode=="startup_drift":
    identity={"status":"verified","source_sha256":"0"*64}
elif mode=="dependency_payload":
    import packaging
    target=pathlib.Path(packaging.__file__)
    restore=(target,target.read_bytes())
    target.write_bytes(restore[1]+b"\n# synthetic dependency payload drift\n")
try:
    report=loaded_process_provenance(release,source_identity=identity)
    print(json.dumps(report,sort_keys=True))
finally:
    if restore:
        restore[0].write_bytes(restore[1])
"""
    # Preserve the complete fixture once: all original eight cold processes plus
    # three real loaded-origin regressions, without another runtime copy/build.
    results = {}
    modes = ("owned", "external_module", "external_search", "empty_search",
             "namespace", "startup_drift", "dependency_payload", "nonisolated",
             "masked_loader_origin", "missing_advertised_origin", "masked_namespace_spec")
    for mode in modes:
        command = [str(runtime / "bin" / "python")]
        if mode != "nonisolated":
            command.append("-I")
        command += ["-B", "-c", script, str(repo), mode, str(private)]
        # Inherited Python startup configuration is explicitly poisoned for
        # isolated positives/refusals. Nonisolated case gets a clean environment
        # solely to reach the diagnostic, which must still deny isolation.
        env = dict(os.environ)
        if mode != "nonisolated":
            env.update(PYTHONHOME=str(private), PYTHONPATH=str(private))
        else:
            env.pop("PYTHONHOME", None)
            env.pop("PYTHONPATH", None)
        observed = subprocess.run(command, cwd=private, env=env, check=True,
                                  capture_output=True, text=True, timeout=30)
        assert observed.stderr == ""
        result = json.loads(observed.stdout)
        assert result["verification_scope"] == "current_process_origins_and_dependency_lock"
        assert result["signing_certified"] is False
        assert result["live_readiness_certified"] is False
        assert str(tmp_path) not in observed.stdout and "PRIVATE" not in observed.stdout
        results[mode] = result
    positive = results["owned"]
    assert positive["status"] == "verified"
    for key in ("isolated_interpreter", "interpreter_owned", "stdlib_owned",
                "search_paths_owned", "loaded_modules_owned",
                "dependency_lock_matches", "source_matches_startup"):
        assert positive[key] is True
    for mode in modes[1:]:
        assert results[mode]["status"] == "unverified", (mode, results[mode])
    assert results["external_module"]["loaded_modules_owned"] is False
    assert results["namespace"]["loaded_modules_owned"] is False
    assert results["external_search"]["search_paths_owned"] is False
    assert results["empty_search"]["search_paths_owned"] is False
    assert results["startup_drift"]["source_matches_startup"] is False
    assert results["dependency_payload"]["dependency_lock_matches"] is False
    assert results["nonisolated"]["isolated_interpreter"] is False
    for mode in ("masked_loader_origin", "missing_advertised_origin", "masked_namespace_spec"):
        assert results[mode]["loaded_modules_owned"] is False
    assert verify_runtime_candidate(runtime, repo)
    assert (private / "not-imported.py").read_bytes() == private_before
    assert not any(path.name in {"tasks.sqlite3", "auth.token", "service.json"}
                   for path in resources.rglob("*"))



@pytest.mark.parametrize("defect", [
    "owned", "no_advertised", "builtin", "frozen", "namespace_owned",
    "foreign_file", "foreign_origin", "missing_file_foreign_origin",
    "relative_origin", "empty_origin", "foreign_namespace", "foreign_spec_namespace",
    "builtin_wrong_loader", "frozen_wrong_loader", "malformed_file",
    "malformed_origin", "malformed_namespace", "malformed_spec_namespace",
])
def test_module_origin_contract_checks_advertised_and_loader_metadata_independently(
    tmp_path, defect
):
    """Pure metadata boundary, separate from the real-runtime positive above."""
    import types
    from importlib.machinery import BuiltinImporter, FrozenImporter, ModuleSpec
    from executor.autonomy.runtime_provenance import _module_origins_owned

    owned = tmp_path / "owned"
    foreign = tmp_path / "PRIVATE_FOREIGN"
    owned.mkdir()
    foreign.mkdir()
    module = types.ModuleType("synthetic_origin")
    path = str(owned / "module.py")
    module.__file__ = path
    module.__spec__ = ModuleSpec(module.__name__, loader=None, origin=path)
    positive = defect in {"owned", "no_advertised", "builtin", "frozen", "namespace_owned"}
    if defect == "no_advertised":
        del module.__file__
    elif defect in {"builtin", "frozen", "builtin_wrong_loader", "frozen_wrong_loader"}:
        del module.__file__
        frozen = defect.startswith("frozen")
        module.__spec__.origin = "frozen" if frozen else "built-in"
        module.__spec__.loader = (
            FrozenImporter if frozen else BuiltinImporter
        ) if positive else object()
    elif defect == "namespace_owned":
        del module.__file__
        module.__path__ = [str(owned)]
        module.__spec__.origin = None
        module.__spec__.submodule_search_locations = [str(owned)]
    elif defect == "foreign_file":
        module.__file__ = str(foreign / "module.py")
    elif defect in {"foreign_origin", "missing_file_foreign_origin"}:
        module.__spec__.origin = str(foreign / "module.py")
        if defect == "missing_file_foreign_origin":
            del module.__file__
    elif defect == "relative_origin":
        module.__spec__.origin = "relative.py"
    elif defect == "empty_origin":
        module.__spec__.origin = ""
    elif defect == "foreign_namespace":
        module.__path__ = [str(foreign)]
    elif defect == "foreign_spec_namespace":
        module.__path__ = [str(owned)]
        module.__spec__.submodule_search_locations = [str(foreign)]
    elif defect == "malformed_file":
        module.__file__ = 42
    elif defect == "malformed_origin":
        module.__spec__.origin = 42
    elif defect == "malformed_namespace":
        module.__path__ = [42]
    elif defect == "malformed_spec_namespace":
        module.__spec__.submodule_search_locations = [42]

    observations = []
    def inside(value):
        observations.append(value)
        return isinstance(value, str) and Path(value).is_absolute() and Path(value).is_relative_to(owned)

    assert _module_origins_owned(module, inside) is positive
    if defect in {"foreign_origin", "missing_file_foreign_origin", "foreign_spec_namespace"}:
        assert any(isinstance(value, str) and "PRIVATE_FOREIGN" in value for value in observations)
    assert list(foreign.iterdir()) == []
