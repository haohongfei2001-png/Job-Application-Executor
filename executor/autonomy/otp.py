from __future__ import annotations

import re
import hashlib
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ..auth.attempts import AuthAttemptStore
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
    attempt_id: str
    origin: str


class OtpBroker:
    def __init__(self, queue, *, ttl=300, clock=time.monotonic, attempts=None):
        self.queue, self.ttl, self.clock = queue, min(max(ttl, 1), 300), clock
        self.attempts = attempts or AuthAttemptStore(queue)
        self._codes = {}
        self._seen = {}
        self._lock = threading.RLock()

    def _purge(self):
        now = self.clock()
        self._codes = {k: v for k, v in self._codes.items() if v.expires > now}
        self._seen = {k: v for k, v in self._seen.items() if v > now}

    def push(self, *, message, task_id=None, hint=None, attempt_id=None):
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            return {"accepted": False, "reason": "auth_attempt_required"}
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
        attempt = self.attempts.valid_wait(tid, attempt_id=attempt_id)
        if attempt is None:
            return {"accepted": False, "reason": "missing_or_expired_auth_attempt"}
        origin = attempt["origin"]
        with self._lock:
            self._purge()
            fingerprint = (tid, hashlib.sha256(code.encode()).digest())
            if fingerprint in self._seen:
                return {"accepted": False, "reason": "duplicate_code"}
            self._seen[fingerprint] = self.clock()+self.ttl
            # Two unconsumed messages are ambiguous, even for the same task.
            key = (tid, attempt["attempt_id"], origin)
            if key in self._codes:
                self._codes.pop(key, None)
                return {"accepted": False, "reason": "ambiguous_code"}
            self._codes[key] = Code(code, self.clock()+self.ttl,
                                    attempt["attempt_id"], origin)
        return {"accepted": True, "task_id": tid, "attempt_id": attempt["attempt_id"],
                "expires_in": self.ttl}

    def consume(self, task_id, *, attempt_id=None, origin=None):
        with self._lock:
            self._purge()
            attempt = self.attempts.valid_wait(task_id, attempt_id=attempt_id, origin=origin)
            if attempt is None:
                return None
            key = (task_id, attempt["attempt_id"], attempt["origin"])
            entry = self._codes.pop(key, None)
            return entry.value if entry else None

    def discard(self, task_id):
        with self._lock:
            self._codes = {key: value for key, value in self._codes.items()
                           if key[0] != task_id}

    def pending(self, task_id):
        with self._lock:
            self._purge()
            attempt = self.attempts.valid_wait(task_id)
            return bool(attempt and (task_id, attempt["attempt_id"], attempt["origin"])
                        in self._codes)

    def expire(self):
        with self._lock:
            self._purge()


class BrokerBridge:
    """Existing executor OTP protocol, fed by authenticated local ingestion.

    An already configured iPhone relay remains optional and narrowly scoped.
    """
    enabled = True

    def __init__(self, broker, task_id, queue, owner, guard, *, relay=None, target_url=None):
        self.broker, self.task_id, self.queue, self.owner, self.guard = broker, task_id, queue, owner, guard
        self.relay = relay
        self.target_url = target_url
        self.attempts = broker.attempts
        self.attempt = None

    def begin_attempt(self, site):
        self.attempt = self.attempts.begin(self.task_id, self.owner, site, self.target_url)
        return self.attempt

    def before_send(self):
        self.guard()
        self.attempt = self.attempts.record_send_intent(
            self.attempt["attempt_id"], self.task_id, self.owner)

    def after_send(self):
        self.attempt = self.attempts.record_send_result(
            self.attempt["attempt_id"], outcome="CLICK_OBSERVED")

    def observe_existing(self):
        self.attempt = self.attempts.observe_external_wait(
            self.attempt["attempt_id"], self.task_id, self.owner)

    def complete_attempt(self):
        if self.attempt:
            self.attempts.close(self.task_id, self.attempt["attempt_id"],
                                outcome="AUTHENTICATED")
            self.broker.discard(self.task_id)

    def wait_for_code(self, site):
        self.queue.checkpoint(self.task_id, self.owner, "NEEDS_USER_ACTION", blocker="otp_waiting")
        self.guard()
        attempt = self.attempts.valid_wait(self.task_id,
                                           attempt_id=self.attempt["attempt_id"],
                                           origin=site) if self.attempt else None
        if not attempt:
            return None
        code = self.broker.consume(self.task_id, attempt_id=attempt["attempt_id"],
                                   origin=site)
        if code:
            return {"code": code, "source": "local_broker"}
        # The existing bridge is only polled if explicitly enabled by private config.
        if self.relay and self.relay.enabled:
            response = self.relay.wait_for_code(
                site, timeout_seconds=2, attempt_id=attempt["attempt_id"],
                requested_at=attempt["requested_at"])
            if (isinstance(response, dict) and
                    response.get("attempt_id") == attempt["attempt_id"] and
                    response.get("origin") == attempt["origin"]):
                return response
        return None
