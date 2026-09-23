from __future__ import annotations

import json
import os
import hashlib
import re
import sqlite3
import tempfile
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..autonomy.queue import IDENTIFIER, private_dir


_AUTH_FRAGMENTS = ("password", "cookie", "token", "otp", "secret",
                   "authorization", "verification code", "security code",
                   "验证码", "短信码")


def _validate_applicant_answer(key: str, value, *, label: str = "") -> None:
    context = re.sub(r"[_-]+", " ", f"{key} {label}".casefold())
    if any(fragment in context for fragment in _AUTH_FRAGMENTS):
        raise ValueError("authentication values are not applicant facts")
    if isinstance(value, str) and any(fragment in value.casefold() for fragment in
                                      ("otp", "verification code", "security code", "验证码", "短信码")):
        raise ValueError("OTP-like values require the dedicated in-memory channel")
    # Numeric applicant facts include years, salaries and postal codes. Reject
    # impossible all-digit names/roles by their field semantics, not by an OTP
    # length heuristic that cannot distinguish a valid numeric fact from a code.
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value) and re.search(
        r"(?:^|\.)(?:role|name|relationship|school|major)$", key, re.I
    ):
        raise ValueError("numeric value is invalid for this applicant field")


def _private_cipher(root: Path, key_name: str) -> Fernet:
    root = private_dir(root)
    key_path = root / key_name
    if not key_path.exists():
        with tempfile.NamedTemporaryFile(dir=root, prefix=".answer-key-", delete=False) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            file.write(Fernet.generate_key())
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, key_path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    if key_path.stat().st_mode & 0o077:
        raise PermissionError("private answer key must be mode 0600")
    return Fernet(key_path.read_bytes())


class TaskAnswerStore:
    """Versioned task-only answers; raw values never enter operational rows."""

    def __init__(self, queue):
        self.queue = queue
        self.cipher = _private_cipher(queue.root, "task-answers.key")
        with queue.tx() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS task_answer_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                field_key TEXT NOT NULL,
                ciphertext BLOB NOT NULL,
                answer_version INTEGER NOT NULL,
                source TEXT NOT NULL,
                task_revision INTEGER NOT NULL,
                created REAL NOT NULL,
                reuse_requested INTEGER NOT NULL DEFAULT 0,
                reuse_applied INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(task_answer_events)")}
            if "reuse_requested" not in columns:
                db.execute("ALTER TABLE task_answer_events ADD COLUMN reuse_requested INTEGER NOT NULL DEFAULT 0")
            if "reuse_applied" not in columns:
                db.execute("ALTER TABLE task_answer_events ADD COLUMN reuse_applied INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE INDEX IF NOT EXISTS task_answer_latest
                ON task_answer_events(task_id,field_key,sequence)""")

    @staticmethod
    def _validate(answers: dict) -> None:
        if not isinstance(answers, dict) or not answers or len(answers) > 100:
            raise ValueError("answers must be a canonical-key mapping")
        if any(not isinstance(key, str) or not IDENTIFIER.fullmatch(key)
               for key in answers):
            raise ValueError("invalid answer key")
        if any(not isinstance(value, (str, int, bool, float)) or
               len(str(value)) > 10000 for value in answers.values()):
            raise ValueError("invalid answer value")
        for key, value in answers.items():
            _validate_applicant_answer(key, value)

    def _save_in_tx(self, db, task_id: str, answers: dict,
                    expected_revision: int, *, remember: bool = False) -> None:
        task = db.execute("SELECT revision,stage,owner,details FROM tasks WHERE task_id=?",
                          (task_id,)).fetchone()
        if task is None:
            raise KeyError("task not found")
        if task["revision"] != expected_revision:
            raise RuntimeError("stale task revision")
        if task["owner"] or task["stage"] != "NEEDS_USER_INPUT":
            raise ValueError("task is not waiting for facts")
        pending = set(json.loads(task["details"]).get("unresolved_keys", []))
        if not set(answers).issubset(pending):
            raise ValueError("answer keys must match pending facts")
        for key, value in answers.items():
            previous = db.execute("""SELECT answer_version FROM task_answer_events
                WHERE task_id=? AND field_key=? ORDER BY sequence DESC LIMIT 1""",
                (task_id, key)).fetchone()
            version = previous["answer_version"] + 1 if previous else 1
            ciphertext = self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())
            db.execute("""INSERT INTO task_answer_events
                (task_id,field_key,ciphertext,answer_version,source,task_revision,created,
                 reuse_requested)
                VALUES(?,?,?,?,?,?,?,?)""",
                (task_id, key, ciphertext, version, "user_explicit_task", expected_revision,
                 self.queue.clock(), int(remember)))

    def save(self, task_id: str, answers: dict, *, expected_revision: int) -> None:
        self._validate(answers)
        with self.queue.tx() as db:
            self._save_in_tx(db, task_id, answers, expected_revision)

    def save_and_resume(self, task_id: str, answers: dict,
                        *, expected_revision: int, remember: bool = False) -> dict:
        """Rejected/stale input leaves neither an answer nor a transition."""
        self._validate(answers)
        with self.queue.tx() as db:
            self._save_in_tx(db, task_id, answers, expected_revision, remember=remember)
            if remember:
                # Keep the task unclaimable until the canonical write finishes.
                # A process crash leaves the encrypted answer available for retry.
                db.execute("""UPDATE tasks SET stage='BLOCKED',blocker='profile_promotion_pending',
                    updated=? WHERE task_id=?""", (self.queue.clock(), task_id))
                self.queue._event(db, task_id, "profile_promotion_pending", "BLOCKED")
            else:
                self.queue._resume_in_tx(db, task_id, expected_revision=expected_revision)
        return self.queue.get(task_id)

    def load(self, task_id: str) -> dict:
        with self.queue.tx() as db:
            rows = db.execute("""SELECT field_key,ciphertext FROM task_answer_events
                WHERE task_id=? ORDER BY sequence""", (task_id,)).fetchall()
        answers = {}
        for row in rows:
            try:
                answers[row["field_key"]] = json.loads(self.cipher.decrypt(row["ciphertext"]))
            except (InvalidToken, ValueError) as exc:
                raise RuntimeError("private task answer unreadable; do not guess") from exc
        return answers

    def metadata(self, task_id: str) -> list[dict]:
        with self.queue.tx() as db:
            return [dict(row) for row in db.execute("""SELECT field_key,answer_version,
                source,task_revision,created,reuse_requested,reuse_applied FROM task_answer_events
                WHERE task_id=? ORDER BY sequence""", (task_id,))]

    def pending_reuse(self, task_id: str) -> list[dict]:
        with self.queue.tx() as db:
            rows = db.execute("""SELECT sequence,field_key,ciphertext,answer_version
                FROM task_answer_events WHERE task_id=? AND reuse_requested=1
                AND reuse_applied=0 ORDER BY sequence""", (task_id,)).fetchall()
        pending = []
        for row in rows:
            try:
                value = json.loads(self.cipher.decrypt(row["ciphertext"]))
            except (InvalidToken, ValueError) as exc:
                raise RuntimeError("private reusable fact unreadable; do not guess") from exc
            pending.append({"sequence": row["sequence"], "key": row["field_key"],
                            "value": value, "version": row["answer_version"]})
        return pending

    def pending_tasks(self) -> list[str]:
        with self.queue.tx() as db:
            return [row[0] for row in db.execute("""SELECT task_id FROM task_answer_events
                WHERE reuse_requested=1 AND reuse_applied=0
                UNION SELECT task_id FROM tasks
                WHERE stage='BLOCKED' AND blocker='profile_promotion_pending'""")]

    def mark_reuse_applied(self, sequence: int, *, task_id: str, profile_ref: str) -> None:
        """The final applied marker, task resume and barrier release commit together."""
        with self.queue.tx() as db:
            updated = db.execute("""UPDATE task_answer_events SET reuse_applied=1
                WHERE sequence=? AND task_id=? AND reuse_requested=1""",
                (sequence, task_id)).rowcount
            if updated != 1:
                raise RuntimeError("reusable answer event missing")
            remaining = db.execute("""SELECT 1 FROM task_answer_events
                WHERE task_id=? AND reuse_requested=1 AND reuse_applied=0 LIMIT 1""",
                (task_id,)).fetchone()
            if not remaining:
                task = db.execute("SELECT stage,blocker FROM tasks WHERE task_id=?",
                                  (task_id,)).fetchone()
                if task and task["stage"] == "BLOCKED" and task["blocker"] in {
                    "profile_promotion_pending", "profile_changed",
                }:
                    self.queue._resume_in_tx(db, task_id)
                elif not task or not (task["stage"] == "CANCELLED" or
                                     str(task["blocker"] or "").startswith("user_paused")):
                    raise RuntimeError("promotion task state changed unexpectedly")
                self.queue._finish_profile_write_in_tx(db, profile_ref, task_id)

    def finish_pending_task(self, task_id: str, profile_ref: str) -> None:
        """Recover a legacy post-marker crash with no pending answer event."""
        with self.queue.tx() as db:
            pending = db.execute("""SELECT 1 FROM task_answer_events
                WHERE task_id=? AND reuse_requested=1 AND reuse_applied=0 LIMIT 1""",
                (task_id,)).fetchone()
            if pending:
                return
            task = db.execute("SELECT stage,blocker FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task and task["stage"] == "BLOCKED" and task["blocker"] == "profile_promotion_pending":
                self.queue._resume_in_tx(db, task_id)
                self.queue._finish_profile_write_in_tx(db, profile_ref, task_id)


class ExecutionAnswerStore:
    """The legacy CLI uses the same private versioned answer contract."""

    def __init__(self, root: Path):
        self.root = private_dir(root)
        self.cipher = _private_cipher(self.root, "execution-answers.key")
        self.path = self.root / "execution-answers.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS answer_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                match_digest TEXT NOT NULL,
                ciphertext BLOB NOT NULL,
                answer_version INTEGER NOT NULL,
                created REAL NOT NULL
            )""")
        self.path.chmod(0o600)

    @staticmethod
    def _match_key(answer: dict) -> str:
        selector = answer.get("selector")
        field_id = answer.get("field_id")
        canonical = answer.get("canonical_key")
        key = selector or field_id or canonical
        if not isinstance(key, str) or not key or len(key) > 1000:
            raise ValueError("answer requires a bounded field identity")
        for candidate in (selector, field_id, canonical):
            if isinstance(candidate, str):
                _validate_applicant_answer(candidate, answer.get("value"),
                                           label=str(answer.get("label") or ""))
        return hashlib.sha256(key.encode()).hexdigest()

    def save(self, answer: dict) -> Path:
        if not isinstance(answer, dict) or "value" not in answer:
            raise ValueError("invalid execution answer")
        digest = self._match_key(answer)
        payload = json.dumps(answer, ensure_ascii=False).encode()
        if len(payload) > 20000:
            raise ValueError("execution answer too large")
        with sqlite3.connect(self.path, timeout=15) as db:
            previous = db.execute("""SELECT answer_version FROM answer_events
                WHERE match_digest=? ORDER BY sequence DESC LIMIT 1""", (digest,)).fetchone()
            version = previous[0] + 1 if previous else 1
            db.execute("""INSERT INTO answer_events(match_digest,ciphertext,answer_version,created)
                VALUES(?,?,?,?)""", (digest, self.cipher.encrypt(payload), version, time.time()))
        return self.path

    def load(self) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT match_digest,ciphertext FROM answer_events ORDER BY sequence").fetchall()
        latest = {}
        for digest, ciphertext in rows:
            try:
                latest[digest] = json.loads(self.cipher.decrypt(ciphertext))
            except (InvalidToken, ValueError) as exc:
                raise RuntimeError("private execution answer unreadable; do not guess") from exc
        return list(latest.values())
