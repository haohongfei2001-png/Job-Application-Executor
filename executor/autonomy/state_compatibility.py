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



def _schema_contract(db, table):
    """Retain existing authority constraints while allowing additive columns.

    SQLite ALTER ADD preserves the existing CREATE declaration. Keeping that
    prefix also protects CHECK, collation, conflict and AUTOINCREMENT semantics
    that row digests and PRAGMA table_info alone cannot prove.
    """
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not isinstance(row[0], str):
        raise ValueError("task_state_schema")
    head, close, tail = row[0].rpartition(")")
    if not close:
        raise ValueError("task_state_schema")
    columns = {row[1]: tuple(row[2:]) for row in db.execute(
        "PRAGMA table_xinfo(" + _quote(table) + ")")}
    indexes = {}
    for row in db.execute("PRAGMA index_list(" + _quote(table) + ")"):
        name = row[1]
        declaration = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        indexes[name] = (
            tuple(row[2:]),
            tuple(tuple(item[2:]) for item in db.execute(
                "PRAGMA index_xinfo(" + _quote(name) + ")")),
            declaration[0] if declaration else None,
        )
    return {
        "head": head.rstrip(), "tail": tail.strip(), "columns": columns,
        "indexes": indexes,
        "foreign_keys": tuple(tuple(row) for row in db.execute(
            "PRAGMA foreign_key_list(" + _quote(table) + ")")),
        "triggers": dict(db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (table,))),
    }


def _schema_preserved(expected, actual):
    if actual["tail"] != expected["tail"] or not actual["head"].startswith(expected["head"]):
        return False
    addition = actual["head"][len(expected["head"]):].lstrip()
    if addition and not addition.startswith(","):
        return False
    return (
        all(actual["columns"].get(name) == contract
            for name, contract in expected["columns"].items())
        and all(actual["indexes"].get(name) == contract
                for name, contract in expected["indexes"].items())
        and actual["foreign_keys"] == expected["foreign_keys"]
        and all(actual["triggers"].get(name) == sql
                for name, sql in expected["triggers"].items())
    )


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
        result[table] = (columns, _digest(db, table, columns), _schema_contract(db, table))
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
                for table, (columns, expected, schema) in before.items():
                    if not set(columns).issubset(_columns(migrated, table)):
                        return False
                    if (not _schema_preserved(schema, _schema_contract(migrated, table))
                            or _digest(migrated, table, columns) != expected):
                        return False
            return True
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        return False
