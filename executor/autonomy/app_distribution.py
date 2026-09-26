"""Build an unsigned, self-contained Mac app archive without applicant state.

The final manifest is the publication receipt. Building an archive does not
certify, sign, install on an owner device, or activate any applicant task.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import stat
import sys
import tarfile
import tempfile
from pathlib import Path

from .consumer import APP_NAME, _trusted_bundle, install_macos_app
from .release import (RUNTIME_MANIFEST_NAME, source_manifest,
                      verify_runtime_candidate, verify_source_candidate)
from .standalone_runtime import verify_standalone_runtime

ARCHIVE_NAME = "AIApplicationManager-unsigned.tar.gz"
RECEIPT_NAME = "distribution-manifest.json"
PRIVATE_NAMES = frozenset({
    "tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm",
    "task-answers.key", "auth.token", "service.json", "profile.json",
    "profile-overrides.json", "worker.lock", "migration.lock", ".git", ".env",
})


def _no_alias_path(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("distribution_path_alias")


def _private_name(path: Path) -> bool:
    return any(part in PRIVATE_NAMES or part.startswith(".env.")
               for part in path.parts)


def _runtime_payload_without_state(root: Path) -> None:
    # Inspect names only: no user journal, key, token or profile is read.
    if root.is_symlink() or not root.is_dir():
        raise ValueError("distribution_runtime_invalid")
    for path in root.rglob("*"):
        if _private_name(path.relative_to(root)):
            raise ValueError("distribution_private_payload")


def _bundle_members(app: Path) -> list[Path]:
    outer = {"Contents", "Contents/Info.plist", "Contents/MacOS",
             "Contents/MacOS/AIApplicationManager", "Contents/Resources"}
    members = [app, *sorted(app.rglob("*"))]
    for path in members:
        relative = path.relative_to(app)
        mode = path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("distribution_member_invalid")
        name = relative.as_posix()
        if path == app:
            continue
        if (_private_name(relative) or not (
                name in outer or name == "Contents/Resources/release"
                or name.startswith("Contents/Resources/release/")
                or name == "Contents/Resources/runtime"
                or name.startswith("Contents/Resources/runtime/"))):
            raise ValueError("distribution_private_payload")
    return members


def _archive_app(app: Path, archive: Path) -> None:
    members = _bundle_members(app)
    # Stable headers omit build-machine usernames, paths and timestamps.
    with archive.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=1, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", dereference=True) as bundle:
                for path in members:
                    name = app.name if path == app else (
                        app.name + "/" + path.relative_to(app).as_posix())
                    info = bundle.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.pax_headers = {}
                    info.mode = (0o755 if info.isdir() or path.stat().st_mode & stat.S_IXUSR
                                 else 0o644)
                    if info.isfile():
                        with path.open("rb") as file:
                            bundle.addfile(info, file)
                    elif info.isdir():
                        bundle.addfile(info)
                    else:
                        raise ValueError("distribution_member_invalid")
        raw.flush()
        os.fsync(raw.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_macos_distribution(repo_root: str | Path, *,
                             standalone_runtime: str | Path,
                             output_dir: str | Path,
                             platform: str | None = None) -> dict:
    if (platform or sys.platform) != "darwin":
        raise ValueError("distribution_macos_required")
    repo = Path(repo_root).expanduser().absolute()
    runtime = Path(standalone_runtime).expanduser().absolute()
    output = Path(output_dir).expanduser().absolute()
    for path in (repo, runtime, output):
        _no_alias_path(path)
    if (output.exists() or not output.parent.is_dir()
            or output.is_relative_to(runtime) or runtime.is_relative_to(output)
            or output.is_relative_to(repo / "executor")):
        raise ValueError("distribution_output_unavailable")
    # Build only declared code. Repo-local state/profile/credentials are never
    # selected for the app installer or consulted as migration authority.
    expected_source = source_manifest(repo)
    _runtime_payload_without_state(runtime)
    output.mkdir(mode=0o700, exist_ok=False)
    published = []
    try:
        from .release import copy_source_candidate
        with tempfile.TemporaryDirectory(prefix=".jae-distribution-",
                                         dir=output.parent) as temporary:
            work = Path(temporary)
            isolated_source = work / "source"
            copy_source_candidate(repo, isolated_source)
            if source_manifest(isolated_source) != expected_source:
                raise ValueError("distribution_source_changed")
            apps = work / "Applications"
            result = install_macos_app(
                isolated_source, destination=apps, platform=platform,
                task_state_root=work / "empty-build-state",
                standalone_runtime=runtime)
            if result.get("ok") is not True or result.get("replaced") is not False:
                raise ValueError("distribution_candidate_failed")
            app = apps / (APP_NAME + ".app")
            release = app / "Contents" / "Resources" / "release"
            owned_runtime = app / "Contents" / "Resources" / "runtime"
            def verified():
                return (_trusted_bundle(app) and verify_source_candidate(release)
                        and source_manifest(release) == expected_source
                        and verify_runtime_candidate(owned_runtime, release))
            if not verified() or not verify_standalone_runtime(owned_runtime, release):
                raise ValueError("distribution_candidate_invalid")
            identity = json.loads((owned_runtime / RUNTIME_MANIFEST_NAME).read_text())
            members = _bundle_members(app)
            archive = work / ARCHIVE_NAME
            _archive_app(app, archive)
            # Recheck the manifested payload after streaming every file.
            if (not verified() or source_manifest(repo) != expected_source
                    or _bundle_members(app) != members):
                raise ValueError("distribution_candidate_changed")
            receipt = {
                "format": "jae-macos-distribution-v1",
                "archive": ARCHIVE_NAME, "archive_sha256": _sha256(archive),
                "app_name": app.name, "source_sha256": expected_source["source_sha256"],
                "runtime_sha256": identity["runtime_sha256"],
                "requirements_sha256": identity["requirements_sha256"],
                "signing": "unsigned", "certification": "NOT_CERTIFIED",
                "final_click_actor": "user",
                "task_state": "excluded", "build_host_metadata": "excluded",
            }
            manifest = work / RECEIPT_NAME
            with manifest.open("x", encoding="utf-8") as file:
                json.dump(receipt, file, ensure_ascii=False, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            # Exclusive links cannot overwrite a concurrent file. Receipt is
            # committed last: an archive without it is never a ready artifact.
            for source in (archive, manifest):
                target = output / source.name
                os.link(source, target)
                published.append(target)
            fd = os.open(output, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return receipt
    except BaseException:
        for path in reversed(published):
            path.unlink()
        try:
            output.rmdir()
        except OSError:
            pass  # Never delete an unrecognized concurrent artifact.
        raise
