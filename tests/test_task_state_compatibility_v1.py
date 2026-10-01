from __future__ import annotations

from contextlib import closing, contextmanager, nullcontext
import sqlite3
import json
import os
import sys
import fcntl
import subprocess
import threading
from pathlib import Path

import pytest

from executor.autonomy.state_compatibility import (
    task_state_candidate_compatible, task_state_guard, stage_task_state_backup,
    verify_task_state_backup, task_state_backup_candidate_compatible,
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

def encrypted_answers(root, db):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    path = root / "task-answers.key"
    path.write_bytes(key)
    path.chmod(0o600)
    db.execute("""CREATE TABLE task_answer_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL, field_key TEXT NOT NULL, ciphertext BLOB NOT NULL,
        answer_version INTEGER NOT NULL, source TEXT NOT NULL,
        task_revision INTEGER NOT NULL, created REAL NOT NULL,
        reuse_requested INTEGER NOT NULL DEFAULT 0,
        reuse_applied INTEGER NOT NULL DEFAULT 0)""")
    values = [
        ("synthetic-task", "family.primary.role", "PRIVATE_OLD_ANSWER_CANARY", 1),
        ("synthetic-task", "family.primary.role", "PRIVATE_LATEST_ANSWER_CANARY", 2),
        ("synthetic-task", "preferences.accept_travel", False, 1),
        ("synthetic-task", "education.highest.graduation_date", 2026, 1),
        ("another-task", "family.primary.role", "PRIVATE_OTHER_TASK_CANARY", 1),
    ]
    for task, field, value, version in values:
        db.execute("""INSERT INTO task_answer_events
            (task_id,field_key,ciphertext,answer_version,source,task_revision,created)
            VALUES(?,?,?,?,?,?,?)""", (task, field, Fernet(key).encrypt(
                json.dumps(value).encode()), version, "user_explicit_task", 1, 2))
    db.commit()
    return key


def answer_candidate(tmp_path, mutation=""):
    import shutil
    release = tmp_path / "answer-release"
    actual = Path(__file__).resolve().parents[1]
    shutil.copytree(actual / "executor", release / "executor",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if mutation:
        with (release / "executor" / "facts" / "answers.py").open("a") as handle:
            handle.write("\n_original_load = TaskAnswerStore.load\n"
                         "def _changed_load(self, task_id):\n"
                         "    values = _original_load(self, task_id)\n"
                         + mutation + "\n"
                         "TaskAnswerStore.load = _changed_load\n")
    return release


@pytest.mark.parametrize("mutation,compatible", [
    ("", True),
    ("    return {}", False),
    ("    return {'family.primary.role': 'guessed substitute'}", False),
    ("    return {key: str(value) for key,value in values.items()}", False),
    ("    raise RuntimeError('synthetic unreadable answer')", False),
    ("    (self.queue.root/'task-answers.key').write_bytes(Fernet.generate_key())\n"
     "    return values", False),
])
def test_candidate_must_recover_latest_typed_encrypted_answers_with_the_same_key(
    tmp_path, mutation, compatible
):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        events = db.execute("SELECT * FROM task_answer_events").fetchall()
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        release = answer_candidate(tmp_path, mutation)
        with task_state_guard(root):
            assert task_state_candidate_compatible(
                Path(sys.executable), release, root) is compatible
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == events
        assert (root / "task-answers.key").read_bytes() == key
        assert (root / "task-answers.key").stat().st_mode & 0o077 == 0
        assert not (root / ".answer-compatibility.json").exists()
        assert not (root / "auth.token").exists()
        assert not (root / "service.json").exists()
        for path in root.iterdir():
            if path.is_file():
                assert b"PRIVATE_LATEST_ANSWER_CANARY" not in path.read_bytes()


@pytest.mark.parametrize("defect", ["missing", "invalid", "public", "symlink", "fifo", "ciphertext"])
def test_ambiguous_encryption_authority_refuses_before_any_candidate_runs(
    tmp_path, monkeypatch, defect
):
    from executor.autonomy import state_compatibility
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        encrypted_answers(root, db)
        key = root / "task-answers.key"
        outside = tmp_path / "outside-key"
        outside.write_bytes(key.read_bytes())
        if defect == "missing":
            key.unlink()
        elif defect == "invalid":
            key.write_bytes(b"!" * 44)
        elif defect == "public":
            key.chmod(0o644)
        elif defect == "symlink":
            key.unlink()
            key.symlink_to(outside)
        elif defect == "fifo":
            key.unlink()
            os.mkfifo(key)
        else:
            db.execute("UPDATE task_answer_events SET ciphertext=?", (b"unreadable",))
            db.commit()
        before = authority(db)
        events = db.execute("SELECT * FROM task_answer_events").fetchall()
        outside_before = outside.read_bytes()

        def forbidden_candidate(*_args, **_kwargs):
            pytest.fail("unreadable key/answer must refuse before candidate startup")

        monkeypatch.setattr(state_compatibility.subprocess, "run", forbidden_candidate)
        assert not task_state_candidate_compatible(Path(sys.executable), tmp_path, root)
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == events
        assert outside.read_bytes() == outside_before


def test_state_guard_fences_queue_schema_initializer_and_releases_after_refusal(tmp_path):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        declaration = db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()
        with (root / "migration.lock").open("a+") as initializer:
            fcntl.flock(initializer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError):
                with task_state_guard(root):
                    pytest.fail("guard cannot pass an active schema initializer")
            # Failure to acquire the second fence releases the worker fence.
            with ProcessLock(root / "worker.lock"):
                pass
        assert authority(db) == before
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone() == declaration
        with task_state_guard(root):
            with (root / "migration.lock").open("a+") as initializer:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(initializer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (root / "migration.lock").open("a+") as initializer:
            fcntl.flock(initializer, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_real_queue_constructor_cannot_initialize_guarded_state_in_another_process(tmp_path):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone()
        fields = ",".join('"' + row[1].replace('"', '""') + '"'
                          for row in db.execute("PRAGMA table_info(tasks)"))
        process = None
        try:
            with task_state_guard(root):
                process = subprocess.Popen(
                    [sys.executable, "-c",
                     "from pathlib import Path; from executor.autonomy.queue import TaskQueue; "
                     "import sys; print('starting', flush=True); "
                     "TaskQueue(Path(sys.argv[1])); print('initialized', flush=True)",
                     str(root)],
                    cwd=Path(__file__).resolve().parents[1],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                assert process.stdout.readline().strip() == "starting"
                with pytest.raises(subprocess.TimeoutExpired):
                    process.communicate(timeout=0.3)
                assert authority(db) == before
                assert db.execute("SELECT sql FROM sqlite_master WHERE name='tasks'").fetchone() == schema
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            assert "initialized" in stdout
            assert db.execute("SELECT " + fields + " FROM tasks").fetchall() == before[0]
            assert db.execute("SELECT * FROM events").fetchall() == before[1]
            assert "revision" in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


@pytest.mark.parametrize("name", ["worker.lock", "migration.lock"])
@pytest.mark.parametrize("kind", ["hardlink", "fifo", "directory"])
def test_state_transaction_refuses_nonordinary_locks_without_changing_payload_or_permissions(
    tmp_path, name, kind
):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        outside = tmp_path / "outside"
        outside.write_text("synthetic immutable lock canary")
        outside.chmod(0o644)
        lock = root / name
        if kind == "hardlink":
            os.link(outside, lock)
        elif kind == "fifo":
            os.mkfifo(lock, 0o644)
        else:
            lock.mkdir()
        metadata = lock.stat()
        with pytest.raises((ValueError, OSError)):
            with task_state_guard(root):
                pytest.fail("nonordinary lock must refuse before candidate or activation")
        assert authority(db) == before
        assert lock.stat().st_mode == metadata.st_mode
        assert outside.read_text() == "synthetic immutable lock canary"
        assert outside.stat().st_mode & 0o777 == 0o644
        lock.unlink() if kind != "directory" else lock.rmdir()
        with task_state_guard(root):
            pass


def test_queue_keeps_migration_fence_through_schema_transaction(tmp_path, monkeypatch):
    from executor.autonomy.queue import TaskQueue

    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        entered, release_initializer = threading.Event(), threading.Event()
        original_tx = TaskQueue.tx
        errors = []

        @contextmanager
        def held_schema_transaction(queue):
            entered.set()
            if not release_initializer.wait(timeout=5):
                raise RuntimeError("synthetic initializer release missing")
            with original_tx(queue) as transaction:
                yield transaction

        monkeypatch.setattr(TaskQueue, "tx", held_schema_transaction)

        def initialize():
            try:
                TaskQueue(root)
            except BaseException as error:
                errors.append(error)

        writer = threading.Thread(target=initialize, daemon=True)
        writer.start()
        try:
            assert entered.wait(timeout=5), "real initializer must reach schema boundary"
            with pytest.raises(BlockingIOError):
                with task_state_guard(root):
                    pytest.fail("backup completion cannot release the schema writer fence")
            assert authority(db) == before
            assert "revision" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        finally:
            release_initializer.set()
            writer.join(timeout=5)
        assert not writer.is_alive()
        assert not errors
        assert "revision" in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        with task_state_guard(root):
            pass


def test_durable_backup_captures_committed_wal_and_key_without_migrating_source(tmp_path):
    import hashlib

    root = tmp_path / "state"
    destination = tmp_path / "private-backup"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        events = db.execute("SELECT * FROM task_answer_events").fetchall()
        schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        # Other private files remain exclusively in the source authority.
        (root / "auth.token").write_text("PRIVATE_BACKUP_TOKEN_CANARY")
        (root / "profile.json").write_text("PRIVATE_BACKUP_PROFILE_CANARY")
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        receipt = stage_task_state_backup(root, destination)
        assert sorted(path.name for path in destination.iterdir()) == [
            "backup-manifest.json", "task-answers.key", "tasks.sqlite3"]
        assert destination.stat().st_mode & 0o077 == 0
        assert all(path.stat().st_mode & 0o077 == 0 for path in destination.iterdir())
        assert json.loads((destination / "backup-manifest.json").read_text()) == receipt
        assert receipt["format"] == "jae-task-state-backup-v1"
        assert receipt["activation"] == "NOT_AUTHORIZED"
        assert receipt["legacy_writer_retirement"] == "NOT_CERTIFIED"
        assert receipt["source_authority"] == "unchanged"
        assert receipt["other_private_files"] == "excluded"
        assert receipt["answer_task_count"] == 2
        assert receipt["database_sha256"] == hashlib.sha256(
            (destination / "tasks.sqlite3").read_bytes()).hexdigest()
        assert receipt["answer_key_sha256"] == hashlib.sha256(key).hexdigest()
        with closing(sqlite3.connect(destination / "tasks.sqlite3")) as backup:
            assert authority(backup) == before
            assert backup.execute("SELECT * FROM task_answer_events").fetchall() == events
            assert backup.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == schema
        assert (destination / "task-answers.key").read_bytes() == key
        saved = {path.name: path.read_bytes() for path in destination.iterdir()}
        # Current candidate schema/decoder runs only against another disposable
        # copy; the durable backup and original journal both stay immutable.
        assert task_state_candidate_compatible(
            Path(sys.executable), Path(__file__).resolve().parents[1], destination)
        assert {path.name: path.read_bytes() for path in destination.iterdir()} == saved
        with pytest.raises(ValueError, match="destination_unavailable"):
            stage_task_state_backup(root, destination)
        assert {path.name: path.read_bytes() for path in destination.iterdir()} == saved
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == events
        assert db.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == schema
        assert (root / "task-answers.key").read_bytes() == key
        assert (root / "auth.token").read_text() == "PRIVATE_BACKUP_TOKEN_CANARY"
        assert (root / "profile.json").read_text() == "PRIVATE_BACKUP_PROFILE_CANARY"
        for path in destination.iterdir():
            assert b"PRIVATE_LATEST_ANSWER_CANARY" not in path.read_bytes()
            assert b"PRIVATE_BACKUP_TOKEN_CANARY" not in path.read_bytes()
            assert b"PRIVATE_BACKUP_PROFILE_CANARY" not in path.read_bytes()
        assert not (root / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
        assert not (root / "service.json").exists()


@pytest.mark.parametrize("fence", ["worker", "migration", "legacy_service"])
def test_backup_refuses_a_live_writer_before_creating_output(tmp_path, fence):
    root = tmp_path / "state"
    destination = tmp_path / "backup"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        if fence == "legacy_service":
            (root / "service.json").write_text(json.dumps({"pid": os.getpid(), "port": 9344}))
            guard = nullcontext()
        elif fence == "worker":
            guard = ProcessLock(root / "worker.lock")
        else:
            @contextmanager
            def migration_guard():
                with (root / "migration.lock").open("a+") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    yield
            guard = migration_guard()
        with guard:
            with pytest.raises((BlockingIOError, RuntimeError)):
                stage_task_state_backup(root, destination)
        assert not destination.exists()
        assert authority(db) == before


@pytest.mark.parametrize("defect", ["missing_key", "bad_ciphertext", "hardlinked_key", "key_fifo"])
def test_backup_never_publishes_unreadable_or_aliased_answer_authority(tmp_path, defect):
    root = tmp_path / "state"
    destination = tmp_path / "backup"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        key_path = root / "task-answers.key"
        outside = tmp_path / "outside-key"
        outside.write_bytes(key)
        if defect == "missing_key":
            key_path.unlink()
        elif defect == "bad_ciphertext":
            db.execute("UPDATE task_answer_events SET ciphertext=?", (b"unreadable",))
            db.commit()
        elif defect == "hardlinked_key":
            key_path.unlink()
            os.link(outside, key_path)
        else:
            key_path.unlink()
            os.mkfifo(key_path)
        before = authority(db)
        with pytest.raises((ValueError, OSError)):
            stage_task_state_backup(root, destination)
        assert not destination.exists()
        assert authority(db) == before
        assert outside.read_bytes() == key


def test_backup_publication_failure_preserves_unknown_artifact_and_original(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    destination = tmp_path / "backup"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        events = db.execute("SELECT * FROM task_answer_events").fetchall()
        def fail_receipt(source, target):
            assert Path(source).name == ".backup-manifest.tmp"
            assert sorted(path.name for path in destination.iterdir()) == [
                ".backup-manifest.tmp", "task-answers.key", "tasks.sqlite3"]
            (destination / "unknown-concurrent-artifact").write_text("UNCHANGED_UNKNOWN_CANARY")
            raise OSError("synthetic receipt publication failure")
        monkeypatch.setattr(state_compatibility.os, "link", fail_receipt)
        with pytest.raises(OSError, match="publication failure"):
            stage_task_state_backup(root, destination)
        assert sorted(path.name for path in destination.iterdir()) == ["unknown-concurrent-artifact"]
        assert (destination / "unknown-concurrent-artifact").read_text() == "UNCHANGED_UNKNOWN_CANARY"
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == events
        assert (root / "task-answers.key").read_bytes() == key
        with pytest.raises(ValueError, match="destination_unavailable"):
            stage_task_state_backup(root, destination)


@pytest.mark.parametrize("path_kind", ["ancestor_alias", "inside_source", "existing"])
def test_backup_refuses_destination_alias_overlap_or_existing_payload(tmp_path, path_kind):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        outside = tmp_path / "outside"
        outside.mkdir()
        canary = outside / "canary"
        canary.write_text("UNCHANGED_DESTINATION_CANARY")
        if path_kind == "ancestor_alias":
            alias = tmp_path / "alias"
            alias.symlink_to(outside, target_is_directory=True)
            destination = alias / "new"
        elif path_kind == "inside_source":
            destination = root / "backup"
        else:
            destination = outside
        with pytest.raises(ValueError):
            stage_task_state_backup(root, destination)
        assert sorted(path.name for path in outside.iterdir()) == ["canary"]
        assert canary.read_text() == "UNCHANGED_DESTINATION_CANARY"
        assert not (root / "backup").exists()
        assert authority(db) == before

def test_backup_refuses_key_rotation_before_publication_without_repairing_source(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    destination = tmp_path / "backup"
    with closing(legacy_state(root)) as db:
        old_key = encrypted_answers(root, db)
        rotated_key = Fernet.generate_key()
        before = authority(db)
        events = db.execute("SELECT * FROM task_answer_events").fetchall()
        original_contract = state_compatibility._answer_contract
        def rotate_after_snapshot(backup, key, secret):
            result = original_contract(backup, key, secret)
            (root / "task-answers.key").write_bytes(rotated_key)
            return result
        monkeypatch.setattr(state_compatibility, "_answer_contract", rotate_after_snapshot)
        with pytest.raises(ValueError, match="source_changed"):
            stage_task_state_backup(root, destination)
        assert not destination.exists()
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == events
        assert (root / "task-answers.key").read_bytes() == rotated_key
        assert rotated_key != old_key

def test_bound_backup_preflight_uses_real_candidate_decoder_without_touching_capsule(tmp_path):
    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        history = db.execute("SELECT * FROM task_answer_events").fetchall()
        receipt = stage_task_state_backup(root, capsule)
        payload = {path.name: path.read_bytes() for path in capsule.iterdir()}
        assert verify_task_state_backup(capsule, receipt)
        assert task_state_backup_candidate_compatible(
            Path(sys.executable), Path(__file__).resolve().parents[1], capsule, receipt)
        assert {path.name: path.read_bytes() for path in capsule.iterdir()} == payload
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == history
        assert (root / "task-answers.key").read_bytes() == key
        assert "revision" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
        assert not (capsule / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
        assert not (root / "service.json").exists()


def test_bound_backup_preflight_accepts_a_journal_without_answers_or_key(tmp_path):
    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)) as db:
        before = authority(db)
        receipt = stage_task_state_backup(root, capsule)
        assert receipt["answer_key_sha256"] is None
        assert receipt["answer_task_count"] == 0
        payload = {p.name: p.read_bytes() for p in capsule.iterdir()}
        assert verify_task_state_backup(capsule, receipt)
        assert task_state_backup_candidate_compatible(
            Path(sys.executable), Path(__file__).resolve().parents[1], capsule, receipt)
        assert {p.name: p.read_bytes() for p in capsule.iterdir()} == payload
        assert not (capsule / "task-answers.key").exists()
        assert not (root / "task-answers.key").exists()
        assert authority(db) == before


@pytest.mark.parametrize("defect", [
    "missing_manifest", "extra_file", "database_alias", "key_alias", "manifest_alias",
    "hardlinked_database", "hardlinked_key", "key_fifo", "public_directory",
    "public_database", "public_key", "invalid_manifest", "oversized_manifest",
    "rotated_key", "edited_database_and_manifest",
])
def test_bound_backup_refuses_payload_drift_before_any_candidate_runs(tmp_path, monkeypatch, defect):
    import hashlib
    from cryptography.fernet import Fernet
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        receipt = stage_task_state_backup(root, capsule)
        manifest = capsule / "backup-manifest.json"
        if defect == "missing_manifest":
            manifest.unlink()
        elif defect == "extra_file":
            (capsule / "unexpected").write_text("UNKNOWN_PAYLOAD_CANARY")
        elif defect.endswith("_alias") or defect in {"hardlinked_database", "hardlinked_key", "key_fifo"}:
            name = ("tasks.sqlite3" if "database" in defect else
                    "task-answers.key" if "key" in defect else "backup-manifest.json")
            path = capsule / name
            outside = tmp_path / ("outside-" + name)
            outside.write_bytes(path.read_bytes())
            outside.chmod(0o600)
            path.unlink()
            if defect.endswith("_alias"):
                path.symlink_to(outside)
            elif defect == "key_fifo":
                os.mkfifo(path)
            else:
                os.link(outside, path)
        elif defect == "public_directory":
            capsule.chmod(0o755)
        elif defect in {"public_database", "public_key"}:
            (capsule / ("tasks.sqlite3" if defect == "public_database"
                        else "task-answers.key")).chmod(0o644)
        elif defect == "invalid_manifest":
            manifest.write_text("not a receipt")
        elif defect == "oversized_manifest":
            manifest.write_text("x" * 65537)
        elif defect == "rotated_key":
            (capsule / "task-answers.key").write_bytes(Fernet.generate_key())
        else:
            with closing(sqlite3.connect(capsule / "tasks.sqlite3")) as changed:
                changed.execute("UPDATE tasks SET blocker=NULL")
                changed.commit()
            forged = {**receipt, "database_sha256": hashlib.sha256(
                (capsule / "tasks.sqlite3").read_bytes()).hexdigest()}
            manifest.write_text(json.dumps(forged))
        def forbidden_candidate(*args):
            pytest.fail("an unbound/changed backup must refuse before candidate execution")
        monkeypatch.setattr(state_compatibility, "task_state_candidate_compatible", forbidden_candidate)
        assert verify_task_state_backup(capsule, receipt) is False
        assert task_state_backup_candidate_compatible(
            Path(sys.executable), Path(__file__).resolve().parents[1], capsule, receipt) is False
        assert authority(db) == before
        assert (root / "task-answers.key").read_bytes() == key
        if defect == "extra_file":
            assert (capsule / "unexpected").read_text() == "UNKNOWN_PAYLOAD_CANARY"
        if defect.endswith("_alias") or defect.startswith("hardlinked_") or defect == "key_fifo":
            assert outside.exists()


@pytest.mark.parametrize("field,value", [
    ("activation", "AUTHORIZED"), ("legacy_writer_retirement", "CERTIFIED"),
    ("table_count", True), ("table_count", 999), ("answer_task_count", False),
    ("answer_task_count", 999), ("database_sha256", "unknown"),
])
def test_bound_backup_refuses_invalid_receipt_authority_or_counts(tmp_path, field, value):
    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)):
        receipt = stage_task_state_backup(root, capsule)
        invalid = {**receipt, field: value}
        # Even a matching on-disk manifest cannot promote authority or hide
        # wrong typed counts; receipt binding and actual DB inventory both matter.
        (capsule / "backup-manifest.json").write_text(json.dumps(invalid))
        assert verify_task_state_backup(capsule, invalid) is False


@pytest.mark.parametrize("mutation", [
    "    return {}",
    "    values = _original_load(self, task_id); values['preferences.accept_travel'] = 0; return values",
])
def test_bound_backup_candidate_preflight_refuses_decoder_loss_or_type_coercion(tmp_path, mutation):
    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)) as db:
        encrypted_answers(root, db)
        receipt = stage_task_state_backup(root, capsule)
        before = authority(db)
        payload = {p.name: p.read_bytes() for p in capsule.iterdir()}
        release = answer_candidate(tmp_path, mutation)
        assert task_state_backup_candidate_compatible(
            Path(sys.executable), release, capsule, receipt) is False
        assert verify_task_state_backup(capsule, receipt) is True
        assert {p.name: p.read_bytes() for p in capsule.iterdir()} == payload
        assert authority(db) == before


def test_bound_backup_is_rechecked_after_candidate_probe_and_never_repairs_changes(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        receipt = stage_task_state_backup(root, capsule)
        changed_key = Fernet.generate_key()
        def probe(*args):
            (capsule / "task-answers.key").write_bytes(changed_key)
            return True
        monkeypatch.setattr(state_compatibility, "task_state_candidate_compatible", probe)
        assert task_state_backup_candidate_compatible(
            Path(sys.executable), Path(__file__).resolve().parents[1], capsule, receipt) is False
        assert (capsule / "task-answers.key").read_bytes() == changed_key
        assert (root / "task-answers.key").read_bytes() == key
        assert authority(db) == before

def test_bound_backup_requires_delete_journal_header_even_with_matching_external_digest(tmp_path):
    import hashlib

    root = tmp_path / "state"
    capsule = tmp_path / "capsule"
    with closing(legacy_state(root)):
        receipt = stage_task_state_backup(root, capsule)
        with closing(sqlite3.connect(capsule / "tasks.sqlite3")) as changed:
            assert changed.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        observed = {**receipt, "database_sha256": hashlib.sha256(
            (capsule / "tasks.sqlite3").read_bytes()).hexdigest()}
        (capsule / "backup-manifest.json").write_text(json.dumps(observed))
        before = {p.name: p.read_bytes() for p in capsule.iterdir()}
        assert verify_task_state_backup(capsule, observed) is False
        assert {p.name: p.read_bytes() for p in capsule.iterdir()} == before


# Exact authority admission precedes every private state read/mutation.
@pytest.mark.parametrize("path_kind", [
    "root_alias", "ancestor_alias", "missing_child", "missing_grandchild",
])
def test_state_alias_admission_refuses_before_locks_or_candidate_reads(tmp_path, monkeypatch, path_kind):
    from executor.autonomy import state_compatibility

    actual = tmp_path / "owned"
    actual.mkdir()
    state = actual / "state"
    with closing(legacy_state(state)) as db:
        encrypted_answers(state, db)
        state.chmod(0o755)
        before = authority(db)
        history = db.execute("SELECT * FROM task_answer_events").fetchall()
        payload = {path.name: path.read_bytes() for path in state.iterdir()}
        mode = state.stat().st_mode
        alias = tmp_path / "alias"
        if path_kind == "root_alias":
            alias.symlink_to(state, target_is_directory=True)
            supplied = alias
        else:
            alias.symlink_to(actual, target_is_directory=True)
            supplied = alias / ("state" if path_kind == "ancestor_alias" else
                                "absent" if path_kind == "missing_child" else "absent/nested")

        def never(*args, **kwargs):
            pytest.fail("aliased authority reached private read or candidate startup")
        monkeypatch.setattr(state_compatibility, "_answer_key", never)
        monkeypatch.setattr(state_compatibility.subprocess, "run", never)
        with pytest.raises(ValueError, match="task_state_path_invalid"):
            with task_state_guard(supplied):
                pytest.fail("aliased authority admitted")
        assert task_state_candidate_compatible(Path(sys.executable), tmp_path, supplied) is False
        # Compare every private file, including volatile SQLite SHM, before
        # the fixture performs SELECTs that can update WAL-index read marks.
        assert {path.name: path.read_bytes() for path in state.iterdir()} == payload
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == history
        assert state.stat().st_mode == mode
        assert sorted(path.name for path in actual.iterdir()) == ["state"]
        assert not any((state / name).exists() for name in
                       ("native-window.lock", "worker.lock", "migration.lock"))
        assert alias.is_symlink()


def test_state_admission_refuses_foreign_root_before_chmod_or_private_read(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        encrypted_answers(root, db)
        root.chmod(0o755)
        mode = root.stat().st_mode
        before = authority(db)
        history = db.execute("SELECT * FROM task_answer_events").fetchall()
        # Establish semantic reads before the exact byte baseline.
        payload = {path.name: path.read_bytes() for path in root.iterdir()}
        current_uid = os.geteuid()
        monkeypatch.setattr(state_compatibility.os, "geteuid", lambda: current_uid + 1)
        def never(*args, **kwargs):
            pytest.fail("foreign authority reached private read or candidate startup")
        monkeypatch.setattr(state_compatibility, "_answer_key", never)
        monkeypatch.setattr(state_compatibility.subprocess, "run", never)
        with pytest.raises(ValueError, match="task_state_path_invalid"):
            with task_state_guard(root):
                pytest.fail("foreign root admitted")
        assert task_state_candidate_compatible(Path(sys.executable), tmp_path, root) is False
        assert root.stat().st_mode == mode
        # Reader queries mutate SHM read marks independently of admission.
        # Keep the full byte oracle around the product operation itself.
        assert {path.name: path.read_bytes() for path in root.iterdir()} == payload
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == history


@pytest.mark.parametrize("operation", ["install", "rollback"])
@pytest.mark.parametrize("path_kind", ["ancestor_alias", "missing_child"])
def test_app_transaction_refuses_aliased_authority_before_activation(tmp_path, monkeypatch, operation, path_kind):
    from executor.autonomy import consumer

    actual = tmp_path / "owned"
    actual.mkdir()
    state = actual / "state"
    apps = tmp_path / "Applications"
    apps.mkdir()
    app = apps / (consumer.APP_NAME + ".app")
    previous = apps / ("." + consumer.APP_NAME + ".app.previous")
    for path, canary in ((app, "CURRENT_APP_CANARY"), (previous, "PREVIOUS_APP_CANARY")):
        path.mkdir()
        (path / "retained").write_text(canary)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    supplied = alias / ("state" if path_kind == "ancestor_alias" else "absent")
    with closing(legacy_state(state)) as db:
        key = encrypted_answers(state, db)
        before = authority(db)
        state.chmod(0o755)
        mode = state.stat().st_mode
        payload = {path.name: path.read_bytes() for path in state.iterdir()}
        def never(*args, **kwargs):
            pytest.fail("aliased app transaction reached staging, health or activation")
        monkeypatch.setattr(consumer, "_install_macos_app_unlocked", never)
        monkeypatch.setattr(consumer, "_rollback_macos_app_unlocked", never)
        if operation == "install":
            result = consumer.install_macos_app(tmp_path / "unused-source",
                destination=apps, platform="darwin", task_state_root=supplied)
        else:
            result = consumer.rollback_macos_app(apps, task_state_root=supplied)
        assert result["ok"] is False
        assert result["reason"] == "task_state_unavailable"
        assert "task_state_backup" not in result
        assert "owned" not in json.dumps(result)
        # Observe all bytes before fixture SELECTs can alter SHM read marks.
        assert {path.name: path.read_bytes() for path in state.iterdir()} == payload
        assert authority(db) == before
        assert (state / "task-answers.key").read_bytes() == key
        assert state.stat().st_mode == mode
        assert sorted(path.name for path in actual.iterdir()) == ["state"]
        assert (app / "retained").read_text() == "CURRENT_APP_CANARY"
        assert (previous / "retained").read_text() == "PREVIOUS_APP_CANARY"
        assert not (apps / ("." + consumer.APP_NAME + ".app.installing")).exists()
        assert not (apps / ("." + consumer.APP_NAME + ".app.failed")).exists()
        assert not list(apps.glob(".jae-task-state-backup-*"))
        # The refused transaction released its application lock.
        fd = consumer._acquire_app_transaction_lock(apps)
        os.close(fd)


# The production daemon and release transaction must acquire the same owned
# inode. These cases exercise ProcessLock itself, not a replacement test lock.
@pytest.mark.parametrize("path_kind", [
    "root_alias", "ancestor_alias", "missing_child", "missing_grandchild",
])
def test_worker_lock_refuses_aliased_authority_before_permissions_or_private_reads(
        tmp_path, path_kind):
    actual = tmp_path / "owned"
    actual.mkdir()
    root = actual / "state"
    with closing(legacy_state(root)) as db:
        encrypted_answers(root, db)
        before = authority(db)
        history = db.execute("SELECT * FROM task_answer_events").fetchall()
        root.chmod(0o755)
        payload = {path.name: path.read_bytes() for path in root.iterdir()}
        mode = root.stat().st_mode
        alias = tmp_path / "alias"
        if path_kind == "root_alias":
            alias.symlink_to(root, target_is_directory=True)
            supplied = alias
        else:
            alias.symlink_to(actual, target_is_directory=True)
            supplied = alias / ("state" if path_kind == "ancestor_alias" else
                                "absent" if path_kind == "missing_child" else "absent/nested")
        with pytest.raises(ValueError, match="worker_state_path_invalid") as failure:
            with ProcessLock(supplied / "worker.lock"):
                pytest.fail("aliased worker authority admitted")
        assert str(failure.value) == "worker_state_path_invalid"
        assert {path.name: path.read_bytes() for path in root.iterdir()} == payload
        assert root.stat().st_mode == mode
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == history
        assert sorted(path.name for path in actual.iterdir()) == ["state"]
        assert alias.is_symlink()
        assert not (root / "worker.lock").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_worker_lock_refuses_nonordinary_inode_without_reading_or_chmodding_it(
        tmp_path, kind):
    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "outside-private"
    outside.write_bytes(b"SYNTHETIC_PRIVATE_LOCK_CANARY")
    outside.chmod(0o640)
    before_mode = outside.stat().st_mode
    path = root / "worker.lock"
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    elif kind == "fifo":
        os.mkfifo(path, 0o640)
    else:
        path.mkdir(mode=0o750)
    entry = path.stat(follow_symlinks=False)
    before = (entry.st_dev, entry.st_ino, entry.st_mode, entry.st_nlink)
    guard = ProcessLock(path)
    with pytest.raises(RuntimeError, match="local worker lock unavailable") as failure:
        with guard:
            pytest.fail("nonordinary worker inode admitted")
    assert str(failure.value) == "local worker lock unavailable"
    assert guard.handle is None
    assert outside.read_bytes() == b"SYNTHETIC_PRIVATE_LOCK_CANARY"
    assert outside.stat().st_mode == before_mode
    entry = path.stat(follow_symlinks=False)
    assert (entry.st_dev, entry.st_ino, entry.st_mode, entry.st_nlink) == before
    assert sorted(p.name for p in root.iterdir()) == ["worker.lock"]


def test_worker_lock_refuses_foreign_root_before_chmod_or_file_creation(tmp_path, monkeypatch):
    from executor.autonomy import worker

    root = tmp_path / "state"
    root.mkdir(mode=0o750)
    private = root / "retained-private"
    private.write_bytes(b"SYNTHETIC_PRIVATE_ROOT_CANARY")
    mode = root.stat().st_mode
    current_uid = os.geteuid()
    monkeypatch.setattr(worker.os, "geteuid", lambda: current_uid + 1)
    with pytest.raises(ValueError, match="worker_state_path_invalid"):
        with ProcessLock(root / "worker.lock"):
            pytest.fail("foreign worker root admitted")
    assert root.stat().st_mode == mode
    assert private.read_bytes() == b"SYNTHETIC_PRIVATE_ROOT_CANARY"
    assert sorted(p.name for p in root.iterdir()) == ["retained-private"]


@pytest.mark.parametrize("replacement", ["ordinary", "symlink"])
def test_worker_lock_rechecks_inode_after_real_kernel_acquisition_and_releases_refusal(
        tmp_path, monkeypatch, replacement):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    root.mkdir()
    path = root / "worker.lock"
    path.write_bytes(b"SYNTHETIC_ORIGINAL_LOCK")
    path.chmod(0o640)
    original_mode = path.stat().st_mode
    displaced = root / "displaced"
    outside = tmp_path / "outside-private"
    outside.write_bytes(b"SYNTHETIC_REPLACEMENT_CANARY")
    outside.chmod(0o640)
    outside_mode = outside.stat().st_mode
    real_flock = fcntl.flock
    injected = []

    def replace_after_acquisition(fd, operation):
        real_flock(fd, operation)
        if not injected:
            injected.append(True)
            path.rename(displaced)
            if replacement == "symlink":
                path.symlink_to(outside)
            else:
                path.write_bytes(b"SYNTHETIC_UNEXPECTED_NEW_LOCK")
                path.chmod(0o640)

    with monkeypatch.context() as patch:
        patch.setattr(state_compatibility.fcntl, "flock", replace_after_acquisition)
        guard = ProcessLock(path)
        with pytest.raises(RuntimeError, match="local worker lock unavailable") as failure:
            with guard:
                pytest.fail("replaced inode admitted")
        assert str(failure.value) == "local worker lock unavailable"
        assert guard.handle is None
    assert injected == [True]
    assert displaced.read_bytes() == b"SYNTHETIC_ORIGINAL_LOCK"
    assert displaced.stat().st_mode == original_mode
    assert outside.read_bytes() == b"SYNTHETIC_REPLACEMENT_CANARY"
    assert outside.stat().st_mode == outside_mode
    if replacement == "symlink":
        assert path.is_symlink()
    else:
        assert path.read_bytes() == b"SYNTHETIC_UNEXPECTED_NEW_LOCK"
        assert path.stat().st_mode & 0o777 == 0o640
    # Independent real flock proves the rejected descriptor was closed.
    with displaced.open("a+") as retained:
        real_flock(retained, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert sorted(p.name for p in root.iterdir()) == ["displaced", "worker.lock"]


@pytest.mark.parametrize("holder", ["worker", "release"])
def test_worker_and_app_guard_share_one_inode_across_real_processes_without_authority_change(
        tmp_path, holder):
    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        history = db.execute("SELECT * FROM task_answer_events").fetchall()
        schema = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        parent = ProcessLock(root / "worker.lock") if holder == "worker" else task_state_guard(root)
        # The child imports and acquires the actual OTHER production guard.
        code = (
            "import sys\nfrom pathlib import Path\n"
            "from executor.autonomy.worker import ProcessLock\n"
            "from executor.autonomy.state_compatibility import task_state_guard\n"
            "root = Path(sys.argv[1])\n"
            + ("guard = task_state_guard(root)\n" if holder == "worker" else
               "guard = ProcessLock(root / 'worker.lock')\n")
            + "try:\n    with guard:\n        pass\n"
            "except (BlockingIOError, RuntimeError):\n"
            "    print('REFUSED', flush=True)\n    sys.exit(87)\n"
            "print('ACQUIRED', flush=True)\n"
        )
        with parent:
            lock = root / "worker.lock"
            identity = (lock.stat().st_dev, lock.stat().st_ino)
            payload = {p.name: p.read_bytes() for p in root.iterdir()
                       if p.name not in {"worker.lock", "migration.lock", "native-window.lock"}}
            child = subprocess.run([sys.executable, "-c", code, str(root)],
                                   capture_output=True, text=True, timeout=15)
            assert child.returncode == 87, child.stderr
            assert child.stdout.strip() == "REFUSED"
            assert not child.stderr
            assert (lock.stat().st_dev, lock.stat().st_ino) == identity
            assert {p.name: p.read_bytes() for p in root.iterdir()
                    if p.name not in {"worker.lock", "migration.lock", "native-window.lock"}} == payload
        reopened = subprocess.run([sys.executable, "-c", code, str(root)],
                                  capture_output=True, text=True, timeout=15)
        assert reopened.returncode == 0, reopened.stderr
        assert reopened.stdout.strip() == "ACQUIRED"
        assert not reopened.stderr
        assert (lock.stat().st_dev, lock.stat().st_ino) == identity
        assert lock.stat().st_mode & 0o777 == 0o600
        assert {p.name: p.read_bytes() for p in root.iterdir()
                if p.name not in {"worker.lock", "migration.lock", "native-window.lock"}} == payload
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == history
        assert db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema
        assert (root / "task-answers.key").read_bytes() == key
        assert not list(tmp_path.glob("backup*"))


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("entry", ["journal", "capsule"])
@pytest.mark.parametrize("mutation", ["", "delete_authority"])
def test_owned_compatibility_scratch_alias_uses_real_candidate_and_preserves_authority(
        tmp_path, monkeypatch, encrypted, entry, mutation):
    """An OS temporary-parent alias is not the caller's journal authority."""
    import hashlib
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    parent = (tmp_path / "temporary-parent").resolve()
    parent.mkdir()
    alias = tmp_path / "temporary-parent-alias"
    alias.symlink_to(parent, target_is_directory=True)
    alias_identity = alias.lstat()
    outside = parent / "unrelated"
    outside.write_bytes(b"PRIVATE_UNRELATED_SCRATCH_CANARY" * 400)
    outside.chmod(0o640)
    outside_before = (outside.read_bytes(), outside.stat().st_mode,
                      outside.stat().st_dev, outside.stat().st_ino)
    release = answer_candidate(tmp_path)
    if mutation:
        # Change only the disposable journal AFTER the real schema migration.
        with (release / "executor" / "autonomy" / "queue.py").open("a") as handle:
            handle.write(
                "\n_original_init = TaskQueue.__init__\n"
                "def _changed_init(self, *args, **kwargs):\n"
                "    _original_init(self, *args, **kwargs)\n"
                "    with self.tx() as db:\n"
                "        db.execute('DELETE FROM tasks')\n"
                "TaskQueue.__init__ = _changed_init\n")
    def source_inventory():
        return {str(p.relative_to(release)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in release.rglob("*") if p.is_file()}
    source_before = source_inventory()
    real_temporary = state_compatibility.tempfile.TemporaryDirectory
    real_run = state_compatibility.subprocess.run
    scratch = []
    admitted = []

    @contextmanager
    def aliased_owned_temporary(*args, **kwargs):
        assert kwargs["prefix"] == "jae-state-compatibility-"
        assert "dir" not in kwargs
        with real_temporary(*args, dir=parent, **kwargs) as created:
            canonical = Path(created).resolve(strict=True)
            lexical = alias / canonical.name
            assert lexical.is_dir()
            assert any(p.is_symlink() for p in lexical.parents)
            scratch.append(canonical)
            yield str(lexical)
        assert not canonical.exists()

    def actual_candidate(command, **kwargs):
        selected = Path(command[-1])
        assert selected == scratch[-1]
        assert not any(p.is_symlink() for p in (selected, *selected.parents))
        assert Path(command[-2]) == release
        assert command[1:4] == ["-I", "-B", "-c"]
        assert kwargs["timeout"] == 10
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        # This is the actual isolated candidate queue/decoder, not a result mock.
        completed = real_run(command, **kwargs)
        assert completed.returncode == 0
        admitted.append(selected)
        assert (selected / "tasks.sqlite3").is_file()
        assert (selected / "tasks.sqlite3").stat().st_mode & 0o077 == 0
        if encrypted:
            assert (selected / "task-answers.key").stat().st_mode & 0o077 == 0
            assert (selected / ".answer-compatibility.json").stat().st_mode & 0o077 == 0
        else:
            assert not (selected / "task-answers.key").exists()
        with closing(sqlite3.connect(selected / "tasks.sqlite3")) as migrated:
            assert "revision" in {r[1] for r in migrated.execute("PRAGMA table_info(tasks)")}
            assert migrated.execute("SELECT count(*) FROM tasks").fetchone()[0] == (
                0 if mutation else 1)
        return completed

    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db) if encrypted else None
        before = authority(db)
        schema = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        history = db.execute("SELECT * FROM task_answer_events").fetchall() if encrypted else None
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        capsule = tmp_path / "capsule"
        receipt = stage_task_state_backup(root, capsule) if entry == "capsule" else None
        capsule_before = ({p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
                           for p in capsule.iterdir()} if receipt else None)
        with task_state_guard(root):
            journal_before = {name: (root / name).read_bytes()
                              for name in ["tasks.sqlite3", "tasks.sqlite3-wal"]}
            with monkeypatch.context() as patch:
                patch.setattr(state_compatibility.tempfile, "TemporaryDirectory",
                              aliased_owned_temporary)
                patch.setattr(state_compatibility.subprocess, "run", actual_candidate)
                if entry == "journal":
                    result = task_state_candidate_compatible(Path(sys.executable), release, root)
                else:
                    result = task_state_backup_candidate_compatible(
                        Path(sys.executable), release, capsule, receipt)
            assert result is (not bool(mutation))
            assert admitted == scratch and len(admitted) == 1
            assert all(not p.exists() for p in scratch)
            assert {name: (root / name).read_bytes() for name in journal_before} == journal_before
        assert authority(db) == before
        assert db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema
        assert "revision" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
        if encrypted:
            assert db.execute("SELECT * FROM task_answer_events").fetchall() == history
            assert (root / "task-answers.key").read_bytes() == key
        else:
            assert not (root / "task-answers.key").exists()
        assert not (root / "auth.token").exists()
        assert not (root / "service.json").exists()
        assert not (root / ".answer-compatibility.json").exists()
        if receipt:
            assert verify_task_state_backup(capsule, receipt)
            assert {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
                    for p in capsule.iterdir()} == capsule_before
    assert source_inventory() == source_before
    assert alias.is_symlink()
    assert (alias.lstat().st_dev, alias.lstat().st_ino) == (
        alias_identity.st_dev, alias_identity.st_ino)
    assert (outside.read_bytes(), outside.stat().st_mode,
            outside.stat().st_dev, outside.stat().st_ino) == outside_before
    assert sorted(p.name for p in parent.iterdir()) == ["unrelated"]


@pytest.mark.parametrize("shape", ["root", "ancestor"])
def test_owned_scratch_canonicalization_never_admits_a_caller_journal_alias(
        tmp_path, monkeypatch, shape):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        before = authority(db)
        history = db.execute("SELECT * FROM task_answer_events").fetchall()
        schema = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        alias = tmp_path / "caller-alias"
        alias.symlink_to(root if shape == "root" else tmp_path, target_is_directory=True)
        selected = alias if shape == "root" else alias / root.name
        alias_identity = alias.lstat()

        def forbidden(*args, **kwargs):
            pytest.fail("caller aliases must refuse before scratch creation or child migration")

        with monkeypatch.context() as patch:
            patch.setattr(state_compatibility.tempfile, "TemporaryDirectory", forbidden)
            patch.setattr(state_compatibility.subprocess, "run", forbidden)
            assert task_state_candidate_compatible(Path(sys.executable), tmp_path, selected) is False
        assert authority(db) == before
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == history
        assert db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == schema
        assert (root / "task-answers.key").read_bytes() == key
        assert not (root / "auth.token").exists()
        assert not (root / "service.json").exists()
        assert alias.is_symlink()
        assert (alias.lstat().st_dev, alias.lstat().st_ino) == (
            alias_identity.st_dev, alias_identity.st_ino)

@pytest.mark.parametrize("name", ["tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm"])
def test_journal_hardlink_cannot_enter_guard_or_candidate_snapshot(tmp_path, monkeypatch, name):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        authority_before = authority(db)
        source = root / name
        assert source.is_file()
        outside = tmp_path / ("external-" + name)
        os.link(source, outside)
        outside_identity = (outside.stat().st_dev, outside.stat().st_ino)
        outside_bytes = outside.read_bytes()

        with pytest.raises(ValueError, match="task_state_path_invalid"):
            with task_state_guard(root):
                pytest.fail("hardlinked journal cannot certify a unique authority")
        assert not any((root / lock).exists() for lock in (
            "native-window.lock", "worker.lock", "migration.lock"))

        def no_candidate(*_args, **_kwargs):
            pytest.fail("candidate cannot run after hardlinked journal refusal")
        monkeypatch.setattr(state_compatibility.subprocess, "run", no_candidate)
        assert task_state_candidate_compatible(
            sys.executable, candidate(tmp_path, "pass"), root) is False
        assert outside.read_bytes() == outside_bytes
        assert (outside.stat().st_dev, outside.stat().st_ino) == outside_identity
        assert authority(db) == authority_before


def test_hardlinked_answer_key_refuses_before_candidate_snapshot(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        authority_before = authority(db)
        outside = tmp_path / "external-answer-key"
        os.link(root / "task-answers.key", outside)
        identity = (outside.stat().st_dev, outside.stat().st_ino)

        def no_candidate(*_args, **_kwargs):
            pytest.fail("candidate cannot run after hardlinked answer-key refusal")
        monkeypatch.setattr(state_compatibility.subprocess, "run", no_candidate)
        assert task_state_candidate_compatible(
            sys.executable, candidate(tmp_path, "pass"), root) is False
        assert authority(db) == authority_before
        assert (root / "task-answers.key").read_bytes() == key
        assert outside.read_bytes() == key
        assert (outside.stat().st_dev, outside.stat().st_ino) == identity


def test_hardlinked_service_record_is_not_read_as_old_writer_proof(tmp_path, monkeypatch):
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    root.mkdir()
    outside = tmp_path / "external-service-record"
    payload = b"PRIVATE_SERVICE_RECORD_CANARY"
    outside.write_bytes(payload)
    os.link(outside, root / "service.json")
    identity = (outside.stat().st_dev, outside.stat().st_ino)

    def no_json(_raw):
        pytest.fail("hardlinked service record must not be parsed")
    monkeypatch.setattr(state_compatibility.json, "loads", no_json)
    with pytest.raises(ValueError, match="task_service_record_invalid"):
        with task_state_guard(root):
            pytest.fail("hardlinked service record cannot certify a stopped writer")
    assert outside.read_bytes() == payload
    assert (outside.stat().st_dev, outside.stat().st_ino) == identity

@pytest.mark.parametrize("mutation", [
    "unchanged", "wal_append", "event_changed", "schema_changed",
    "authority_deleted", "root_replaced", "database_replaced",
    "same_key_new_inode", "live_service_appeared",
])
def test_actual_candidate_cannot_certify_changed_original_journal_authority(
        tmp_path, monkeypatch, mutation):
    """A real migrated scratch result cannot retire a changed old authority."""
    import hashlib
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    release = answer_candidate(tmp_path)
    original_run = state_compatibility.subprocess.run
    calls = []
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        profile = json.dumps({"rows": [
            {"id": i, "body": "PRIVATE_COMPLETE_PROFILE_" + str(i)}
            for i in range(1000)]}, ensure_ascii=False).encode()
        preferences = json.dumps({"rows": [
            {"id": i, "body": "PRIVATE_COMPLETE_PREFERENCES_" + str(i)}
            for i in range(1000)]}, ensure_ascii=False).encode()
        for name, payload in [("profile.json", profile), ("preferences.json", preferences)]:
            (root / name).write_bytes(payload)
            (root / name).chmod(0o600)
        authority_before = authority(db)
        answers_before = db.execute("SELECT * FROM task_answer_events").fetchall()
        schema_before = db.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        source_before = {str(p.relative_to(release)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in release.rglob("*") if p.is_file()}
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        observed = {}
        retained = None

        def actual_migrate_then_change(command, **options):
            nonlocal retained
            assert command[1:4] == ["-I", "-B", "-c"]
            assert options["timeout"] == 10
            assert all(options[name] == subprocess.DEVNULL
                       for name in ["stdin", "stdout", "stderr"])
            completed = original_run(command, **options)
            assert completed.returncode == 0
            scratch = Path(command[-1])
            with closing(sqlite3.connect(scratch / "tasks.sqlite3")) as migrated:
                assert "revision" in {row[1] for row in migrated.execute("PRAGMA table_info(tasks)")}
                assert migrated.execute("SELECT count(*) FROM task_answer_events").fetchone() == (5,)
            calls.append(scratch)
            if mutation == "wal_append":
                db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(?,?,?,?)",
                           ("synthetic-task", 3, "PRIVATE_LATE_WAL_EVENT", "BLOCKED"))
                db.commit()
            elif mutation == "event_changed":
                db.execute("UPDATE events SET kind='PRIVATE_CHANGED_EVENT'")
                db.commit()
            elif mutation == "schema_changed":
                db.execute("CREATE INDEX private_changed_index ON events(task_id)")
                db.commit()
            elif mutation == "authority_deleted":
                db.execute("DELETE FROM tasks")
                db.commit()
            elif mutation == "root_replaced":
                retained = tmp_path / "retained-original"
                replacement_path = tmp_path / "replacement-root-journal.sqlite3"
                with closing(sqlite3.connect(replacement_path)) as replacement:
                    db.backup(replacement)
                root.rename(retained)
                root.mkdir(mode=0o700)
                replacement_path.rename(root / "tasks.sqlite3")
                (root / "tasks.sqlite3").chmod(0o600)
                (root / "task-answers.key").write_bytes(key)
                (root / "task-answers.key").chmod(0o600)
            elif mutation == "database_replaced":
                retained = root / "retained-original.sqlite3"
                replacement_path = tmp_path / "replacement-journal.sqlite3"
                with closing(sqlite3.connect(replacement_path)) as replacement:
                    db.backup(replacement)
                (root / "tasks.sqlite3").rename(retained)
                replacement_path.rename(root / "tasks.sqlite3")
                (root / "tasks.sqlite3").chmod(0o600)
            elif mutation == "same_key_new_inode":
                retained = root / "retained-original.key"
                (root / "task-answers.key").rename(retained)
                (root / "task-answers.key").write_bytes(key)
                (root / "task-answers.key").chmod(0o600)
            elif mutation == "live_service_appeared":
                (root / "service.json").write_text(json.dumps({"pid": os.getpid()}))
                (root / "service.json").chmod(0o600)
            observed["authority"] = authority(db)
            observed["answers"] = db.execute("SELECT * FROM task_answer_events").fetchall()
            observed["schema"] = db.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            actual_root = retained if mutation == "root_replaced" else root
            observed["private"] = {
                name: (actual_root / name).read_bytes()
                for name in ["profile.json", "preferences.json", "task-answers.key"]}
            observed["files"] = {
                str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mode,
                                              p.stat().st_dev, p.stat().st_ino)
                for p in tmp_path.rglob("*")
                if p.is_file() and scratch not in p.parents
                and p.name not in {"tasks.sqlite3-shm", "tasks.sqlite3-wal"}}
            return completed

        with task_state_guard(root):
            with monkeypatch.context() as patch:
                patch.setattr(state_compatibility.subprocess, "run", actual_migrate_then_change)
                assert task_state_candidate_compatible(
                    Path(sys.executable), release, root) is (mutation == "unchanged")
        assert len(calls) == 1 and not calls[0].exists()
        assert authority(db) == observed["authority"]
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == observed["answers"]
        assert db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall() == observed["schema"]
        assert observed["answers"] == answers_before
        if mutation not in {"wal_append", "event_changed", "authority_deleted"}:
            assert observed["authority"] == authority_before
        if mutation != "schema_changed":
            assert observed["schema"] == schema_before
        actual_root = retained if mutation == "root_replaced" else root
        assert {name: (actual_root / name).read_bytes() for name in observed["private"]} == observed["private"]
        assert observed["private"] == {"profile.json": profile, "preferences.json": preferences,
                                      "task-answers.key": key}
        assert {str(p.relative_to(release)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in release.rglob("*") if p.is_file()} == source_before
        assert {str(p.relative_to(tmp_path)): (p.read_bytes(), p.stat().st_mode,
                                             p.stat().st_dev, p.stat().st_ino)
                for p in tmp_path.rglob("*")
                if p.is_file() and p.name not in {"tasks.sqlite3-shm", "tasks.sqlite3-wal"}} == observed["files"]
        assert not (root / "auth.token").exists()
        assert not (actual_root / "tasks.sqlite3.pre-jcr01.sqlite3").exists()
        assert not list(tmp_path.glob("capsule*"))
        assert not list(tmp_path.glob("backup*"))
        if mutation != "live_service_appeared":
            assert not (root / "service.json").exists()

@pytest.mark.parametrize("mutation", [
    "wal_append", "event_changed", "schema_changed", "task_deleted",
])
def test_backup_refuses_committed_source_change_after_complete_wal_snapshot(
        tmp_path, monkeypatch, mutation):
    """A staged capsule cannot claim source continuity after an old writer commits."""
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    destination = tmp_path / "private-backup"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        private = {
            "profile.json": json.dumps({"rows": [
                {"id": index, "body": "PRIVATE_PROFILE_" + str(index)}
                for index in range(1000)]}, ensure_ascii=False).encode(),
            "preferences.json": json.dumps({"rows": [
                {"id": index, "body": "PRIVATE_PREFERENCES_" + str(index)}
                for index in range(1000)]}, ensure_ascii=False).encode(),
        }
        for name, payload in private.items():
            (root / name).write_bytes(payload)
            (root / name).chmod(0o600)
        source_events = db.execute("SELECT * FROM task_answer_events").fetchall()
        assert (root / "tasks.sqlite3-wal").stat().st_size > 0
        original_contract = state_compatibility._answer_contract
        calls = []

        def commit_after_backup_snapshot(backup, answer_key, secret):
            result = original_contract(backup, answer_key, secret)
            calls.append(True)
            assert len(calls) == 1
            assert answer_key == key
            if mutation == "wal_append":
                db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(?,?,?,?)",
                           ("synthetic-task", 3, "PRIVATE_LATE_BACKUP_EVENT", "BLOCKED"))
            elif mutation == "event_changed":
                db.execute("UPDATE events SET kind='PRIVATE_CHANGED_BACKUP_EVENT'")
            elif mutation == "schema_changed":
                db.execute("CREATE INDEX private_backup_index ON events(task_id)")
            else:
                db.execute("DELETE FROM tasks")
            db.commit()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(state_compatibility, "_answer_contract", commit_after_backup_snapshot)
            with pytest.raises(ValueError, match="task_backup_source_changed"):
                stage_task_state_backup(root, destination)
        assert calls == [True]
        assert not destination.exists()
        assert db.execute("SELECT * FROM task_answer_events").fetchall() == source_events
        assert (root / "task-answers.key").read_bytes() == key
        assert {name: (root / name).read_bytes() for name in private} == private
        assert not (root / "service.json").exists()
        assert not (root / "auth.token").exists()
        if mutation == "wal_append":
            assert db.execute("SELECT kind FROM events ORDER BY seq DESC LIMIT 1").fetchone() == (
                "PRIVATE_LATE_BACKUP_EVENT",)
        elif mutation == "event_changed":
            assert db.execute("SELECT DISTINCT kind FROM events").fetchall() == [
                ("PRIVATE_CHANGED_BACKUP_EVENT",)]
        elif mutation == "schema_changed":
            assert db.execute("SELECT name FROM sqlite_master WHERE name='private_backup_index'"
                              ).fetchone() == ("private_backup_index",)
        else:
            assert db.execute("SELECT count(*) FROM tasks").fetchone() == (0,)

def test_backup_receipt_publication_rechecks_late_wal_commit_and_cleans_only_stage(
        tmp_path, monkeypatch):
    """A complete manifest cannot survive a source commit during publication."""
    from executor.autonomy import state_compatibility

    root = tmp_path / "state"
    destination = tmp_path / "private-backup"
    with closing(legacy_state(root)) as db:
        key = encrypted_answers(root, db)
        profile = json.dumps({"rows": [
            {"id": index, "body": "PRIVATE_PROFILE_" + str(index)}
            for index in range(1000)]}, ensure_ascii=False).encode()
        (root / "profile.json").write_bytes(profile)
        (root / "profile.json").chmod(0o600)
        original_link = state_compatibility.os.link
        observed = []

        def publish_then_commit(source, target, *args, **kwargs):
            assert Path(source).name == ".backup-manifest.tmp"
            assert Path(target).name == "backup-manifest.json"
            original_link(source, target, *args, **kwargs)
            observed.append(True)
            db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(?,?,?,?)",
                       ("synthetic-task", 4, "PRIVATE_AFTER_MANIFEST", "BLOCKED"))
            db.commit()

        with monkeypatch.context() as patch:
            patch.setattr(state_compatibility.os, "link", publish_then_commit)
            with pytest.raises(ValueError, match="task_backup_source_changed"):
                stage_task_state_backup(root, destination)
        assert observed == [True]
        assert not destination.exists()
        assert db.execute("SELECT kind FROM events ORDER BY seq DESC LIMIT 1").fetchone() == (
            "PRIVATE_AFTER_MANIFEST",)
        assert db.execute("SELECT count(*) FROM task_answer_events").fetchone() == (5,)
        assert (root / "task-answers.key").read_bytes() == key
        assert (root / "profile.json").read_bytes() == profile
        assert not (root / "service.json").exists()
        assert not (root / "auth.token").exists()
