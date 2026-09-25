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
import stat
import sys
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



RUNTIME_MANIFEST_NAME = "release-runtime-manifest.json"


def runtime_manifest(root: str | Path, release: str | Path) -> dict:
    """Hash the app-owned interpreter and dependency tree against pinned source."""
    root = Path(root).expanduser()
    release = Path(release).expanduser()
    python = root / "bin" / "python"
    requirements = release / "requirements.txt"
    if (root.is_symlink() or not root.is_dir() or python.is_symlink()
            or not python.is_file() or not python.stat().st_mode & stat.S_IXUSR
            or not requirements.is_file() or requirements.is_symlink()):
        raise ValueError("release_runtime_missing")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("release_runtime_symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("release_runtime_invalid")
        relative = path.relative_to(root).as_posix()
        if relative == RUNTIME_MANIFEST_NAME:
            continue
        data = path.read_bytes()
        files.append({
            "path": relative,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "executable": bool(path.stat().st_mode & stat.S_IXUSR),
        })
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "format": "jae-release-runtime-v1",
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "files": files,
        "runtime_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def verify_runtime_candidate(root: str | Path, release: str | Path) -> bool:
    root = Path(root).expanduser()
    manifest_file = root / RUNTIME_MANIFEST_NAME
    if manifest_file.is_symlink():
        return False
    try:
        saved = json.loads(manifest_file.read_text(encoding="utf-8"))
        return saved == runtime_manifest(root, release)
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def copy_runtime_candidate(venv: str | Path, destination: str | Path,
                           release: str | Path) -> dict:
    """Stage a private virtualenv snapshot; never link back to the checkout."""
    source = Path(venv).expanduser()
    target = Path(destination).expanduser()
    if source.is_symlink() or not source.is_dir() or target.exists():
        raise ValueError("release_runtime_source_invalid")
    python = source / "bin" / "python"
    if not python.is_file():
        raise ValueError("release_runtime_source_invalid")
    # The interpreter may be a venv copy or alias, but never an arbitrary
    # executable or a symlink to a private file outside the environment.
    expected_python = hashlib.sha256(Path(sys.executable).read_bytes()).digest()
    if hashlib.sha256(python.read_bytes()).digest() != expected_python:
        raise ValueError("release_runtime_interpreter_mismatch")
    for path in source.rglob("*"):
        if not path.is_symlink():
            continue
        relative = path.relative_to(source)
        if (len(relative.parts) != 2 or relative.parts[0] != "bin"
                or not re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", relative.name)
                or not path.resolve().is_file()
                or hashlib.sha256(path.read_bytes()).digest() != expected_python):
            raise ValueError("release_runtime_source_symlink")
    shutil.copytree(source, target, symlinks=False)
    before = runtime_manifest(target, release)
    (target / RUNTIME_MANIFEST_NAME).write_text(
        json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    if not verify_runtime_candidate(target, release):
        raise ValueError("release_runtime_candidate_invalid")
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
