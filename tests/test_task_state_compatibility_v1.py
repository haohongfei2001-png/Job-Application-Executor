from __future__ import annotations

from contextlib import closing
import sqlite3
import json
import os
import sys
from pathlib import Path

import pytest

from executor.autonomy.state_compatibility import (
    task_state_candidate_compatible, task_state_guard,
)
from executor.autonomy.worker import ProcessLock


def legacy_state(root):
    root.mkdir()
    db = sqlite3.connect(root / "tasks.sqlite3")
    db.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            spec TEXT NOT NULL, stage TEXT NOT NULL, checkpoint TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL,
            next_run REAL NOT NULL DEFAULT 0, blocker TEXT,
            details TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, at REAL,
            kind TEXT NOT NULL, stage TEXT NOT NULL);
    """)
    assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute(
        "INSERT INTO tasks(task_id,idempotency_key,spec,stage,checkpoint,blocker,created,updated) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("synthetic-task", "synthetic-key", '{"synthetic":true}', "BLOCKED",
         "FORM_FILLED", "unknown_outcome", 1, 2),
    )
    db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(?,?,?,?)",
               ("synthetic-task", 2, "unknown_outcome", "BLOCKED"))
    db.commit()
    return db


def candidate(tmp_path, operation):
    release = tmp_path / "release"
    package = release / "executor" / "autonomy"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "queue.py").write_text(
        "import sqlite3\nfrom pathlib import Path\n"
        "class TaskQueue:\n"
        "    def __init__(self, root):\n"
        "        with sqlite3.connect(Path(root)/'tasks.sqlite3') as db:\n"
        "            assert db.execute('SELECT blocker FROM tasks').fetchone()[0] == 'unknown_outcome'\n"
        + "            " + operation + "\n"
    )
    return release


def authority(db):
    return (db.execute("SELECT * FROM tasks").fetchall(),
            db.execute("SELECT * FROM events").fetchall())


def test_current_queue_migrates_only_the_consistent_wal_backup(tmp_path):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        with task_state_guard(root):
            assert task_state_candidate_compatible(
                Path(sys.executable), Path(__file__).resolve().parents[1], root)
        assert authority(db) == before
        assert "revision" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
        assert not (root / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
        assert not (root / "auth.token").exists()
        assert not (root / "service.json").exists()


@pytest.mark.parametrize("operation,compatible", [
    ("db.execute('ALTER TABLE tasks ADD COLUMN new_metadata TEXT')", True),
    ("db.execute('DELETE FROM tasks')", False),
    ("db.execute(\"UPDATE tasks SET blocker=NULL,stage='DISCOVERED'\")", False),
    ("db.execute('DROP TABLE events')", False),
    ("db.execute('ALTER TABLE tasks DROP COLUMN checkpoint')", False),
    ("raise RuntimeError('synthetic migration failure')", False),
])
def test_candidate_preserves_all_existing_authority_or_is_refused(tmp_path, operation, compatible):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        release = candidate(tmp_path, operation)
        with task_state_guard(root):
            assert task_state_candidate_compatible(Path(sys.executable), release, root) is compatible
        assert authority(db) == before
        assert not (root / "auth.token").exists()


def test_state_guard_uses_the_live_daemon_lock_even_for_a_new_state_directory(tmp_path):
    root = tmp_path / "state"
    with task_state_guard(root):
        with pytest.raises(RuntimeError, match="another local worker"):
            with ProcessLock(root / "worker.lock"):
                pass
    with ProcessLock(root / "worker.lock"):
        with pytest.raises(BlockingIOError):
            with task_state_guard(root):
                pass
    with task_state_guard(root):
        pass


@pytest.mark.parametrize("name", ["tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm", "worker.lock", "service.json"])
def test_state_guard_refuses_symlinked_state_and_never_writes_the_target(tmp_path, name):
    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("synthetic untouched")
    (root / name).symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        with task_state_guard(root):
            pass
    assert outside.read_text() == "synthetic untouched"


def test_compatibility_refuses_unrecognized_or_corrupt_state(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    database = root / "tasks.sqlite3"
    database.write_text("synthetic not a database")
    assert not task_state_candidate_compatible(Path(sys.executable), tmp_path, root)
    assert database.read_text() == "synthetic not a database"


def test_empty_state_is_compatible_without_starting_any_candidate(tmp_path):
    assert task_state_candidate_compatible(tmp_path / "nonexistent-python", tmp_path, tmp_path / "absent")

@pytest.mark.parametrize("mutation", [
    "CREATE TABLE tasks_copy AS SELECT * FROM tasks; DROP TABLE tasks; ALTER TABLE tasks_copy RENAME TO tasks",
    "CREATE TABLE events_copy(seq INTEGER PRIMARY KEY,task_id TEXT,at REAL,kind TEXT NOT NULL,stage TEXT NOT NULL); INSERT INTO events_copy SELECT * FROM events; DROP TABLE events; ALTER TABLE events_copy RENAME TO events",
    "DROP INDEX task_authority_by_blocker",
    "DROP INDEX task_unique_checkpoint",
    "DROP TRIGGER task_stage_fence",
    "DROP TRIGGER task_stage_fence; CREATE TRIGGER task_stage_fence BEFORE UPDATE ON tasks WHEN NEW.stage='SYNTHETIC_FORBIDDEN' BEGIN SELECT 1; END",
])
def test_equal_rows_cannot_hide_removed_authority_schema(tmp_path, mutation):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        db.executescript("""
            CREATE INDEX task_authority_by_blocker ON tasks(blocker,stage);
            CREATE UNIQUE INDEX task_unique_checkpoint ON tasks(idempotency_key,checkpoint)
                WHERE stage='BLOCKED';
            CREATE TRIGGER task_stage_fence BEFORE UPDATE ON tasks
                WHEN NEW.stage='SYNTHETIC_FORBIDDEN'
                BEGIN SELECT RAISE(ABORT,'synthetic stage fence'); END;
        """)
        before = authority(db)
        schema_before = db.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        release = candidate(tmp_path, "db.executescript(" + repr(mutation) + ")")
        with task_state_guard(root):
            assert not task_state_candidate_compatible(Path(sys.executable), release, root)
        assert authority(db) == before
        assert db.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema_before


def test_additive_queue_migration_keeps_existing_constraint_index_and_trigger_floor(tmp_path):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        db.executescript("""
            CREATE INDEX task_authority_by_blocker ON tasks(blocker,stage);
            CREATE UNIQUE INDEX task_unique_checkpoint ON tasks(idempotency_key,checkpoint)
                WHERE stage='BLOCKED';
            CREATE TRIGGER task_stage_fence BEFORE UPDATE ON tasks
                WHEN NEW.stage='SYNTHETIC_FORBIDDEN'
                BEGIN SELECT RAISE(ABORT,'synthetic stage fence'); END;
        """)
        before = authority(db)
        with task_state_guard(root):
            assert task_state_candidate_compatible(
                Path(sys.executable), Path(__file__).resolve().parents[1], root)
        assert authority(db) == before
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE tasks SET stage='SYNTHETIC_FORBIDDEN'")
        db.rollback()
        assert authority(db) == before

def test_state_guard_refuses_live_legacy_service_even_when_worker_lock_is_free(tmp_path):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        registry = root / "service.json"
        registry.write_text(json.dumps({"pid": os.getpid(), "port": 12345,
                                       "private_canary": "synthetic-only"}))
        before_record = registry.read_bytes()
        with pytest.raises(BlockingIOError, match="task_state_in_use") as failure:
            with task_state_guard(root):
                pytest.fail("live pre-lock daemon must block release mutation")
        assert "private_canary" not in str(failure.value)
        assert authority(db) == before
        assert registry.read_bytes() == before_record
        # Failure released the flock; the service is still alive and untouched.
        with ProcessLock(root / "worker.lock"):
            pass
        assert os.getpid() == json.loads(registry.read_text())["pid"]


@pytest.mark.parametrize("raw", [
    "not-json", "[]", "{}", '{"pid":true}', '{"pid":"1"}',
    '{"pid":0}', '{"pid":-1}', '{"pid":2147483648}', " " * 65537,
])
def test_ambiguous_service_record_cannot_certify_a_stopped_writer(tmp_path, raw):
    root = tmp_path / "state"
    root.mkdir()
    registry = root / "service.json"
    registry.write_text(raw)
    with pytest.raises(ValueError, match="task_service_record_invalid"):
        with task_state_guard(root):
            pytest.fail("ambiguous writer must refuse")
    assert registry.read_text() == raw


def test_dead_service_record_is_retained_without_killing_or_starting_a_process(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility
    root = tmp_path / "state"
    root.mkdir()
    registry = root / "service.json"
    raw = json.dumps({"pid": 12345, "port": 34567})
    registry.write_text(raw)
    calls = []

    def dead(pid, signal):
        calls.append((pid, signal))
        raise ProcessLookupError

    monkeypatch.setattr(state_compatibility.os, "kill", dead)
    with task_state_guard(root):
        pass
    assert calls == [(12345, 0)], "probe never terminates a process"
    assert registry.read_text() == raw


def test_uninspectable_service_pid_is_not_a_stopped_writer(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility
    root = tmp_path / "state"
    root.mkdir()
    registry = root / "service.json"
    registry.write_text(json.dumps({"pid": 12345}))

    def denied(pid, signal):
        assert (pid, signal) == (12345, 0)
        raise PermissionError

    monkeypatch.setattr(state_compatibility.os, "kill", denied)
    with pytest.raises(BlockingIOError):
        with task_state_guard(root):
            pytest.fail("permission denial is not proof of absence")
    assert json.loads(registry.read_text())["pid"] == 12345


def test_service_fifo_is_refused_without_blocking_or_consuming_it(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    os.mkfifo(root / "service.json")
    with pytest.raises(ValueError, match="task_service_record_invalid"):
        with task_state_guard(root):
            pass
