"""Build an unsigned, self-contained Mac app archive without applicant state.

The final manifest is the publication receipt. Building an archive does not
certify, sign, install on an owner device, or activate any applicant task.
"""
from __future__ import annotations

import gzip
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .consumer import APP_NAME, _owned_bundle_text, _trusted_bundle, install_macos_app
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
             "Contents/MacOS/AIApplicationManager", "Contents/Resources",
             "Contents/Resources/native-host",
             "Contents/Resources/native-host/AIApplicationWindow",
             "Contents/Resources/native-host/native-host-manifest.json"}
    members = [app, *sorted(app.rglob("*"))]
    for path in members:
        relative = path.relative_to(app)
        metadata = path.lstat()
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise ValueError("distribution_member_invalid")
        if (stat.S_ISREG(metadata.st_mode)
                and (metadata.st_nlink != 1 or metadata.st_uid != os.geteuid())):
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
            with tarfile.open(fileobj=zipped, mode="w", dereference=False) as bundle:
                for path in members:
                    name = app.name if path == app else (
                        app.name + "/" + path.relative_to(app).as_posix())
                    info = bundle.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.pax_headers = {}
                    if info.isfile():
                        # The preflight walk is advisory: bind each streamed file to
                        # one owned, single-link inode and recheck its visible path.
                        try:
                            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                        except OSError:
                            raise ValueError("distribution_member_changed") from None
                        with os.fdopen(fd, "rb") as file:
                            before = os.fstat(file.fileno())
                            visible = path.lstat()
                            identity = lambda value: (
                                value.st_dev, value.st_ino, value.st_mode,
                                value.st_size, value.st_mtime_ns, value.st_nlink,
                                value.st_uid)
                            if (not stat.S_ISREG(before.st_mode)
                                    or before.st_nlink != 1
                                    or before.st_uid != os.geteuid()
                                    or identity(before) != identity(visible)
                                    or before.st_size != info.size):
                                raise ValueError("distribution_member_changed")
                            info.mode = 0o755 if before.st_mode & stat.S_IXUSR else 0o644
                            bundle.addfile(info, file)
                            if (identity(os.fstat(file.fileno()))
                                    != identity(before)
                                    or identity(path.lstat()) != identity(before)):
                                raise ValueError("distribution_member_changed")
                    elif info.isdir():
                        if not stat.S_ISDIR(path.lstat().st_mode):
                            raise ValueError("distribution_member_changed")
                        info.mode = 0o755
                        bundle.addfile(info)
                    else:
                        raise ValueError("distribution_member_changed")
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
                             platform: str | None = None,
                             native_presentation: bool = False) -> dict:
    if (platform or sys.platform) != "darwin":
        raise ValueError("distribution_macos_required")
    if type(native_presentation) is not bool:
        raise ValueError("distribution_presentation_invalid")
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
                standalone_runtime=runtime, native_presentation=native_presentation)
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
            identity = json.loads(_owned_bundle_text(owned_runtime / RUNTIME_MANIFEST_NAME))
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
                **({"presentation": "native"} if native_presentation else {}),
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


# Intake bounds include tar headers/padding, not only declared member sizes.
# They do not reduce the complete built runtime or any certification corpus.
MAX_DISTRIBUTION_BYTES = 2 * 1024 * 1024 * 1024
MAX_DISTRIBUTION_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_DISTRIBUTION_MEMBERS = 100_000


def _receipt_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("distribution_receipt_duplicate")
        result[key] = value
    return result


def _read_distribution_receipt(path: Path) -> tuple[dict, bytes]:
    _no_alias_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        metadata = os.fstat(file.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("distribution_receipt_invalid")
        encoded = file.read(16 * 1024 + 1)
    if len(encoded) > 16 * 1024:
        raise ValueError("distribution_receipt_invalid")
    receipt = json.loads(encoded, object_pairs_hook=_receipt_pairs)
    literals = {
        "format": "jae-macos-distribution-v1", "archive": ARCHIVE_NAME,
        "app_name": APP_NAME + ".app", "signing": "unsigned",
        "certification": "NOT_CERTIFIED", "final_click_actor": "user",
        "task_state": "excluded", "build_host_metadata": "excluded",
    }
    digests = {"archive_sha256", "source_sha256", "runtime_sha256",
               "requirements_sha256"}
    required = set(literals) | digests
    if (not isinstance(receipt, dict)
            or set(receipt) not in (required, required | {"presentation"})
            or any(type(receipt.get(key)) is not str or receipt[key] != value
                   for key, value in literals.items())
            or ("presentation" in receipt and receipt["presentation"] != "native")):
        raise ValueError("distribution_receipt_invalid")
    for key in digests:
        value = receipt[key]
        if (type(value) is not str or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)):
            raise ValueError("distribution_receipt_invalid")
    return receipt, encoded


def _distribution_pax_matches(member: tarfile.TarInfo) -> bool:
    # CPython's PAX writer appends one slash to directory paths; its reader
    # strips it from member.name but retains it in pax_headers["path"].
    # Accept only that exact canonical representation, never other metadata.
    expected = member.name + ("/" if member.isdir() else "")
    return all(key == "path" and value == expected
               for key, value in member.pax_headers.items())


@contextmanager
def stage_macos_distribution(distribution: str | Path):
    """Yield a privately staged, receipt-bound app without running its payload.

    This is unsigned local integrity admission, not publisher authenticity,
    signing, health, activation, task migration or a certification receipt.
    The existing app transaction remains the only activation authority.
    The candidate and all staging files cease to exist when the caller exits.
    """
    try:
        supplied = Path(distribution).expanduser().absolute()
        _no_alias_path(supplied)
        if not supplied.is_dir():
            raise ValueError("distribution_directory_invalid")
        receipt_path = supplied / RECEIPT_NAME
        receipt, receipt_bytes = _read_distribution_receipt(receipt_path)
        archive_path = supplied / ARCHIVE_NAME
        _no_alias_path(archive_path)
        archive_metadata = archive_path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(archive_metadata.st_mode)
                or archive_metadata.st_nlink != 1
                or not 0 < archive_metadata.st_size <= MAX_DISTRIBUTION_BYTES):
            raise ValueError("distribution_archive_invalid")
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError("distribution_intake_refused") from None
    with tempfile.TemporaryDirectory(prefix=".jae-distribution-intake-") as temporary:
        # Canonicalize only our newly created private temporary root. macOS
        # may spell its system temp ancestor through /var -> /private/var.
        # Caller-supplied package/destination aliases still remain forbidden.
        work = Path(temporary).resolve(strict=True)
        _no_alias_path(work)
        app = work / (APP_NAME + ".app")
        expanded = work / "payload.tar"
        # Fixed diagnostic stages carry no package text, filesystem path or
        # applicant value; owning CI can distinguish a remaining admission root.
        phase = "archive"
        try:
            # One descriptor binds digest, decompression and final recheck.
            fd = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as raw:
                original = os.fstat(raw.fileno())
                if (not stat.S_ISREG(original.st_mode) or original.st_nlink != 1
                        or not 0 < original.st_size <= MAX_DISTRIBUTION_BYTES):
                    raise ValueError("distribution_archive_invalid")
                def digest():
                    raw.seek(0)
                    hashed = hashlib.sha256()
                    read_bytes = 0
                    for block in iter(lambda: raw.read(1024 * 1024), b""):
                        read_bytes += len(block)
                        if read_bytes > MAX_DISTRIBUTION_BYTES:
                            raise ValueError("distribution_archive_size")
                        hashed.update(block)
                    return hashed.hexdigest()
                if digest() != receipt["archive_sha256"]:
                    raise ValueError("distribution_archive_digest")
                raw.seek(0)
                phase = "expansion"
                expanded_bytes = 0
                with gzip.GzipFile(fileobj=raw, mode="rb") as zipped, expanded.open("xb") as target:
                    for block in iter(lambda: zipped.read(1024 * 1024), b""):
                        expanded_bytes += len(block)
                        if expanded_bytes > MAX_DISTRIBUTION_EXPANDED_BYTES:
                            raise ValueError("distribution_archive_expansion")
                        target.write(block)
                phase = "members"
                seen = set()
                with expanded.open("rb") as payload, tarfile.open(fileobj=payload, mode="r:") as archive:
                    for member in archive:
                        name = member.name
                        relative = PurePosixPath(name)
                        if (len(seen) >= MAX_DISTRIBUTION_MEMBERS or name in seen
                                or relative.is_absolute() or relative.as_posix() != name
                                or "\\" in name or ":" in name
                                or any(part in ("", ".", "..") for part in relative.parts)
                                or not relative.parts or relative.parts[0] != app.name
                                or _private_name(Path(*relative.parts[1:]))
                                or member.type not in (tarfile.DIRTYPE, tarfile.REGTYPE)
                                or member.size < 0 or (member.isdir() and member.size != 0)
                                or member.mode not in (0o644, 0o755)
                                or (member.isdir() and member.mode != 0o755)
                                or member.uid != 0 or member.gid != 0 or member.mtime != 0
                                or member.uname or member.gname
                                or not _distribution_pax_matches(member)):
                            raise ValueError("distribution_archive_member")
                        target = work.joinpath(*relative.parts)
                        if not target.parent.is_dir():
                            raise ValueError("distribution_archive_parent")
                        seen.add(name)
                        if member.isdir():
                            target.mkdir(mode=0o755, exist_ok=False)
                        else:
                            source = archive.extractfile(member)
                            if source is None:
                                raise ValueError("distribution_archive_member")
                            copied = 0
                            with source, target.open("xb") as file:
                                for block in iter(lambda: source.read(1024 * 1024), b""):
                                    copied += len(block)
                                    file.write(block)
                            if copied != member.size:
                                raise ValueError("distribution_archive_truncated")
                            target.chmod(member.mode)
                    # Only canonical zero padding may follow the final member.
                    payload.seek(archive.offset)
                    for block in iter(lambda: payload.read(1024 * 1024), b""):
                        if any(block):
                            raise ValueError("distribution_archive_trailing")
                phase = "archive_recheck"
                _no_alias_path(archive_path)
                current = archive_path.stat(follow_symlinks=False)
                if (current.st_dev != original.st_dev or current.st_ino != original.st_ino
                        or current.st_nlink != 1 or current.st_size != original.st_size
                        or digest() != receipt["archive_sha256"]):
                    raise ValueError("distribution_archive_changed")
            phase = "receipt_recheck"
            if _read_distribution_receipt(receipt_path) != (receipt, receipt_bytes):
                raise ValueError("distribution_receipt_changed")
            release = app / "Contents/Resources/release"
            runtime = app / "Contents/Resources/runtime"
            phase = "payload"
            _bundle_members(app)
            if (not _trusted_bundle(app)
                    or not verify_source_candidate(release)
                    or not verify_runtime_candidate(runtime, release)
                    or source_manifest(release)["source_sha256"] != receipt["source_sha256"]):
                raise ValueError("distribution_payload_invalid")
            phase = "identity"
            identity = json.loads(_owned_bundle_text(runtime / RUNTIME_MANIFEST_NAME))
            if any(identity[key] != receipt[key]
                   for key in ("runtime_sha256", "requirements_sha256")):
                raise ValueError("distribution_payload_identity")
            phase = "presentation"
            from .consumer import _native_packaged_launcher
            native = _owned_bundle_text(app / "Contents/MacOS/AIApplicationManager") == _native_packaged_launcher()
            if native != (receipt.get("presentation") == "native"):
                raise ValueError("distribution_presentation_mismatch")
            expanded.unlink()
        except (OSError, EOFError, ValueError, TypeError, KeyError, UnicodeError,
                tarfile.TarError):
            # Do not disclose applicant/build paths or untrusted archive text.
            raise ValueError("distribution_intake_refused:" + phase) from None
        yield app


def install_macos_distribution(
    distribution: str | Path, *,
    destination: str | Path | None = None,
    task_state_root: str | Path | None = None,
    platform: str | None = None,
) -> dict:
    """Admit a complete local delivery into the existing app transaction.

    Intake is static and receipt-bound; bundle admission performs the real
    standalone-runtime probe. The existing installer alone owns app/state
    locks, private backup, candidate health, activation and compensation.
    This does not discover/download releases, attest an unsigned publisher,
    stop a live service, or grant a second task-state/update authority.
    """
    if (platform or sys.platform) != "darwin":
        return {"ok": False, "reason": "macos_required"}
    with ExitStack() as cleanup:
        try:
            candidate = cleanup.enter_context(stage_macos_distribution(distribution))
        except (OSError, ValueError, TypeError, UnicodeError):
            return {"ok": False, "reason": "distribution_candidate_invalid",
                    "message": "交付包无法核对；现有应用和任务保持不变。"}
        from .consumer import install_macos_bundle

        # Keep the admitted candidate alive through the entire transaction,
        # including refusal/compensation, and clean only our staging on exit.
        return install_macos_bundle(candidate, destination=destination,
            task_state_root=task_state_root, platform=platform)
