from __future__ import annotations

import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit


HOST = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}$")
ACTIVE = {"PREPARED", "SEND_UNKNOWN", "CLICK_OBSERVED", "EXTERNAL_OBSERVED"}


class AuthAttemptStore:
    """Durable SMS attempt metadata only; code and phone value never enter SQLite."""

    def __init__(self, queue, *, ttl_seconds: int = 300):
        self.queue = queue
        self.ttl = min(max(int(ttl_seconds), 30), 300)
        with queue.tx() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS auth_attempts (
                attempt_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                origin TEXT NOT NULL,
                return_target_digest TEXT NOT NULL,
                mode TEXT NOT NULL,
                phone_fact_ref TEXT NOT NULL,
                send_outcome TEXT NOT NULL,
                requested_at REAL,
                deadline REAL,
                cooldown_until REAL,
                current INTEGER NOT NULL,
                created REAL NOT NULL,
                updated REAL NOT NULL
            )""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_current_auth_attempt
                ON auth_attempts(task_id) WHERE current=1""")

    @staticmethod
    def _view(row):
        return dict(row) if row else None

    @staticmethod
    def _host(origin: str) -> str:
        host = (urlsplit(origin).hostname if "://" in origin else origin).casefold().strip(".")
        if not HOST.fullmatch(host):
            raise ValueError("invalid authentication origin")
        return host

    def current(self, task_id: str) -> dict | None:
        with self.queue.tx() as db:
            row = db.execute("""SELECT * FROM auth_attempts WHERE task_id=? AND current=1""",
                             (task_id,)).fetchone()
            return self._view(row)

    def begin(self, task_id: str, owner: str, origin: str, return_target: str) -> dict:
        """Bind one SMS attempt to the leased task and exact same-origin target."""
        host = self._host(origin)
        digest = hashlib.sha256(return_target.encode()).hexdigest()
        with self.queue.tx() as db:
            task = db.execute("SELECT owner,lease_until,spec FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
            if not task or task["owner"] != owner or task["lease_until"] <= self.queue.clock():
                raise RuntimeError("authentication task lease lost")
            target = json.loads(task["spec"])["target_url"]
            if target != return_target or urlsplit(target).hostname != host:
                raise ValueError("authentication origin or return target is unverified")
            current = db.execute("SELECT * FROM auth_attempts WHERE task_id=? AND current=1",
                                 (task_id,)).fetchone()
            if current:
                if current["origin"] != host or current["return_target_digest"] != digest:
                    raise RuntimeError("authentication attempt target changed")
                return self._view(current)
            now = self.queue.clock()
            attempt_id = uuid.uuid4().hex
            db.execute("""INSERT INTO auth_attempts
                (attempt_id,task_id,origin,return_target_digest,mode,phone_fact_ref,
                 send_outcome,requested_at,deadline,cooldown_until,current,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (attempt_id, task_id, host, digest, "sms", "identity.phone", "PREPARED",
                 None, None, None, 1, now, now))
            return self._view(db.execute("SELECT * FROM auth_attempts WHERE attempt_id=?",
                                         (attempt_id,)).fetchone())

    def record_send_intent(self, attempt_id: str, task_id: str, owner: str) -> dict:
        """Commit a conservative unknown-effect marker before clicking send."""
        with self.queue.tx() as db:
            task = db.execute("SELECT owner,lease_until FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
            row = db.execute("""SELECT * FROM auth_attempts
                WHERE attempt_id=? AND task_id=? AND current=1""",
                (attempt_id, task_id)).fetchone()
            if not task or task["owner"] != owner or task["lease_until"] <= self.queue.clock():
                raise RuntimeError("authentication task lease lost")
            if not row or row["send_outcome"] != "PREPARED":
                raise RuntimeError("SMS send already attempted or outcome unknown")
            now = self.queue.clock()
            db.execute("""UPDATE auth_attempts SET send_outcome='SEND_UNKNOWN',
                requested_at=?,deadline=?,cooldown_until=?,updated=? WHERE attempt_id=?""",
                (now, now + self.ttl, now + 60, now, attempt_id))
            return self._view(db.execute("SELECT * FROM auth_attempts WHERE attempt_id=?",
                                         (attempt_id,)).fetchone())

    def record_send_result(self, attempt_id: str, *, outcome: str) -> dict:
        if outcome not in {"CLICK_OBSERVED", "SEND_UNKNOWN"}:
            raise ValueError("invalid non-secret send result")
        with self.queue.tx() as db:
            row = db.execute("SELECT * FROM auth_attempts WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if not row or row["send_outcome"] not in {"SEND_UNKNOWN", outcome}:
                raise RuntimeError("SMS send result without durable intent")
            db.execute("UPDATE auth_attempts SET send_outcome=?,updated=? WHERE attempt_id=?",
                       (outcome, self.queue.clock(), attempt_id))
            return self._view(db.execute("SELECT * FROM auth_attempts WHERE attempt_id=?",
                                         (attempt_id,)).fetchone())

    def observe_external_wait(self, attempt_id: str, task_id: str, owner: str) -> dict:
        """Bind an already-shown OTP control in an isolated fixture only."""
        with self.queue.tx() as db:
            task = db.execute("SELECT owner,lease_until FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
            row = db.execute("SELECT * FROM auth_attempts WHERE attempt_id=? AND task_id=?",
                             (attempt_id, task_id)).fetchone()
            if not task or task["owner"] != owner or task["lease_until"] <= self.queue.clock():
                raise RuntimeError("authentication task lease lost")
            if not row or row["send_outcome"] != "PREPARED":
                raise RuntimeError("existing OTP wait cannot replace an attempted send")
            now = self.queue.clock()
            db.execute("""UPDATE auth_attempts SET send_outcome='EXTERNAL_OBSERVED',
                requested_at=?,deadline=?,updated=? WHERE attempt_id=?""",
                (now, now + self.ttl, now, attempt_id))
            return self._view(db.execute("SELECT * FROM auth_attempts WHERE attempt_id=?",
                                         (attempt_id,)).fetchone())

    def valid_wait(self, task_id: str, *, attempt_id: str | None = None,
                   origin: str | None = None) -> dict | None:
        row = self.current(task_id)
        if (not row or row["send_outcome"] not in {"CLICK_OBSERVED", "SEND_UNKNOWN", "EXTERNAL_OBSERVED"}
                or not row["deadline"] or row["deadline"] <= self.queue.clock()):
            return None
        if attempt_id is not None and row["attempt_id"] != attempt_id:
            return None
        if origin is not None and row["origin"] != self._host(origin):
            return None
        return row

    def close(self, task_id: str, attempt_id: str, *, outcome: str) -> None:
        if outcome not in {"AUTHENTICATED", "EXPIRED", "CANCELLED", "UNVERIFIED"}:
            raise ValueError("invalid auth attempt outcome")
        with self.queue.tx() as db:
            db.execute("""UPDATE auth_attempts SET current=0,send_outcome=?,updated=?
                WHERE task_id=? AND attempt_id=? AND current=1""",
                (outcome, self.queue.clock(), task_id, attempt_id))
