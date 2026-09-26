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
