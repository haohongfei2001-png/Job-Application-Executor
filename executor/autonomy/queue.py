from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import ApplicationStage
from ..protected_targets import assert_target_not_protected

RUNTIME = Path(__file__).resolve().parents[2] / "runtime" / "autonomy"
SAFE_STAGES = {"DISCOVERED", "PROFILE_RESOLVED", "FORM_FILLED", "VALIDATED"}
STOPPED = {"READY_TO_SUBMIT", "SUBMITTED", "VERIFIED", "CANCELLED"}
STAGES = {str(x) for x in ApplicationStage}
IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.:-]{0,150}$")


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    company: str = Field(min_length=1, max_length=150)
    role: str = Field(min_length=1, max_length=200)
    target_url: str = Field(max_length=2000)
    job_id: str = Field(default="", max_length=150)
    campaign: str = Field(default="", max_length=150)
    profile_ref: str = Field(min_length=1, max_length=1000)
    attachment_refs: dict[str, str] = Field(default_factory=dict)
    live_authorized: bool = False
    max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def consistent_job(self):
        ids = {v for k, v in parse_qsl(urlsplit(self.target_url).query) if k.lower() in {"postid", "jobid", "positionid"}}
        if len(ids) > 1 or (ids and self.job_id and self.job_id not in ids):
            raise ValueError("conflicting target identifiers")
        return self

    @field_validator("target_url")
    @classmethod
    def safe_url(cls, value):
        u = urlsplit(value)
        if u.scheme not in {"https", "http", "file"} or u.username or u.password or u.fragment:
            raise ValueError("invalid operational target URL")
        if u.scheme != "file" and not u.hostname:
            raise ValueError("target host required")
        # Only job identifiers/routing parameters belong in durable operational state.
        allowed = {"postid", "posttype", "jobid", "positionid", "id", "companyid", "recruittype", "type", "lang", "locale", "brandcode", "position", "siteid", "channel", "recruitpage", "resumeid", "tid", "step"}
        if any(k.lower() not in allowed for k, _ in parse_qsl(u.query)):
            raise ValueError("URL contains non-operational query parameters")
        return value

    @field_validator("attachment_refs")
    @classmethod
    def assets(cls, value):
        if set(value) - {"resume", "photo"} or any(len(v) > 1000 for v in value.values()):
            raise ValueError("invalid attachment references")
        return value


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


class TaskQueue:
    """A transaction per operation; leases use fencing tokens, not process IDs."""
    def __init__(self, root=RUNTIME, *, clock=time.time):
        self.root = private_dir(root)
        self.path = self.root / "tasks.sqlite3"
        self.clock = clock
        self.lock = threading.RLock()
        with self.tx() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    spec TEXT NOT NULL, stage TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL,
                    next_run REAL NOT NULL DEFAULT 0, blocker TEXT, details TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, at REAL,
                    kind TEXT NOT NULL, stage TEXT NOT NULL);
            ''')
            columns = {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
            if "checkpoint_url" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN checkpoint_url TEXT")
        self.path.chmod(0o600)

    @contextmanager
    def tx(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def _event(self, db, task_id, kind, stage):
        db.execute("INSERT INTO events(task_id,at,kind,stage) VALUES(?,?,?,?)", (task_id, self.clock(), kind, stage))

    @staticmethod
    def _view(row):
        if row is None:
            raise KeyError("task not found")
        d = dict(row)
        d["spec"] = json.loads(d["spec"])
        d["details"] = json.loads(d["details"])
        return d

    def enqueue(self, spec: TaskSpec):
        assert_target_not_protected(spec.target_url)
        # Profile, campaign and display text cannot defeat exact-target duplicate suppression.
        u = urlsplit(spec.target_url)
        ids = [v for k, v in parse_qsl(u.query) if k.lower() in {"postid", "jobid", "positionid"}]
        job_id = ids[0] if ids else spec.job_id
        identity = f"{u.hostname}:{job_id}" if job_id else spec.target_url
        key = hashlib.sha256(identity.encode()).hexdigest()
        now = self.clock()
        with self.tx() as db:
            row = db.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                return self._view(row)
            tid = uuid.uuid4().hex
            db.execute("INSERT INTO tasks(task_id,idempotency_key,spec,stage,checkpoint,created,updated) VALUES(?,?,?,?,?,?,?)", (tid, key, spec.model_dump_json(), "DISCOVERED", "DISCOVERED", now, now))
            self._event(db, tid, "enqueued", "DISCOVERED")
            return self._view(db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())

    def get(self, tid):
        with self.tx() as db:
            return self._view(db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone())

    def tasks(self):
        with self.tx() as db:
            return [self._view(x) for x in db.execute("SELECT * FROM tasks ORDER BY created")]

    def events(self, after=0):
        with self.tx() as db:
            return [dict(x) for x in db.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 200", (after,))]

    def claim(self, worker, lease_seconds=60):
        now = self.clock()
        with self.tx() as db:
            # A crash after persisting a pause must not leave an unresumable owner.
            db.execute("UPDATE tasks SET owner=NULL,lease_until=NULL WHERE owner IS NOT NULL AND lease_until<=? AND stage IN ('NEEDS_USER_INPUT','NEEDS_USER_ACTION','BLOCKED','READY_TO_SUBMIT','CANCELLED')", (now,))
            # One active task, including across independent processes/queue instances.
            if db.execute("SELECT 1 FROM tasks WHERE owner IS NOT NULL AND lease_until>?", (now,)).fetchone():
                return None
            rows = db.execute("SELECT * FROM tasks WHERE (owner IS NULL OR lease_until<=?) AND next_run<=? ORDER BY created", (now, now)).fetchall()
            for row in rows:
                spec = json.loads(row["spec"])
                runnable = row["stage"] in SAFE_STAGES or (row["stage"] == "ERROR" and row["blocker"] == "retry_pending")
                if not runnable:
                    continue
                if row["attempts"] >= spec["max_attempts"]:
                    db.execute("UPDATE tasks SET stage='ERROR',blocker='retry_exhausted',owner=NULL,lease_until=NULL,updated=? WHERE task_id=?", (now, row["task_id"]))
                    self._event(db, row["task_id"], "retry_exhausted", "ERROR")
                    continue
                owner = worker + ":" + uuid.uuid4().hex
                db.execute("UPDATE tasks SET owner=?,lease_until=?,attempts=attempts+1,stage=checkpoint,updated=? WHERE task_id=?", (owner, now+lease_seconds, now, row["task_id"]))
                self._event(db, row["task_id"], "recovered" if row["owner"] else "claimed", row["checkpoint"])
                return self._view(db.execute("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone())
        return None

    def renew(self, tid, owner, seconds=60):
        with self.tx() as db:
            return bool(db.execute("UPDATE tasks SET lease_until=? WHERE task_id=? AND owner=? AND lease_until>? AND stage NOT IN ('CANCELLED','READY_TO_SUBMIT','SUBMITTED','VERIFIED')", (self.clock()+seconds, tid, owner, self.clock())).rowcount)

    def checkpoint(self, tid, owner, stage, *, blocker=None, details=None, release=False, page_url=None):
        if stage not in STAGES or stage in {"SUBMITTED", "VERIFIED"}:
            raise ValueError("worker cannot cross manual submission boundary")
        # Only key/status metadata, never field values or exception/site text.
        details = details or {}
        safe = {}
        for key in ("unresolved_keys", "filled_keys"):
            safe[key] = [k for k in details.get(key, []) if isinstance(k, str) and IDENTIFIER.fullmatch(k)]
        if stage == "READY_TO_SUBMIT":
            safe["final_review"] = {"final_click_actor": "user", "validated": True, "review_ref": tid, "manual_final_click_required": True}
        if blocker not in {None, "unknown_facts", "security_challenge", "otp_waiting", "otp_ambiguous", "validation", "retry_pending", "retry_exhausted", "live_not_authorized", "session_unavailable", "protected_target", "target_mismatch"}:
            raise ValueError("invalid blocker type")
        with self.tx() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=? AND owner=? AND lease_until>? AND stage NOT IN ('CANCELLED','READY_TO_SUBMIT','SUBMITTED','VERIFIED')", (tid, owner, self.clock())).fetchone()
            if not row:
                raise RuntimeError("lease lost or task stopped")
            if page_url:
                try:
                    TaskSpec.safe_url(page_url)
                    if urlsplit(page_url).netloc == urlsplit(json.loads(row["spec"])["target_url"]).netloc:
                        db.execute("UPDATE tasks SET checkpoint_url=? WHERE task_id=?", (page_url, tid))
                except ValueError:
                    pass  # Secret-bearing redirect URLs are never persisted.
            cp = stage if stage in SAFE_STAGES else row["checkpoint"]
            delay = min(60, 2**row["attempts"]) if blocker == "retry_pending" else 0
            db.execute("UPDATE tasks SET stage=?,checkpoint=?,blocker=?,details=?,owner=?,lease_until=?,next_run=?,updated=? WHERE task_id=?", (stage, cp, blocker, json.dumps(safe), None if release else owner, None if release else row["lease_until"], self.clock()+delay, self.clock(), tid))
            self._event(db, tid, "checkpoint", stage)

    def resume(self, tid):
        with self.tx() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
            self._view(row)
            if (row["owner"] and row["lease_until"] > self.clock()) or row["stage"] in STOPPED:
                raise ValueError("task active or at immutable human boundary")
            if row["attempts"] >= json.loads(row["spec"])["max_attempts"] and row["stage"] == "ERROR":
                raise ValueError("retry budget exhausted")
            assert_target_not_protected(json.loads(row["spec"])["target_url"])
            # Human input/action waits do not consume retry budget. If such a
            # wait was paused, pause() records that origin in the blocker so the
            # same reset semantics survive the BLOCKED intermediary state.
            legacy_human_wait = False
            if row["stage"] == "BLOCKED" and row["blocker"] == "user_paused":
                # Compatibility with pre-marker rows: old pause() overwrote the
                # blocker, but the preceding checkpoint event still records
                # whether the task was waiting for facts or a human action.
                previous_wait = db.execute(
                    "SELECT kind,stage FROM events WHERE task_id=? AND kind!='paused' "
                    "ORDER BY seq DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                legacy_human_wait = bool(
                    previous_wait
                    and previous_wait["kind"] == "checkpoint"
                    and previous_wait["stage"] in {"NEEDS_USER_INPUT", "NEEDS_USER_ACTION"}
                )
            human_wait = row["stage"] in {"NEEDS_USER_INPUT", "NEEDS_USER_ACTION"} or (
                row["stage"] == "BLOCKED"
                and row["blocker"] in {
                    "user_paused_from_input",
                    "user_paused_from_action",
                    "user_paused_from_otp_waiting",
                    "user_paused_from_otp_ambiguous",
                }
            ) or legacy_human_wait
            recoverable_block = (
                row["stage"] == "BLOCKED"
                and row["blocker"] in {
                    "session_unavailable",
                    "validation",
                    "user_paused_from_session_unavailable",
                    "user_paused_from_validation",
                }
            )
            attempts = 0 if (human_wait or recoverable_block) else row["attempts"]
            db.execute("UPDATE tasks SET stage=checkpoint,blocker=NULL,next_run=0,attempts=?,owner=NULL,lease_until=NULL,updated=? WHERE task_id=?", (attempts, self.clock(), tid))
            self._event(db, tid, "resumed", row["checkpoint"])
        return self.get(tid)

    def pause(self, tid):
        """Pause a task at its last safe checkpoint without cancelling it.

        An active worker is fenced immediately by clearing its lease. An in-flight
        browser primitive may finish, but the worker guard prevents subsequent
        mutations. Resume returns the task to its existing safe checkpoint.
        """
        with self.tx() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
            self._view(row)
            if row["stage"] in STOPPED or row["stage"] in {"SUBMITTED", "VERIFIED"}:
                raise ValueError("task is already at an immutable boundary")
            if row["stage"] == "ERROR" and row["blocker"] == "retry_exhausted":
                raise ValueError("retry budget exhausted")
            preserved_pause_markers = {
                "user_paused_from_input",
                "user_paused_from_action",
                "user_paused_from_otp_waiting",
                "user_paused_from_otp_ambiguous",
                "user_paused_from_session_unavailable",
                "user_paused_from_validation",
            }
            if row["stage"] == "BLOCKED" and row["blocker"] in preserved_pause_markers:
                pause_blocker = row["blocker"]
            elif row["stage"] == "BLOCKED" and row["blocker"] in {
                "session_unavailable",
                "validation",
            }:
                pause_blocker = "user_paused_from_" + row["blocker"]
            elif row["stage"] == "NEEDS_USER_ACTION" and row["blocker"] in {
                "otp_waiting",
                "otp_ambiguous",
            }:
                pause_blocker = "user_paused_from_" + row["blocker"]
            else:
                pause_blocker = {
                    "NEEDS_USER_INPUT": "user_paused_from_input",
                    "NEEDS_USER_ACTION": "user_paused_from_action",
                }.get(row["stage"], "user_paused")
            now = self.clock()
            active_claim = bool(
                row["owner"]
                and row["lease_until"] is not None
                and row["lease_until"] > now
            )
            paused_attempts = max(0, row["attempts"] - 1) if active_claim else row["attempts"]
            db.execute(
                "UPDATE tasks SET stage='BLOCKED',blocker=?,attempts=?,"
                "owner=NULL,lease_until=NULL,next_run=0,updated=? WHERE task_id=?",
                (pause_blocker, paused_attempts, now, tid),
            )
            self._event(db, tid, "paused", "BLOCKED")
        return self.get(tid)

    def cancel(self, tid):
        with self.tx() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
            self._view(row)
            if row["stage"] in {"SUBMITTED", "VERIFIED"}:
                raise ValueError("submitted task is read-only")
            db.execute("UPDATE tasks SET stage='CANCELLED',owner=NULL,lease_until=NULL,updated=? WHERE task_id=?", (self.clock(), tid))
            self._event(db, tid, "cancelled", "CANCELLED")
        return self.get(tid)
