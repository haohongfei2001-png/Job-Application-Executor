from __future__ import annotations

import re
import hashlib
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..otp.bridge import OtpBridge


def extract_code(message: str) -> str | None:
    """Reject messages with multiple plausible codes, rather than guess."""
    if not isinstance(message, str) or len(message) > 4096:
        return None
    hits = set(re.findall(r"(?<!\d)(\d{4,8})(?!\d)", message))
    return hits.pop() if len(hits) == 1 else None


@dataclass(repr=False)
class Code:
    value: str = field(repr=False)
    expires: float


class OtpBroker:
    def __init__(self, queue, *, ttl=300, clock=time.monotonic):
        self.queue, self.ttl, self.clock = queue, min(max(ttl, 1), 300), clock
        self._codes = {}
        self._seen = {}
        self._lock = threading.RLock()

    def _purge(self):
        now = self.clock()
        self._codes = {k: v for k, v in self._codes.items() if v.expires > now}
        self._seen = {k: v for k, v in self._seen.items() if v > now}

    def push(self, *, message, task_id=None, hint=None):
        code = extract_code(message)
        if code is None:
            return {"accepted": False, "reason": "ambiguous_or_missing_code"}
        tasks = [t for t in self.queue.tasks() if t["blocker"] == "otp_waiting" and t["stage"] == "NEEDS_USER_ACTION"]
        if task_id:
            matches = [t for t in tasks if t["task_id"] == task_id]
        elif hint:
            normalized = hint.strip().casefold()
            matches = [t for t in tasks if normalized in {t["spec"]["company"].casefold(), urlsplit(t["spec"]["target_url"]).hostname}]
        else:
            matches = []
        if len(matches) != 1:
            return {"accepted": False, "reason": "ambiguous_or_missing_task"}
        tid = matches[0]["task_id"]
        with self._lock:
            self._purge()
            fingerprint = (tid, hashlib.sha256(code.encode()).digest())
            if fingerprint in self._seen:
                return {"accepted": False, "reason": "duplicate_code"}
            self._seen[fingerprint] = self.clock()+self.ttl
            # Two unconsumed messages are ambiguous, even for the same task.
            if tid in self._codes:
                self._codes.pop(tid, None)
                return {"accepted": False, "reason": "ambiguous_code"}
            self._codes[tid] = Code(code, self.clock()+self.ttl)
        return {"accepted": True, "task_id": tid, "expires_in": self.ttl}

    def consume(self, task_id):
        with self._lock:
            self._purge()
            entry = self._codes.pop(task_id, None)
            return entry.value if entry else None

    def discard(self, task_id):
        with self._lock:
            self._codes.pop(task_id, None)

    def pending(self, task_id):
        with self._lock:
            self._purge()
            return task_id in self._codes

    def expire(self):
        with self._lock:
            self._purge()


class BrokerBridge:
    """Existing executor OTP protocol, fed by authenticated local ingestion.

    An already configured iPhone relay remains optional and narrowly scoped.
    """
    enabled = True

    def __init__(self, broker, task_id, queue, owner, guard, *, relay=None):
        self.broker, self.task_id, self.queue, self.owner, self.guard = broker, task_id, queue, owner, guard
        self.relay = relay

    def wait_for_code(self, site):
        self.queue.checkpoint(self.task_id, self.owner, "NEEDS_USER_ACTION", blocker="otp_waiting")
        self.guard()
        code = self.broker.consume(self.task_id)
        if code:
            return {"code": code, "source": "local_broker"}
        # The existing bridge is only polled if explicitly enabled by private config.
        if self.relay and self.relay.enabled:
            return self.relay.wait_for_code(site, timeout_seconds=2)
        return None
