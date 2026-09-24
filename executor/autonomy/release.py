"""Deterministic source snapshot for a staged consumer release.

This manifest detects source drift and copy corruption. It is not a signature or
a dependency lock; those are separate release gates.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import shutil
from pathlib import Path


MANIFEST_NAME = "release-source-manifest.json"


def source_manifest(root: str | Path) -> dict:
    root = Path(root).expanduser().resolve()
    package = root / "executor"
    requirements = root / "requirements.txt"
    if (not package.is_dir() or package.is_symlink()
            or not (package / "__init__.py").is_file()
            or not requirements.is_file() or requirements.is_symlink()):
        raise ValueError("release_source_missing")
    if any(path.is_symlink() for path in package.rglob("*")):
        raise ValueError("release_source_symlink")

    paths = [requirements, *sorted(package.rglob("*.py"))]
    files = []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError("release_source_invalid")
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "format": "jae-release-source-v1",
        "files": files,
        "source_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def verify_source_candidate(root: str | Path) -> bool:
    supplied = Path(root).expanduser()
    if supplied.is_symlink():
        return False
    root = supplied.resolve()
    manifest_file = root / MANIFEST_NAME
    if manifest_file.is_symlink():
        return False
    try:
        saved = json.loads(manifest_file.read_text(encoding="utf-8"))
        if saved != source_manifest(root):
            return False
        allowed = {entry["path"] for entry in saved["files"]} | {MANIFEST_NAME}
        for path in root.rglob("*"):
            if path.is_symlink() or (path.is_file() and path.relative_to(root).as_posix() not in allowed):
                return False
        return True
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def copy_source_candidate(repo_root: str | Path, destination: str | Path) -> dict:
    """Copy only declared source files, then verify source and candidate."""
    source = Path(repo_root).expanduser().resolve()
    target = Path(destination).expanduser()
    before = source_manifest(source)
    target.mkdir(parents=True, exist_ok=False)
    for entry in before["files"]:
        relative = Path(entry["path"])
        output = target / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, output)
    if source_manifest(source) != before or source_manifest(target) != before:
        raise ValueError("release_source_changed")
    (target / MANIFEST_NAME).write_text(
        json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if not verify_source_candidate(target):
        raise ValueError("release_candidate_invalid")
    return before


def installed_dependencies_match(root: str | Path) -> bool:
    """Require the candidate interpreter to have the pinned release set."""
    try:
        lines = (Path(root) / "requirements.txt").read_text(encoding="utf-8").splitlines()
        pins = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
        if not pins:
            return False
        for pin in pins:
            match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.!+~-]*)", pin)
            if not match or importlib.metadata.version(match[1]) != match[2]:
                return False
        return True
    except (OSError, UnicodeError, importlib.metadata.PackageNotFoundError):
        return False


def read_release_identity(root: str | Path) -> dict:
    """Read a verified packaged-source identity without exposing local paths."""
    root = Path(root).expanduser().resolve()
    if not verify_source_candidate(root):
        return {"status": "unverified", "source_sha256": ""}
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
        return {"status": "verified", "source_sha256": manifest["source_sha256"]}
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return {"status": "unverified", "source_sha256": ""}
