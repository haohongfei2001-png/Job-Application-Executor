"""Complete recovery cases, including seven retained cases moved verbatim.

All seven prior restored-health cases and their assertions are copied exactly
from the original consumer suite. Seventeen new cases use its real packaged
release/private-WAL fixture and autouse isolated-home fixture. Both owning
files run completely on Ubuntu/Mac; no case, fixture or timeout is reduced.
"""
from __future__ import annotations

import json
import os
import select
import threading
import time
from pathlib import Path

import pytest

from executor.autonomy import consumer
from executor.autonomy.consumer import install_macos_app, rollback_macos_app
from test_consumer_entry_v1 import (
    isolated_packaged_task_state,
    _identity_transaction_fixture,
    _identity_file_inventory,
    _reseal_changed_source,
    _retirement_inventory,
    _assert_retirement_inventory,
)


class _ProbeOutput:
    """Drain both child streams; retain at most 8 KiB total, never publish bytes.

    Nonblocking reads and a bounded select let finish retire the reader even
    when a descendant keeps a write end open. The reader never waits for or
    signals a child. Only the original probe owns process lifecycle decisions.
    Observed byte counts exclude any output left unread at bounded retirement;
    even an exited child can leave buffered bytes without an observed EOF.
    """

    _LIMIT = 4096  # Per stream, 8 KiB total.

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._reads = {}
        self._writes = {}
        self._data = {name: bytearray() for name in ("stdout", "stderr")}
        self._counts = {name: 0 for name in self._data}
        self._eof = {name: False for name in self._data}
        self._error = False
        self._thread = None
        self._result = None
        try:
            for name in self._data:
                read_fd, write_fd = os.pipe()
                self._reads[name] = read_fd
                self._writes[name] = write_fd
                os.set_blocking(read_fd, False)
            self._thread = threading.Thread(target=self._drain, daemon=True)
            self._thread.start()
        except Exception:
            self._stop.set()
            self.close_writers()
            self._close_readers()
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=.25)
            raise

    def close_writers(self):
        while self._writes:
            _, fd = self._writes.popitem()
            try:
                os.close(fd)
            except OSError:
                self._error = True

    def _close_readers(self):
        with self._lock:
            while self._reads:
                _, fd = self._reads.popitem()
                try:
                    os.close(fd)
                except OSError:
                    self._error = True

    def _read_once(self, name, fd):
        # The lock protects against finish closing/reusing an fd after its
        # bounded join. The only read inside it is explicitly nonblocking.
        with self._lock:
            if self._reads.get(name) != fd:
                return
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                return
            except OSError:
                self._error = True
                raise
            if not chunk:
                self._eof[name] = True
                os.close(self._reads.pop(name))
                return
            self._counts[name] += len(chunk)
            remaining = self._LIMIT - len(self._data[name])
            self._data[name].extend(chunk[:remaining])

    def _drain(self):
        try:
            while not self._stop.is_set():
                with self._lock:
                    readers = dict(self._reads)
                if not readers:
                    return
                try:
                    ready, _, _ = select.select(list(readers.values()), [], [], .02)
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                for name, fd in readers.items():
                    if fd in ready:
                        self._read_once(name, fd)
            # Capture already-buffered prefixes without waiting for descendant
            # EOF or trying to drain an unbounded writer to completion.
            with self._lock:
                readers = dict(self._reads)
            for name, fd in readers.items():
                self._read_once(name, fd)
        except Exception:
            # Do not let threading's default exception hook publish private
            # exception text, close a live writer's pipe, or stop draining it.
            # Observation faults invalidate this evidence, not the product.
            self._error = True
            self._discard_until_stopped()
        finally:
            self._close_readers()
            self._done.set()

    def _discard_until_stopped(self):
        while not self._stop.is_set():
            with self._lock:
                for name, fd in list(self._reads.items()):
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        continue
                    if not chunk:
                        self._eof[name] = True
                        try:
                            os.close(self._reads.pop(name))
                        except OSError:
                            self._error = True
                if not self._reads:
                    return
            self._stop.wait(.001)

    def retire(self):
        """Resource cleanup is separate from fallible classification/reporting."""
        self.close_writers()
        self._stop.set()
        self._done.wait(timeout=.25)
        # No reader owns a blocking operation. Closing under the same lock
        # prevents fd reuse while a final nonblocking read is in progress.
        self._close_readers()
        try:
            self._thread.join(timeout=.25)
        except Exception:
            self._error = True
            try:
                threading.Thread.join(self._thread, timeout=.25)
            except Exception:
                pass

    def finish(self):
        self.retire()
        if self._result is not None:
            return dict(self._result)
        result = self._classify()
        self._result = result
        return dict(result)

    def _classify(self):
        with self._lock:
            result = {"reader_stopped": not self._thread.is_alive(),
                      "reader_error": self._error}
            for name, data in self._data.items():
                kind = "empty" if not data else "unclassified"
                if name == "stdout" and data.strip() == b'{"ok": false, "error": "operation_failed"}':
                    kind = "operation_failed"
                elif name == "stderr" and b"Fatal Python error: init_fs_encoding:" in data:
                    kind = "python_initialization_failed"
                elif name == "stderr" and b"AssertionError" in data:
                    kind = "assertion_reported"
                result.update({name + "_class": kind,
                               name + "_bytes_observed": self._counts[name],
                               name + "_bytes_retained": len(data),
                               name + "_truncated": self._counts[name] > len(data),
                               name + "_eof_observed": self._eof[name]})
                data.clear()
            return result


def _observe_candidate_probe(actual, python, source, sequence):
    """Test-only observation; preserve the real call, process and bool result."""
    original_subprocess = consumer.subprocess
    captures = []
    children = []
    capture_failed = False

    class ObservedSubprocess:
        def __getattr__(self, name):
            return getattr(original_subprocess, name)

        def Popen(self, *args, **kwargs):
            nonlocal capture_failed
            try:
                capture = _ProbeOutput()
            except Exception:
                # Observation setup failure must not prevent the original
                # launch or replace its error, nor cause a second launch.
                capture_failed = True
                child = original_subprocess.Popen(*args, **kwargs)
                children.append(child)
                return child
            captures.append(capture)
            observed = {**kwargs, **capture._writes}
            try:
                child = original_subprocess.Popen(*args, **observed)
                children.append(child)
                return child  # Original object; poll/terminate/wait untouched.
            finally:
                capture.close_writers()

    started = time.monotonic()
    outcome = "raised"
    consumer.subprocess = ObservedSubprocess()
    try:
        result = actual(python, source)
        outcome = "healthy" if result is True else "unhealthy"
        return result
    finally:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        consumer.subprocess = original_subprocess
        diagnostics = []
        for capture in captures:
            try:
                diagnostics.append(capture.finish())
            except Exception:
                capture_failed = True
                try:
                    capture.retire()
                except Exception:
                    pass
                with capture._lock:
                    for data in capture._data.values():
                        data.clear()
        try:
            # Everything emitted is a fixed enum or scalar. In particular,
            # never emit bytes, argv, env, token, exception text or path.
            event = {"format": "jae-synthetic-candidate-probe-v1",
                     "probe": sequence, "outcome": outcome, "elapsed_ms": elapsed_ms,
                     "child_count": len(children), "capture_failed": capture_failed,
                     "observation_valid": not capture_failed and all(
                         not item["reader_error"] and item["reader_stopped"] for item in diagnostics),
                     "returncode_after_cleanup": children[-1].returncode if children else None}
            if len(diagnostics) == 1:
                event.update(diagnostics[0])
            print(json.dumps(event, sort_keys=True))
        except Exception:
            pass  # A diagnostic sink cannot change the probe result.


@pytest.fixture(autouse=True)
def _diagnose_one_recovery_probe(request, monkeypatch):
    # Deliberately limit observation to the original failing synthetic nodeid.
    if request.node.name != (
        "test_first_recovery_move_rechecks_both_roles_before_restoring_private_authority"
        "[retained_resealed-rollback]"
    ):
        return
    actual = consumer._candidate_starts
    sequence = 0

    def observed(python, source):
        nonlocal sequence
        sequence += 1
        return _observe_candidate_probe(actual, python, source, sequence)

    monkeypatch.setattr(consumer, "_candidate_starts", observed)


@pytest.mark.parametrize("recovery_fault", [
    "none", "unhealthy", "current_resealed", "current_replaced",
    "quarantine_replaced", "failed_occupied", "failed_alias",
])
def test_rollback_recovery_requires_restored_final_path_health_and_owned_slots(
    tmp_path, monkeypatch, recovery_fault
):
    """Real packaged releases, complete private WAL, and no recovery replay."""
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(tmp_path, rollback=True)
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    current_inode = app.stat().st_ino
    previous_inode = previous.stat().st_ino
    current_before = _identity_file_inventory(app)
    previous_before = _identity_file_inventory(previous)
    actual_start = consumer._candidate_starts
    starts = []
    after_health = {}
    after_inodes = {}

    def inode_inventory():
        return {path.relative_to(apps).as_posix():
                (path.lstat().st_dev, path.lstat().st_ino)
                for path in apps.rglob("*")}

    def start(python, source):
        # Every observed health invocation reaches the real packaged runtime;
        # the second response simulates final-path failure of the old release.
        healthy = actual_start(python, source)
        assert healthy
        starts.append((python, source))
        if len(starts) == 2:
            assert source == app / "Contents" / "Resources" / "release"
            return False
        if len(starts) == 3:
            assert app.stat().st_ino == current_inode
            assert previous.stat().st_ino == previous_inode
            assert _identity_file_inventory(app) == current_before
            assert _identity_file_inventory(previous) == previous_before
            assert not failed.exists() and not failed.is_symlink()
            if recovery_fault == "current_resealed":
                _reseal_changed_source(app)
            elif recovery_fault in {"current_replaced", "quarantine_replaced"}:
                target = app if recovery_fault == "current_replaced" else previous
                preserved = apps / ("preserved-current" if target == app else "preserved-quarantine")
                target.rename(preserved)
                shutil.copytree(preserved, target, symlinks=True)
                assert target.stat().st_ino != preserved.stat().st_ino
                assert consumer._trusted_bundle(target)
            elif recovery_fault == "failed_occupied":
                failed.mkdir()
                (failed / "foreign-evidence.txt").write_text("PRIVATE_FOREIGN_RECOVERY_EVIDENCE")
            elif recovery_fault == "failed_alias":
                failed.symlink_to(apps / "missing-foreign-slot", target_is_directory=True)
                assert failed.is_symlink() and not failed.exists()
            after_health.update(_retirement_inventory(apps))
            after_inodes.update(inode_inventory())
            return recovery_fault != "unhealthy"
        return healthy

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        result = rollback_macos_app(apps, task_state_root=state)

        assert result["ok"] is False
        assert result["reason"] == (
            "rollback_post_activation_unhealthy" if recovery_fault == "none"
            else "rollback_recovery_required")
        assert len(starts) == 3
        assert [source for _, source in starts] == [
            previous / "Contents" / "Resources" / "release",
            app / "Contents" / "Resources" / "release",
            app / "Contents" / "Resources" / "release",
        ]
        assert all(python == source.parent / "runtime" / "bin" / "python"
                   for python, source in starts)
        # No later rename, deletion, cleanup, retry, or speculative activation
        # may erase the exact post-health bytes, aliases, modes, or inodes.
        _assert_retirement_inventory(apps, after_health)
        assert inode_inventory() == after_inodes
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("action", ["install"])
@pytest.mark.parametrize("recovery_fault", [
    "none", "unhealthy", "current_resealed", "current_replaced",
    "quarantine_replaced", "failed_occupied", "failed_alias",
])
def test_install_recovery_requires_restored_final_path_health_and_owned_slots(
    tmp_path, monkeypatch, action, recovery_fault
):
    """Real packaged releases, complete private WAL, and no recovery replay."""
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    current_inode = app.stat().st_ino
    quarantine = previous if action == "rollback" else failed
    rejected_before = _identity_file_inventory(previous) if action == "rollback" else None
    rejected_inode = previous.stat().st_ino if action == "rollback" else None
    current_before = _identity_file_inventory(app)
    actual_start = consumer._candidate_starts
    starts = []
    after_health = {}
    after_inodes = {}

    def inode_inventory():
        return {path.relative_to(apps).as_posix():
                (path.lstat().st_dev, path.lstat().st_ino)
                for path in apps.rglob("*")}

    def start(python, source):
        nonlocal rejected_before, rejected_inode
        # Every observed health invocation reaches the real packaged runtime;
        # the second response simulates final-path failure of the attempted release.
        healthy = actual_start(python, source)
        assert healthy
        starts.append((python, source))
        if len(starts) == 2:
            assert source == app / "Contents" / "Resources" / "release"
            # Capture the entire rejected release before it is quarantined.
            rejected_before = _identity_file_inventory(app)
            rejected_inode = app.stat().st_ino
            return False
        if len(starts) == 3:
            assert app.stat().st_ino == current_inode
            assert quarantine.stat().st_ino == rejected_inode
            assert _identity_file_inventory(app) == current_before
            assert _identity_file_inventory(quarantine) == rejected_before
            empty_slot = failed if action == "rollback" else previous
            assert not empty_slot.exists() and not empty_slot.is_symlink()
            if recovery_fault == "current_resealed":
                _reseal_changed_source(app)
            elif recovery_fault in {"current_replaced", "quarantine_replaced"}:
                target = app if recovery_fault == "current_replaced" else quarantine
                preserved = apps / ("preserved-current" if target == app else "preserved-quarantine")
                target.rename(preserved)
                shutil.copytree(preserved, target, symlinks=True)
                assert target.stat().st_ino != preserved.stat().st_ino
                assert consumer._trusted_bundle(target)
            elif recovery_fault == "failed_occupied":
                empty_slot.mkdir()
                (empty_slot / "foreign-evidence.txt").write_text("PRIVATE_FOREIGN_RECOVERY_EVIDENCE")
            elif recovery_fault == "failed_alias":
                empty_slot.symlink_to(apps / "missing-foreign-slot", target_is_directory=True)
                assert empty_slot.is_symlink() and not empty_slot.exists()
            after_health.update(_retirement_inventory(apps))
            after_inodes.update(inode_inventory())
            return recovery_fault != "unhealthy"
        return healthy

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))

        assert result["ok"] is False
        assert result["reason"] == (
            ("rollback_post_activation_unhealthy" if action == "rollback"
             else "post_activation_unhealthy") if recovery_fault == "none"
            else ("rollback_recovery_required" if action == "rollback"
                  else "post_activation_recovery_required"))
        assert len(starts) == 3
        assert [source for _, source in starts] == [
            (previous if action == "rollback" else apps / ("." + consumer.APP_NAME + ".app.installing"))
            / "Contents" / "Resources" / "release",
            app / "Contents" / "Resources" / "release",
            app / "Contents" / "Resources" / "release",
        ]
        assert all(python == source.parent / "runtime" / "bin" / "python"
                   for python, source in starts)
        # No later rename, deletion, cleanup, retry, or speculative activation
        # may erase the exact post-health bytes, aliases, modes, or inodes.
        _assert_retirement_inventory(apps, after_health)
        assert inode_inventory() == after_inodes
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("recovery_fault", [
    "retained_resealed", "retained_replaced", "quarantine_replaced",
    "destination_occupied", "destination_alias",
])
def test_first_recovery_move_rechecks_both_roles_before_restoring_private_authority(
    tmp_path, monkeypatch, action, recovery_fault
):
    """No second recovery move may use changed authority after the first."""
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(
        tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    retained = failed if action == "rollback" else previous
    quarantine = previous if action == "rollback" else failed
    displaced = apps / ".preserved-recovery-owner"
    actual_rename = Path.rename
    actual_start = consumer._candidate_starts
    starts = []
    moves_after_fault = []
    after_fault = {}

    def locations():
        return {path.relative_to(apps).as_posix():
                (path.lstat().st_dev, path.lstat().st_ino)
                for path in [apps, *apps.rglob("*")]}

    def start(python, source):
        healthy = actual_start(python, source)
        assert healthy
        starts.append(source)
        return False if len(starts) == 2 else healthy

    def rename(source, target):
        target = Path(target)
        if after_fault:
            moves_after_fault.append((source, target))
        result = actual_rename(source, target)
        if source == app and target == quarantine and len(starts) == 2:
            assert retained.is_dir() and quarantine.is_dir()
            assert not app.exists() and not app.is_symlink()
            if recovery_fault == "retained_resealed":
                _reseal_changed_source(retained)
            elif recovery_fault in {"retained_replaced", "quarantine_replaced"}:
                changed = retained if recovery_fault == "retained_replaced" else quarantine
                actual_rename(changed, displaced)
                shutil.copytree(displaced, changed, symlinks=True)
                assert changed.stat().st_ino != displaced.stat().st_ino
                assert consumer._trusted_bundle(changed)
            elif recovery_fault == "destination_occupied":
                app.mkdir()
                assert not list(app.iterdir())
            else:
                app.symlink_to(tmp_path / "missing-foreign-recovery", target_is_directory=True)
                assert app.is_symlink() and not app.exists()
            after_fault["inventory"] = _retirement_inventory(apps)
            after_fault["locations"] = locations()
        return result

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        monkeypatch.setattr(Path, "rename", rename)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert after_fault, "first recovery rename must actually complete before fault"
        assert result["ok"] is False
        assert result["reason"] == (
            "rollback_recovery_required" if action == "rollback"
            else "post_activation_recovery_required")
        assert len(starts) == 2
        assert moves_after_fault == []
        _assert_retirement_inventory(apps, after_fault["inventory"])
        assert locations() == after_fault["locations"]
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass


@pytest.mark.parametrize("action", ["install", "rollback"])
@pytest.mark.parametrize("health_fault", [
    "none", "unhealthy", "current_resealed", "current_replaced",
    "retained_resealed", "retained_replaced", "recovery_occupied", "recovery_alias",
])
def test_activation_rename_recovery_requires_actual_final_path_health_before_cleanup(
    tmp_path, monkeypatch, action, health_fault
):
    """Every moved restore proves health; uncertainty retains complete evidence."""
    import shutil
    from contextlib import closing
    from test_task_state_compatibility_v1 import authority
    from executor.autonomy.state_compatibility import verify_task_state_backup
    from executor.autonomy.worker import ProcessLock

    repo, apps, state, db, key = _identity_transaction_fixture(
        tmp_path, rollback=action == "rollback")
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    failed = apps / ("." + consumer.APP_NAME + ".app.failed")
    staging = apps / ("." + consumer.APP_NAME + ".app.installing")
    candidate = previous if action == "rollback" else staging
    retained = failed if action == "rollback" else previous
    current_inode = app.stat().st_ino
    current_before = _identity_file_inventory(app)
    actual_rename = Path.rename
    actual_start = consumer._candidate_starts
    renames = []
    starts = []
    candidate_before = {}
    candidate_inode = []
    after_health = {}
    after_inodes = {}

    def inode_inventory():
        return {path.relative_to(apps).as_posix():
                (path.lstat().st_dev, path.lstat().st_ino)
                for path in apps.rglob("*")}

    def rename(source, target):
        if source == candidate and Path(target) == app and not renames:
            assert not app.exists() and not app.is_symlink()
            assert retained.stat().st_ino == current_inode
            assert _identity_file_inventory(retained) == current_before
            candidate_before.update(_identity_file_inventory(candidate))
            candidate_inode.append(candidate.stat().st_ino)
            renames.append((source, Path(target)))
            raise OSError("PRIVATE_SYNTHETIC_ACTIVATION_RENAME_ERROR")
        if renames:
            renames.append((source, Path(target)))
        return actual_rename(source, target)

    def start(python, source):
        healthy = actual_start(python, source)
        assert healthy
        starts.append((python, source))
        if len(starts) == 2:
            assert source == app / "Contents" / "Resources" / "release"
            assert app.stat().st_ino == current_inode
            assert _identity_file_inventory(app) == current_before
            assert _identity_file_inventory(candidate) == candidate_before
            assert candidate.stat().st_ino == candidate_inode[0]
            assert not retained.exists() and not retained.is_symlink()
            if health_fault in {"current_resealed", "retained_resealed"}:
                _reseal_changed_source(app if health_fault == "current_resealed" else candidate)
            elif health_fault in {"current_replaced", "retained_replaced"}:
                target = app if health_fault == "current_replaced" else candidate
                preserved = apps / ("preserved-current-health" if target == app else "preserved-candidate-health")
                actual_rename(target, preserved)
                shutil.copytree(preserved, target, symlinks=True)
                assert target.stat().st_ino != preserved.stat().st_ino
                assert consumer._trusted_bundle(target)
            elif health_fault == "recovery_occupied":
                retained.mkdir()
                (retained / "foreign-evidence.txt").write_text("PRIVATE_FOREIGN_RESTORED_HEALTH_EVIDENCE")
            elif health_fault == "recovery_alias":
                retained.symlink_to(apps / "missing-health-recovery-slot", target_is_directory=True)
                assert retained.is_symlink() and not retained.exists()
            after_health.update(_retirement_inventory(apps))
            after_inodes.update(inode_inventory())
            return health_fault != "unhealthy"
        return healthy

    with closing(db):
        expected_authority = authority(db)
        expected_answers = db.execute("SELECT * FROM task_answer_events").fetchall()
        private_before = _identity_file_inventory(state)
        monkeypatch.setattr(Path, "rename", rename)
        monkeypatch.setattr(consumer, "_candidate_starts", start)
        result = (rollback_macos_app(apps, task_state_root=state) if action == "rollback"
                  else install_macos_app(repo, destination=apps, platform="darwin",
                                         task_state_root=state))
        assert result["ok"] is False
        assert len(starts) == 2
        assert [source for _, source in starts] == [
            candidate / "Contents" / "Resources" / "release",
            app / "Contents" / "Resources" / "release",
        ]
        assert all(python == source.parent / "runtime" / "bin" / "python"
                   for python, source in starts)
        assert renames == [(candidate, app), (retained, app)]
        if health_fault == "none":
            assert result["reason"] == ("rollback_activation_failed" if action == "rollback"
                                       else "activation_failed")
            assert app.stat().st_ino == current_inode
            assert _identity_file_inventory(app) == current_before
            assert not retained.exists() and not retained.is_symlink()
            if action == "rollback":
                _assert_retirement_inventory(apps, after_health)
                assert inode_inventory() == after_inodes
                assert _identity_file_inventory(candidate) == candidate_before
            else:
                assert not candidate.exists() and not candidate.is_symlink()
        else:
            assert result["reason"] == ("manual_recovery_required" if action == "rollback"
                                       else "rollback_required")
            _assert_retirement_inventory(apps, after_health)
            assert inode_inventory() == after_inodes
            assert candidate.is_dir(), "uncertain health cannot delete staging evidence"
        assert _identity_file_inventory(state) == private_before
        assert authority(db) == expected_authority
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == expected_answers
        assert (state / "task-answers.key").read_bytes() == key
        backup = result["task_state_backup"]
        assert verify_task_state_backup(Path(backup["path"]), backup["receipt"])
        assert backup["receipt"]["activation"] == "NOT_AUTHORIZED"
        assert "PRIVATE_" not in json.dumps(result)
        assert not (state / "auth.token").exists()
        assert not (state / "service.json").exists()
    with ProcessLock(state / "worker.lock"):
        pass
