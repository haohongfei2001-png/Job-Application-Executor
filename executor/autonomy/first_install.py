"""Finder first-install admission for a source-bound, self-contained Mac app.

No download, environment repair, migration, arbitrary install destination or
second task authority. Explicit engineering native-launch remains separate.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import select
import stat
import time
import subprocess
import sys


_REASONS = {
    "first_install_activation_unconfirmed", "first_install_continuity_unconfirmed",
    "first_install_source_changed", "first_install_target_occupied", "first_install_state_continuity_required",
    "legacy_state_migration_required", "legacy_state_unavailable",
    "update_in_progress", "update_lock_unavailable", "task_state_in_use",
    "task_state_unavailable", "bundle_candidate_invalid", "candidate_start_failed",
    "candidate_state_incompatible", "activation_identity_changed", "rollback_required",
    "activation_failed", "post_activation_unhealthy", "post_activation_recovery_required",
    "source_snapshot_failed", "runtime_snapshot_failed", "native_candidate_failed",
    "candidate_invalid", "candidate_identity_unverified", "staging_pending",
    "rollback_pending", "failed_candidate_pending",
}


def _bundle_tag(identity) -> str:
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def _fence_path(apps: Path) -> Path:
    return apps / ".jae-first-install-state.json"


def _fence_record(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("first install fence invalid")
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != {"format", "bundle_tag", "status"}
            or value["format"] != "jae-first-install-fence-v1"
            or type(value["status"]) is not str or value["status"] not in {"pending", "complete"}
            or type(value["bundle_tag"]) is not str or len(value["bundle_tag"]) != 64
            or any(c not in "0123456789abcdef" for c in value["bundle_tag"])):
        raise ValueError("first install fence invalid")
    return value


def _read_first_fence(apps: Path) -> dict | None:
    from .consumer import _app_transaction_directory
    apps = _app_transaction_directory(apps)
    path = _fence_path(apps)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        entry, visible = os.fstat(fd), path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid()
                or entry.st_nlink != 1 or entry.st_mode & 0o077 or entry.st_size > 1024
                or (entry.st_dev, entry.st_ino) != (visible.st_dev, visible.st_ino)):
            raise ValueError("first install fence invalid")
        return _fence_record(os.read(fd, 1025))
    finally:
        os.close(fd)


def _create_first_fence(apps: Path, identity) -> None:
    # Caller owns the existing application transaction lock. This negative
    # fence is durable BEFORE activation, so a later ordinary Finder launch
    # cannot bypass interrupted or refused first-install admission.
    path = _fence_path(apps)
    data = json.dumps({"format": "jae-first-install-fence-v1",
                       "bundle_tag": _bundle_tag(identity), "status": "pending"},
                      sort_keys=True, separators=(",", ":")).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("first install fence incomplete")
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(apps, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _complete_first_fence(apps: Path, identity, *, _expected_fence_tag=None) -> None:
    # Caller owns app + state guards and has just re-admitted the empty default
    # authority. In-place finite update fails closed if interrupted; no alias,
    # hardlink, unrelated file replacement or delete is permitted.
    path = _fence_path(apps)
    expected = {"format": "jae-first-install-fence-v1",
                "bundle_tag": _bundle_tag(identity), "status": "pending"}
    if _read_first_fence(apps) != expected:
        raise ValueError("first install fence changed")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before, visible = os.fstat(fd), path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or before.st_mode & 0o077 or before.st_size > 1024
                or (before.st_dev, before.st_ino) != (visible.st_dev, visible.st_ino)
                or _fence_record(os.read(fd, 1025)) != expected):
            raise ValueError("first install fence changed")
        if (_expected_fence_tag is not None and hashlib.sha256(json.dumps([
                (before.st_dev, before.st_ino, stat.S_IMODE(before.st_mode), before.st_uid),
                expected], sort_keys=True, separators=(",", ":")).encode()).hexdigest() != _expected_fence_tag):
            raise ValueError("first install fence changed")
        data = json.dumps({**expected, "status": "complete"}, sort_keys=True, separators=(",", ":")).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        if os.write(fd, data) != len(data):
            raise OSError("first install fence incomplete")
        os.ftruncate(fd, len(data)); os.fsync(fd)
        after = path.stat(follow_symlinks=False)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("first install fence changed")
    finally:
        os.close(fd)


def _read_continuity_pipe(fd: int) -> dict | None:
    """Consume one private, bounded parent-to-child continuity record."""
    if type(fd) is not int or not 3 <= fd <= 2147483647:
        return None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISFIFO(metadata.st_mode) or metadata.st_uid != os.geteuid():
            return None
        os.set_blocking(fd, False)
        deadline, data = time.monotonic() + 5, bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(data) > 8192:
                return None
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(fd, min(1024, 8193 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate continuity field")
                value[key] = item
            return value
        result = json.loads(data, object_pairs_hook=unique)
        if (type(result) is not dict or set(result) != {"format", "bundle_tag"}
                or result["format"] != "jae-first-install-reopen-v1"
                or type(result["bundle_tag"]) is not str or len(result["bundle_tag"]) != 64
                or any(c not in "0123456789abcdef" for c in result["bundle_tag"])):
            return None
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def launch_native_entry(root, port, *, smoke=False, continuity_fd=None):
    from . import cli, consumer, macos_host
    from .release import source_manifest
    from .runtime_paths import default_runtime
    from .process_entry import CLI_ENTRY_SCRIPT

    boundary = {"final_click_actor": "user", "submit_capability": False}
    source = Path(__file__).absolute().parents[2]
    candidate = source.parent.parent.parent
    host = source.parent / "native-host"
    home = Path.home().absolute()
    apps = home / "Applications"
    target = apps / (consumer.APP_NAME + ".app")
    state = Path(default_runtime(source)).absolute()
    conventional = home / "Job-Application-Executor"
    activation_attempted = False
    continuity = _read_continuity_pipe(continuity_fd) if continuity_fd is not None else None

    def refuse(reason):
        category = ("legacy-state-required" if reason in {"legacy_state_migration_required", "legacy_state_unavailable", "first_install_continuity_unconfirmed"}
                    else "target-occupied" if reason == "first_install_target_occupied"
                    else "state-continuity-required" if reason == "first_install_state_continuity_required"
                    else "reopen-failed" if reason == "first_install_reopen_failed"
                    else "not-confirmed")
        # No source/host image is executed after it lost the original identity.
        visible = False
        if consumer._bundle_transaction_identity(candidate) == identity:
            visible = macos_host.present_native_first_install(host, result=category) is not None
        return {"ok": False, "installed": False, "reason": reason,
                "first_install_result_visible": visible, **boundary}

    try:
        if (sys.platform != "darwin" or type(smoke) is not bool
                or type(port) is not int or not 0 < port < 65536
                or Path(root).expanduser().absolute() != state
                or source != candidate / "Contents/Resources/release"
                or candidate.name != consumer.APP_NAME + ".app"
                or any(p.is_symlink() for p in (source, *source.parents, state, *state.parents))):
            return {"ok": False, "installed": False, "reason": "native_entry_unverified", **boundary}
        identity = consumer._bundle_transaction_identity(candidate)
        if (identity is None or not consumer._is_native_packaged_launcher(
                consumer._owned_bundle_text(candidate / "Contents/MacOS/AIApplicationManager"))):
            return {"ok": False, "installed": False, "reason": "native_entry_unverified", **boundary}
        expected_source = source_manifest(source)["source_sha256"]
        if continuity_fd is not None and (continuity is None or candidate != target
                or continuity["bundle_tag"] != _bundle_tag(identity)):
            return refuse("first_install_continuity_unconfirmed")
        if candidate == target:
            fence = _read_first_fence(apps)
            if fence is not None and fence["status"] == "pending":
                from .first_use_recovery import prepare_recovery
                try:
                    startup = prepare_recovery(candidate, state, identity, host,
                                               confirm=continuity is None)
                except (OSError, ValueError, TypeError):
                    return refuse("first_install_continuity_unconfirmed")
                if startup is None:
                    return {"ok":True, "cancelled":True, "installed":True,
                            "recovery_requested":False, **boundary}
                return cli.launch_native_consumer(state, port, smoke=smoke,
                                                  _first_use_record=startup)
            elif continuity is not None:
                # No missing/complete fence may replay an initial admission.
                return refuse("first_install_continuity_unconfirmed")
            if consumer._legacy_state_migration_needed(conventional, target, state):
                return refuse("legacy_state_migration_required")
            return cli.launch_native_consumer(state, port, smoke=smoke)
        if target.exists() or target.is_symlink():
            return refuse("first_install_target_occupied")
        choice = macos_host.present_native_first_install(host)
        if choice == {"action": "cancel"}:
            return {"ok": True, "cancelled": True, "installed": False, **boundary}
        choice = macos_host.native_first_install_choice(choice)
        if choice is None:
            return refuse("first_install_intent_unconfirmed")
        if consumer._bundle_transaction_identity(candidate) != identity:
            return {"ok": False, "installed": False, "reason": "first_install_source_changed", **boundary}
        origins = (conventional,)
        if choice.get("continuity") == "selected_legacy_directory":
            selected = Path(choice["legacy_directory"])
            if (any(p.is_symlink() for p in (selected, *selected.parents)) or not selected.is_dir()):
                return refuse("legacy_state_unavailable")
            # Selecting a historical source cannot become a one-shot approval
            # that disappears on the next Finder launch. Until durable legacy
            # transfer is supported, preserve everything and do not activate.
            return refuse("legacy_state_migration_required")
        activation_attempted = True
        result = consumer.install_macos_bundle(candidate, destination=apps,
            _first_install=True, _legacy_additional_origins=origins, _approved_identity=identity)
        if (type(result) is not dict or result.get("ok") is not True
                or result.get("installed") is not True or result.get("replaced") is not False):
            reason = result.get("reason") if type(result) is dict else None
            answer = refuse(reason if reason in _REASONS else "first_install_unconfirmed")
            if reason in {"rollback_required", "post_activation_recovery_required",
                          "first_install_activation_unconfirmed", "first_install_continuity_unconfirmed"} or reason not in _REASONS:
                answer.update(installed=None, activation_unconfirmed=True)
            return answer
        active_source = target / "Contents/Resources/release"
        active_runtime = target / "Contents/Resources/runtime"
        activated_identity = consumer._bundle_transaction_identity(target)
        if (activated_identity is None
                or not consumer._approved_first_install_matches((candidate, identity), target)
                or consumer._bundle_transaction_identity(target) != activated_identity
                or any(p.is_symlink() for p in (target, *target.parents))
                or source_manifest(active_source)["source_sha256"] != expected_source
                or not consumer._is_native_packaged_launcher(
                    consumer._owned_bundle_text(target / "Contents/MacOS/AIApplicationManager"))):
            answer = refuse("first_install_activation_unverified")
            return {**answer, "installed": None, "activation_unconfirmed": True}
        if (consumer._legacy_root_has_state(state, state)
                or any(consumer._legacy_state_migration_needed(old, target, state) for old in origins)):
            answer = refuse("first_install_continuity_unconfirmed")
            return {**answer, "installed": None, "activation_unconfirmed": True}
        read_fd = write_fd = None
        try:
            record = {"format": "jae-first-install-reopen-v1",
                      "bundle_tag": _bundle_tag(activated_identity)}
            payload = json.dumps(record, separators=(",", ":")).encode()
            if len(payload) > 8192:
                raise ValueError("continuity record too large")
            read_fd, write_fd = os.pipe()
            os.set_blocking(write_fd, False)
            if os.write(write_fd, payload) != len(payload):
                raise OSError("continuity record incomplete")
            os.close(write_fd); write_fd = None
            # All prompt/transaction leases have ended. Reopen only the owned
            # activated runtime/source, never recurse into the downloaded app.
            subprocess.Popen([str(active_runtime / "bin/python"), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
                str(active_source), "--port", str(port), "native-entry",
                "--first-install-continuity-fd", str(read_fd)], cwd=active_source,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, pass_fds=(read_fd,), start_new_session=True)
        except (OSError, ValueError):
            answer = refuse("first_install_reopen_failed")
            return {**answer, "installed": True, "reopen_requested": False}
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
        return {"ok": True, "installed": True, "replaced": False,
                "reopen_requested": True, "native_ready_verified": False, **boundary}
    except (OSError, ValueError, TypeError):
        return {"ok": False, "installed": None if activation_attempted else False,
                "activation_unconfirmed": activation_attempted,
                "reason": "native_entry_unverified", **boundary}
