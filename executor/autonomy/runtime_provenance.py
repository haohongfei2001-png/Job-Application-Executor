"""Cold, read-only app payload provenance shared by service and recovery.

Only stdlib and the cold release contract are imported. Current payload integrity
does not certify dependency loading, signing, a live service or consumer readiness.
"""
from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path

from .release import (is_packaged_source, read_release_identity, MANIFEST_NAME,
                      verify_runtime_candidate, read_runtime_candidate, RUNTIME_MANIFEST_NAME)


def current_packaged_source(repo_root: str | Path) -> dict:
    supplied = Path(repo_root).expanduser().absolute()
    try:
        # A damaged/aliased installed app is not permission to fall back to Git
        # or hash arbitrary host files through a replaced path component.
        if any(path.is_symlink() for path in (supplied, *supplied.parents)):
            return {"status": "unverified", "source_sha256": ""}
        manifest = supplied / MANIFEST_NAME
        if not manifest.is_file() or manifest.is_symlink():
            return {"status": "unverified", "source_sha256": ""}
        identity = read_release_identity(supplied)
        digest = identity.get("source_sha256")
        if (identity.get("status") == "verified" and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest)):
            return {"status": "verified", "source_sha256": digest}
    except (OSError, ValueError, RuntimeError, TypeError):
        pass
    return {"status": "unverified", "source_sha256": ""}


def packaged_provenance(repo_root: str | Path, *, required_publisher_policy=None) -> dict | None:
    """Current payload integrity, distinct from the process's startup identity.

    Hash only declared app source/runtime payloads. Never execute the candidate
    interpreter, consult Git, read applicant state, or call a remote service.
    The runtime manifest is an integrity receipt, not proof that a new process
    successfully loaded its dependency lock or that distribution is signed.
    """
    supplied = Path(repo_root).expanduser().absolute()
    if not is_packaged_source(supplied):
        return None
    source = current_packaged_source(supplied)
    source_ok = source["status"] == "verified"
    report = {
        "source_verified_now": source_ok,
        "source_sha256": source["source_sha256"],
        "runtime_verified_now": False,
        "runtime_sha256": "",
        "requirements_sha256": "",
        "interpreter_owned": False,
        "verification_scope": "payload_integrity_only",
        "signed_distribution_certified": False,
    }
    if not source_ok:
        return report
    # Recognize the installed sibling layout. A source-only staging manifest
    # cannot prove interpreter ownership merely by naming another host folder.
    if not (supplied.name == "release" and supplied.parent.name == "Resources"
            and supplied.parent.parent.name == "Contents"
            and supplied.parent.parent.parent.name.endswith(".app")):
        return report
    runtime = supplied.parent / "runtime"
    try:
        manifest_file = runtime / RUNTIME_MANIFEST_NAME
        if runtime.is_symlink():
            return report
        metadata = manifest_file.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            return report
        manifest_text = manifest_file.read_text(encoding="utf-8")
        from .signed_payload import has_current_payload
        if has_current_payload(supplied.parent.parent.parent):
            manifest = read_runtime_candidate(runtime, supplied, required_publisher_policy=required_publisher_policy)
            if manifest is None:
                return report
        else:
            if required_publisher_policy is not None:
                return report
            if not verify_runtime_candidate(runtime, supplied):
                return report
            manifest = json.loads(manifest_text)
        if manifest_file.read_text(encoding="utf-8") != manifest_text:
            return report
        runtime_digest = manifest.get("runtime_sha256")
        requirements_digest = manifest.get("requirements_sha256")
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in (runtime_digest, requirements_digest)):
            return report
        # Re-read source after the runtime hash: a concurrent source edit may
        # invalidate its requirement binding while the report is being built.
        if current_packaged_source(supplied) != source:
            report.update(source_verified_now=False, source_sha256="")
            return report
        root = runtime.resolve()
        report.update(
            runtime_verified_now=True, runtime_sha256=runtime_digest,
            requirements_sha256=requirements_digest,
            interpreter_owned=all(Path(path).resolve().is_relative_to(root)
                                  for path in (sys.executable, sys.prefix, sys.base_prefix)),
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, TypeError):
        pass
    return report




def _module_origins_owned(module, inside) -> bool:
    """One observed module; inside is the caller's fixed ownership predicate."""
    from importlib.machinery import BuiltinImporter, FrozenImporter

    advertised = getattr(module, "__file__", None)
    if advertised is not None and not inside(advertised):
        return False
    namespace = getattr(module, "__path__", ())
    if any(not inside(path) for path in tuple(namespace)):
        return False
    spec = getattr(module, "__spec__", None)
    if spec is None:
        # Dynamic builtin submodules may lack a spec; absence supplies no new
        # positive file origin or dependency evidence.
        return True
    origin = getattr(spec, "origin", None)
    if origin in ("built-in", "frozen"):
        expected = BuiltinImporter if origin == "built-in" else FrozenImporter
        if getattr(spec, "loader", None) is not expected:
            return False
    elif origin is not None and not inside(origin):
        return False
    locations = getattr(spec, "submodule_search_locations", None)
    return locations is None or all(inside(path) for path in tuple(locations))


def _loaded_module_origins_owned(root: Path, release: Path) -> bool:
    """Read advertised and loader origins; no dependency execution or echoes.

    __file__ is mutable and may be removed or redirected after a real import.
    Namespace __path__ and ModuleSpec search locations are independent metadata.
    An owned advertised path never overrides a foreign loader observation.
    """
    def inside(path):
        if not isinstance(path, str) or not path:
            return False
        value = Path(path)
        if (not value.is_absolute()
                or any(item.is_symlink() for item in (value, *value.parents))):
            return False
        resolved = value.resolve()
        return resolved.is_relative_to(root) or resolved.is_relative_to(release)

    try:
        for module in tuple(sys.modules.values()):
            if module is None:
                continue
            if not _module_origins_owned(module, inside):
                return False
        package = sys.modules.get("executor")
        expected = str(release / "executor" / "__init__.py")
        return (package is not None and getattr(package, "__file__", None) == expected
                and getattr(getattr(package, "__spec__", None), "origin", None) == expected)
    except (OSError, UnicodeError, ValueError, RuntimeError, TypeError, AttributeError):
        return False

def loaded_process_provenance(repo_root: str | Path, *, source_identity: dict,
                              required_publisher_policy=None) -> dict | None:
    """Observe this process, never infer loaded provenance from payload hashes.

    This is an explicit diagnostic observation, not a startup/health subprocess,
    signer or live-readiness certificate. No dependency/provider is initialized
    here; metadata/payload inspection uses the existing exact release lock.
    """
    supplied = Path(repo_root).expanduser().absolute()
    if not is_packaged_source(supplied):
        return None
    report = {
        "status": "unverified",
        "verification_scope": "current_process_origins_and_dependency_lock",
        "isolated_interpreter": False, "interpreter_owned": False,
        "stdlib_owned": False, "search_paths_owned": False,
        "loaded_modules_owned": False, "dependency_lock_matches": False,
        "source_matches_startup": False,
        "signing_certified": False, "live_readiness_certified": False,
    }
    try:
        # A source-only staging receipt is not an installed runtime layout.
        if (supplied.name != "release" or supplied.parent.name != "Resources"
                or supplied.parent.parent.name != "Contents"
                or not supplied.parent.parent.parent.name.endswith(".app")
                or any(path.is_symlink() for path in (supplied, *supplied.parents))):
            return report
        runtime = supplied.parent / "runtime"
        if (runtime.is_symlink() or not runtime.is_dir()
                or (runtime / "pyvenv.cfg").exists()):
            return report
        source = current_packaged_source(supplied)
        report["source_matches_startup"] = (
            isinstance(source_identity, dict) and source["status"] == "verified"
            and source_identity == source)
        if not report["source_matches_startup"]:
            return report
        root = runtime.resolve()
        release = supplied.resolve()
        def inside(path, *, allow_release=False):
            # Reject aliases, even when they resolve back into an owned tree.
            value = Path(path)
            if not value.is_absolute() or any(item.is_symlink() for item in (value, *value.parents)):
                return False
            resolved = value.resolve()
            return resolved.is_relative_to(root) or (
                allow_release and resolved.is_relative_to(release))
        report["isolated_interpreter"] = (
            sys.flags.isolated == 1 and sys.flags.ignore_environment == 1
            and sys.flags.no_user_site == 1)
        report["interpreter_owned"] = all(
            inside(path) for path in (sys.executable, sys.prefix, sys.base_prefix))
        import sysconfig
        report["stdlib_owned"] = inside(sysconfig.get_path("stdlib"))
        report["search_paths_owned"] = bool(sys.path) and all(
            inside(path, allow_release=True) for path in tuple(sys.path))
        # Snapshot without importing missing modules or invoking provider code.
        # Builtin/frozen modules have no filesystem origin. Namespace search
        # paths must also stay owned; an empty path is never the current folder.
        report["loaded_modules_owned"] = _loaded_module_origins_owned(root, release)
        # Hash/check dependencies only on the explicit diagnostic path and only
        # after process ownership passes. No repeated full-runtime health hash.
        checks = ("isolated_interpreter", "interpreter_owned", "stdlib_owned",
                  "search_paths_owned", "loaded_modules_owned")
        if all(report[key] for key in checks):
            from .release import installed_dependencies_match
            options = {} if required_publisher_policy is None else {'required_publisher_policy': required_publisher_policy}
            report["dependency_lock_matches"] = installed_dependencies_match(release, **options) is True
            report["loaded_modules_owned"] = _loaded_module_origins_owned(root, release)
            report["search_paths_owned"] = bool(sys.path) and all(
                inside(path, allow_release=True) for path in tuple(sys.path))
        # Re-admit the source after inspecting actual loaded dependency payloads.
        if current_packaged_source(supplied) != source:
            report["source_matches_startup"] = False
        if all(report[key] for key in (*checks, "dependency_lock_matches", "source_matches_startup")):
            report["status"] = "verified"
    except (OSError, UnicodeError, ValueError, RuntimeError, TypeError, AttributeError):
        # Finite aggregate failure only: no origin/exception/credential echo.
        report["status"] = "unverified"
    return report
