"""Read-only release compatibility against an isolated SQLite backup.

No candidate service or worker is started with applicant task state. Only the
candidate queue's schema initializer runs against the disposable copy.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def _state_paths(root: Path):
    return [root / name for name in (
        "tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm")]


@contextmanager
def task_state_guard(root: str | Path):
    """Share the daemon's lock through the entire app install or rollback transaction."""
    root = Path(root).expanduser()
    if root.is_symlink():
        raise ValueError("task_state_path_invalid")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not root.is_dir() or any(p.is_symlink() for p in _state_paths(root)):
        raise ValueError("task_state_path_invalid")
    root.chmod(0o700)
    fd = os.open(root / "worker.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(db, table):
    return [row[1] for row in db.execute("PRAGMA table_info(" + _quote(table) + ")")]


def _digest(db, table, columns):
    # Stream rows in a deterministic SQL order; do not retain applicant bodies.
    fields = ",".join(_quote(c) for c in columns)
    digest = hashlib.sha256()
    count = 0
    for row in db.execute("SELECT " + fields + " FROM " + _quote(table) + " ORDER BY " + fields):
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return count, digest.digest()


def _snapshot(db):
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("task_state_integrity")
    tables = [row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if not row[0].startswith("sqlite_") or row[0] == "sqlite_sequence"]
    if "tasks" not in tables or "events" not in tables:
        raise ValueError("task_state_schema")
    result = {}
    for table in tables:
        columns = _columns(db, table)
        # run_state is derived from stage/blocker/lease by TaskQueue startup.
        # Every authority field, attempt, binding, event and receipt is exact.
        if table == "tasks":
            columns = [c for c in columns if c != "run_state"]
        if not columns:
            raise ValueError("task_state_schema")
        result[table] = (columns, _digest(db, table, columns))
    return result


def task_state_candidate_compatible(python: Path, release: Path, root: Path) -> bool:
    """Reject any candidate migration that loses or changes journal authority."""
    root = Path(root).expanduser()
    database = root / "tasks.sqlite3"
    if not root.exists() and not root.is_symlink():
        return True
    if root.is_symlink() or not root.is_dir() or any(p.is_symlink() for p in _state_paths(root)):
        return False
    if not database.exists():
        return not any(p.exists() for p in _state_paths(root)[1:])
    if not database.is_file():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="jae-state-compatibility-") as directory:
            copy = Path(directory) / "tasks.sqlite3"
            # SQLite backup includes committed WAL rows; file copying does not.
            with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(copy) as destination:
                    deadline = time.monotonic() + 10

                    def progress(_status, _remaining, _total):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("task_state_snapshot_timeout")

                    source.backup(destination, pages=256, progress=progress, sleep=0.01)
                    before = _snapshot(destination)
            copy.chmod(0o600)
            script = (
                "import pathlib,sys;sys.dont_write_bytecode=True;"
                "sys.path.insert(0,str(pathlib.Path(sys.argv[1]).resolve()));"
                "from executor.autonomy.queue import TaskQueue;"
                "TaskQueue(pathlib.Path(sys.argv[2]))"
            )
            completed = subprocess.run(
                [str(python), "-I", "-B", "-c", script, str(release), directory],
                cwd=release, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10,
                env={**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"},
            )
            if completed.returncode != 0 or copy.is_symlink() or not copy.is_file():
                return False
            with sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True) as migrated:
                if migrated.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    return False
                for table, (columns, expected) in before.items():
                    if not set(columns).issubset(_columns(migrated, table)):
                        return False
                    if _digest(migrated, table, columns) != expected:
                        return False
            return True
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        return False
