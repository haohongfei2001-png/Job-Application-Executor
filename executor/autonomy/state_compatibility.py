"""Read-only release compatibility against an isolated SQLite backup.

No candidate service or worker is started with applicant task state. Only the
candidate queue's schema initializer and typed answer decoder run against the disposable copy.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import stat
import os
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path


def _state_paths(root: Path):
    return [root / name for name in (
        "tasks.sqlite3", "tasks.sqlite3-wal", "tasks.sqlite3-shm")]


def _refuse_live_service(root: Path):
    """A legacy daemon may not honor worker.lock; probe only, never signal it."""
    registry = root / "service.json"
    if not registry.exists() and not registry.is_symlink():
        return
    # No symlink, device, FIFO or unbounded registry read. An ambiguous record
    # cannot certify that the old writer is stopped, even with the new lock.
    with os.fdopen(os.open(registry, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK),
                   encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("task_service_record_invalid")
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("task_service_record_invalid")
    try:
        record = json.loads(raw)
    except (UnicodeError, ValueError):
        raise ValueError("task_service_record_invalid") from None
    pid = record.get("pid") if isinstance(record, dict) else None
    if type(pid) is not int or not 0 < pid <= 2147483647:
        raise ValueError("task_service_record_invalid")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        # A process that cannot be inspected is not evidence of a stopped one.
        pass
    raise BlockingIOError("task_state_in_use")



def _private_lock_fd(path: Path, *, blocking: bool = False) -> int:
    """Acquire an owned ordinary single-link lock without reading its payload."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()):
            raise ValueError("transaction_lock_invalid")
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("transaction_lock_replaced")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


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
    # A queue constructor writes schema/derived state under migration.lock even
    # without starting a worker. Fence both writers through compatibility,
    # activation and recovery; contention refuses promptly rather than waits.
    fd = _private_lock_fd(root / "worker.lock")
    migration_fd = None
    try:
        migration_fd = _private_lock_fd(root / "migration.lock")
        _refuse_live_service(root)
        yield
    finally:
        if migration_fd is not None:
            os.close(migration_fd)
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


def _answer_key(root: Path):
    """Read only the owned, bounded task-answer key; never create or repair it."""
    path = root / "task-answers.key"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size != 44):
            raise ValueError("task_answer_key_invalid")
        key = handle.read(45)
    if len(key) != 44:
        raise ValueError("task_answer_key_invalid")
    from cryptography.fernet import Fernet
    Fernet(key)
    return key


def _answer_contract(db, key, secret):
    """Expected latest typed answers, reduced to ephemeral keyed digests.

    The journal's complete encrypted history remains covered by row/schema
    snapshots. This also proves the candidate's actual consumer decoder can
    recover the same latest values, rather than merely retaining ciphertext.
    """
    import hmac
    from cryptography.fernet import Fernet, InvalidToken

    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_answer_events'"
    ).fetchone()
    if not exists:
        return []
    latest = {}
    for task_id, field_key, ciphertext in db.execute(
            "SELECT task_id,field_key,ciphertext FROM task_answer_events ORDER BY sequence"):
        if key is None:
            raise ValueError("task_answer_key_missing")
        try:
            value = json.loads(Fernet(key).decrypt(ciphertext))
        except (InvalidToken, ValueError, TypeError):
            raise ValueError("task_answer_unreadable") from None
        latest.setdefault(task_id, {})[field_key] = value
    return [{"task_id": task_id,
             "digest": hmac.new(secret, json.dumps(values, ensure_ascii=False,
                 sort_keys=True, separators=(",", ":")).encode("utf-8"),
                 hashlib.sha256).hexdigest()}
            for task_id, values in sorted(latest.items())]


def task_state_candidate_compatible(python: Path, release: Path, root: Path) -> bool:
    """Reject any candidate migration that loses or changes journal authority."""
    root = Path(root).expanduser()
    database = root / "tasks.sqlite3"
    if not root.exists() and not root.is_symlink():
        return True
    if root.is_symlink() or not root.is_dir() or any(p.is_symlink() for p in _state_paths(root)):
        return False
    try:
        key = _answer_key(root)
        if not database.exists():
            return key is None and not any(p.exists() for p in _state_paths(root)[1:])
        if not database.is_file():
            return False
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
                    secret = os.urandom(32)
                    answers = _answer_contract(destination, key, secret)
            copy.chmod(0o600)
            if key is not None:
                copied_key = Path(directory) / "task-answers.key"
                with os.fdopen(os.open(copied_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                       0o600), "wb") as handle:
                    handle.write(key)
                    handle.flush()
                    os.fsync(handle.fileno())
                check = Path(directory) / ".answer-compatibility.json"
                check.write_text(json.dumps({"secret": secret.hex(), "tasks": answers}),
                                 encoding="utf-8")
                check.chmod(0o600)
            script = (
                "import pathlib,sys;sys.dont_write_bytecode=True;"
                "sys.path.insert(0,str(pathlib.Path(sys.argv[1]).resolve()));"
                "from executor.autonomy.queue import TaskQueue;"
                "root=pathlib.Path(sys.argv[2]);queue=TaskQueue(root);"
                "\nif (root/'task-answers.key').exists():"
                "\n import hashlib,hmac,json"
                "\n from executor.facts.answers import TaskAnswerStore"
                "\n check=json.loads((root/'.answer-compatibility.json').read_text())"
                "\n answers=TaskAnswerStore(queue)"
                "\n for item in check['tasks']:"
                "\n  payload=json.dumps(answers.load(item['task_id']),ensure_ascii=False,"
                "sort_keys=True,separators=(',',':')).encode('utf-8')"
                "\n  actual=hmac.new(bytes.fromhex(check['secret']),payload,hashlib.sha256).hexdigest()"
                "\n  assert hmac.compare_digest(actual,item['digest'])"

            )
            completed = subprocess.run(
                [str(python), "-I", "-B", "-c", script, str(release), directory],
                cwd=release, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10,
                env={**os.environ, "APPLICATION_EXECUTOR_BROWSER_MODE": "isolated"},
            )
            if completed.returncode != 0 or copy.is_symlink() or not copy.is_file():
                return False
            if _answer_key(root) != key or _answer_key(Path(directory)) != key:
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
    except (ImportError, OSError, ValueError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        return False


def _backup_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _task_backup_paths(root: str | Path, destination: str | Path):
    root = Path(root).expanduser().absolute()
    destination = Path(destination).expanduser().absolute()
    for path in (root, destination):
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("task_backup_path_invalid")
    if (not root.is_dir() or not destination.parent.is_dir()
            or destination.exists() or destination.is_relative_to(root)
            or root.is_relative_to(destination)):
        raise ValueError("task_backup_destination_unavailable")
    database = root / "tasks.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise ValueError("task_backup_database_unavailable")

    return root, destination, database


def stage_task_state_backup(root: str | Path, destination: str | Path) -> dict:
    """Publish a private, WAL-consistent journal/key backup, never activate it.

    The source remains the sole task authority. Current worker/migration locks
    and live-service refusal are required, but cannot certify retirement of
    pre-lock legacy writers. No service token, log, profile or other private
    file is copied. This receipt is scoped backup evidence, not migration or
    permission to install/restore a candidate.
    """
    root, destination, _ = _task_backup_paths(root, destination)
    with task_state_guard(root):
        return _stage_task_state_backup_locked(root, destination)


def _stage_task_state_backup_locked(root: Path, destination: Path) -> dict:
    """Internal backup primitive; the caller holds worker and schema fences."""
    root, destination, database = _task_backup_paths(root, destination)
    created = {}
    def owned_file(path):
        metadata = path.stat(follow_symlinks=False)
        created[path] = (metadata.st_dev, metadata.st_ino)

    # Hold an ordinary source descriptor and compare its identity again
    # before publication. Never follow a replacement/alias as authority.
    fd = os.open(database, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()):
            raise ValueError("task_backup_database_invalid")
        key_path = root / "task-answers.key"
        if key_path.exists() or key_path.is_symlink():
            key_info = key_path.stat(follow_symlinks=False)
            if not stat.S_ISREG(key_info.st_mode) or key_info.st_nlink != 1:
                raise ValueError("task_backup_key_invalid")
        key = _answer_key(root)
        destination.mkdir(mode=0o700, exist_ok=False)
        try:
            copy = destination / "tasks.sqlite3"
            copy_fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                              os.O_NOFOLLOW, 0o600)
            os.close(copy_fd)
            owned_file(copy)
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(copy)) as backup:
                    deadline = time.monotonic() + 10
                    def progress(_status, _remaining, _total):
                        if time.monotonic() >= deadline:
                            raise TimeoutError("task_backup_snapshot_timeout")
                    source.backup(backup, pages=256, progress=progress, sleep=0.01)
                    # A transportable capsule must contain the whole
                    # committed journal in one database, with no WAL/SHM
                    # sidecars needed when reopened or compatibility-checked.
                    if backup.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                        raise ValueError("task_backup_journal_mode_invalid")
                    before = _snapshot(backup)
                    # Prove encrypted values match the copied key without
                    # ever writing their plaintext or ephemeral HMAC secret.
                    answers = _answer_contract(backup, key, os.urandom(32))
            with copy.open("rb") as file:
                os.fsync(file.fileno())
            if key is not None:
                target_key = destination / "task-answers.key"
                with os.fdopen(os.open(target_key, os.O_WRONLY | os.O_CREAT |
                                       os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as file:
                    owned_file(target_key)
                    file.write(key)
                    file.flush()
                    os.fsync(file.fileno())
            current = database.stat(follow_symlinks=False)
            if ((current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
                    or _answer_key(root) != key):
                raise ValueError("task_backup_source_changed")
            _refuse_live_service(root)
            # Receipt appears last, atomically, after the complete payload
            # is flushed. Its absence means an incomplete backup.
            receipt = {
                "format": "jae-task-state-backup-v1",
                "scope": "task_journal_and_encrypted_task_answers",
                "database_sha256": _backup_digest(copy),
                "answer_key_sha256": hashlib.sha256(key).hexdigest() if key is not None else None,
                "table_count": len(before), "answer_task_count": len(answers),
                "activation": "NOT_AUTHORIZED",
                "legacy_writer_retirement": "NOT_CERTIFIED",
                "source_authority": "unchanged",
                "other_private_files": "excluded",
            }
            temporary = destination / ".backup-manifest.tmp"
            with temporary.open("x", encoding="utf-8") as file:
                os.chmod(temporary, 0o600)
                owned_file(temporary)
                json.dump(receipt, file, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            manifest = destination / "backup-manifest.json"
            os.link(temporary, manifest)
            owned_file(manifest)
            temporary.unlink()
            created.pop(temporary)
            directory_fd = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return receipt
        except BaseException:
            # Remove only our own known artifacts. Preserve any unexpected
            # concurrent file or replacement for explicit diagnosis.
            for path, identity in reversed(list(created.items())):
                try:
                    actual = path.stat(follow_symlinks=False)
                    if (actual.st_dev, actual.st_ino) == identity:
                        path.unlink()
                except FileNotFoundError:
                    pass
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
    finally:
        os.close(fd)

def _private_backup_digest(path: Path) -> tuple[str, tuple[int, int]]:
    """Read an owned capsule payload without alias/device/FIFO traversal."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        metadata = os.fstat(file.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077
                or (path.name == "backup-manifest.json" and metadata.st_size > 65536)
                or (path.name == "task-answers.key" and metadata.st_size != 44)):
            raise ValueError("task_backup_payload_invalid")
        if path.name == "tasks.sqlite3":
            header = file.read(20)
            if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
                raise ValueError("task_backup_not_standalone")
            file.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
        identity = (metadata.st_dev, metadata.st_ino)
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise ValueError("task_backup_payload_replaced")
        return digest.hexdigest(), identity


def verify_task_state_backup(destination: str | Path, receipt: dict) -> bool:
    """Read-only capsule proof bound to the caller's original external receipt.

    A self-consistent edited manifest cannot establish authority. The trusted
    receipt must come from staging, not be reread from the capsule. Verification
    never restores state, creates/repairs a key or certifies an old writer stop.
    """
    try:
        if type(receipt) is not dict:
            return False
        expected = dict(receipt)
        keys = {"format", "scope", "database_sha256", "answer_key_sha256",
                "table_count", "answer_task_count", "activation",
                "legacy_writer_retirement", "source_authority", "other_private_files"}
        sha256 = lambda value: (isinstance(value, str) and len(value) == 64
                                and all(char in "0123456789abcdef" for char in value))
        if (set(expected) != keys
                or expected["format"] != "jae-task-state-backup-v1"
                or expected["scope"] != "task_journal_and_encrypted_task_answers"
                or expected["activation"] != "NOT_AUTHORIZED"
                or expected["legacy_writer_retirement"] != "NOT_CERTIFIED"
                or expected["source_authority"] != "unchanged"
                or expected["other_private_files"] != "excluded"
                or not sha256(expected["database_sha256"])
                or (expected["answer_key_sha256"] is not None
                    and not sha256(expected["answer_key_sha256"]))
                or type(expected["table_count"]) is not int or expected["table_count"] < 2
                or type(expected["answer_task_count"]) is not int
                or expected["answer_task_count"] < 0):
            return False
        root = Path(destination).expanduser().absolute()
        if any(part.is_symlink() for part in (root, *root.parents)):
            return False
        metadata = root.stat(follow_symlinks=False)
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o077):
            return False
        root_identity = (metadata.st_dev, metadata.st_ino)
        names = {"backup-manifest.json", "tasks.sqlite3"}
        if expected["answer_key_sha256"] is not None:
            names.add("task-answers.key")
        if {path.name for path in root.iterdir()} != names:
            return False
        hashes = {name: _private_backup_digest(root / name) for name in names}
        manifest = root / "backup-manifest.json"
        fd = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if ((info.st_dev, info.st_ino) != hashes[manifest.name][1]
                    or not stat.S_ISREG(info.st_mode) or info.st_size > 65536):
                return False
            observed = json.loads(file.read(65537))
        if (type(observed) is not dict or observed != expected
                or type(observed.get("table_count")) is not int
                or type(observed.get("answer_task_count")) is not int
                or hashes["tasks.sqlite3"][0] != expected["database_sha256"]
                or ("task-answers.key" in names
                    and hashes["task-answers.key"][0] != expected["answer_key_sha256"])):
            return False
        key = _answer_key(root)
        if (key is None) != (expected["answer_key_sha256"] is None):
            return False
        # immutable+read-only prevents SQLite from creating auxiliary files or
        # opening the original source WAL. A valid capsule is one DELETE DB.
        with closing(sqlite3.connect((root / "tasks.sqlite3").as_uri()
                                     + "?mode=ro&immutable=1", uri=True)) as db:
            if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                return False
            tables = _snapshot(db)
            answers = _answer_contract(db, key, os.urandom(32))
        if (len(tables) != expected["table_count"]
                or len(answers) != expected["answer_task_count"]):
            return False
        # File identity and bytes must remain the independently bound payload
        # throughout the read, including manifest/key and unexpected inventory.
        current = root.stat(follow_symlinks=False)
        return ((current.st_dev, current.st_ino) == root_identity
                and {path.name for path in root.iterdir()} == names
                and all(_private_backup_digest(root / name) == hashes[name]
                        for name in names))
    except (ImportError, OSError, ValueError, TypeError, sqlite3.Error):
        return False


def task_state_backup_candidate_compatible(
    python: Path, release: Path, destination: str | Path, receipt: dict,
) -> bool:
    """Prove candidate migration on a disposable copy of a bound durable capsule."""
    if type(receipt) is not dict:
        return False
    expected = dict(receipt)
    if not verify_task_state_backup(destination, expected):
        return False
    # The candidate never opens/migrates the durable capsule or source journal.
    # Its real queue schema and typed answer decoder run in the existing private
    # disposable compatibility copy, preserving every authority/history field.
    if not task_state_candidate_compatible(python, release, Path(destination)):
        return False
    return verify_task_state_backup(destination, expected)
