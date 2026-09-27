"""Cold, read-only app payload provenance shared by service and recovery.

Only stdlib and the cold release contract are imported. Current payload integrity
does not certify dependency loading, signing, a live service or consumer readiness.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .release import (is_packaged_source, read_release_identity, MANIFEST_NAME,
                      verify_runtime_candidate, RUNTIME_MANIFEST_NAME)


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


def packaged_provenance(repo_root: str | Path) -> dict | None:
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
        if runtime.is_symlink() or manifest_file.is_symlink() or not manifest_file.is_file():
            return report
        manifest_text = manifest_file.read_text(encoding="utf-8")
        if not verify_runtime_candidate(runtime, supplied):
            return report
        if manifest_file.read_text(encoding="utf-8") != manifest_text:
            return report
        manifest = json.loads(manifest_text)
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


