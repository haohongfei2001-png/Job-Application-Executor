from __future__ import annotations

import copy
import fcntl
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .. import browser
from ..application import ApplicationExecutor
from ..models import ApplicationStage
from ..protected_targets import assert_target_not_protected
from ..settings import load_settings
from .otp import BrokerBridge, OtpBroker
from .queue import RUNTIME, STOPPED, private_dir, IDENTIFIER


class LeaseLost(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def __enter__(self):
        private_dir(self.path.parent)
        self.handle = self.path.open("a+")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise RuntimeError("another local worker is active") from None
        return self

    def __exit__(self, *_):
        self.handle.close()


def outcome(plan):
    if plan.stage == ApplicationStage.BLOCKED:
        if plan.metadata.get("auth_kind"):
            return "NEEDS_USER_ACTION", "otp_waiting" if plan.metadata["auth_kind"] == "one_time_code" else "security_challenge"
        if plan.unresolved_fields:
            return "NEEDS_USER_INPUT", "unknown_facts"
        return "BLOCKED", "validation"
    stage = str(plan.stage)
    if stage in {"SUBMITTED", "VERIFIED"}:
        raise RuntimeError("worker cannot submit")
    return stage, None


class OperationalAudit:
    """No raw field/site/exception text or screenshots in daemon audit artifacts."""
    def __init__(self, root, task_id, checkpoint, guard):
        self.root = private_dir(Path(root) / "reviews" / task_id)
        self.checkpoint, self.guard = checkpoint, guard

    def load_user_answers(self):
        return []

    def save_plan(self, plan):
        self.guard()
        stage, blocker = outcome(plan)
        keys = [i.canonical_key or i.field_id for i in plan.unresolved_fields]
        self.checkpoint("VALIDATED" if stage == "READY_TO_SUBMIT" else stage,
                        blocker=blocker, details={"unresolved_keys": keys}, page_url=plan.metadata.get("checkpoint_url"))
        safe = {"execution_id": plan.execution_id, "stage": stage,
                "fields": [{"field": i.canonical_key or i.field_id, "source": i.source, "status": str(i.status)} for i in plan.fields if IDENTIFIER.fullmatch(i.canonical_key or i.field_id)],
                "manual_final_click_required": True}
        if stage == "READY_TO_SUBMIT":
            review = plan.metadata.get("final_review") or {}
            safe["final_review"] = {"final_click_actor": "user", "unresolved_field_count": len(plan.unresolved_fields),
                "attachment_types": list(plan.attachments), "checklist": review.get("checklist", []),
                "project_coverage_status": (review.get("project_coverage") or {}).get("status"),
                "final_control": plan.metadata.get("final_submit_control") if plan.metadata.get("final_submit_control") in {"提交", "确认提交", "确认投递", "提交申请", "Submit", "Submit application"} else "identified_on_live_page"}
        path = self.root / "review.json"
        path.write_text(json.dumps(safe, ensure_ascii=False, indent=2))
        path.chmod(0o600)
        return path

    def record_action(self, action):
        self.guard()
        # Queue events already record transitions. Do not retain arbitrary action text.

    def screenshot_path(self, name):
        raise RuntimeError("daemon screenshots disabled for privacy")


class Worker:
    def __init__(
        self,
        queue,
        broker=None,
        *,
        runner_factory=ApplicationExecutor,
        relay=None,
        settings=None,
    ):
        self.queue = queue
        self.broker = broker or OtpBroker(queue)
        self.runner_factory, self.relay = runner_factory, relay
        self.settings = settings if settings is not None else load_settings()
        self.stop_event = threading.Event()
        self.answers = {}
        self.answers_lock = threading.RLock()
        self.active = None

    def _runner_settings(self):
        settings = copy.deepcopy(self.settings)
        # Synthetic/isolated tests must never call an external model or Keychain.
        if browser.browser_mode() in {"isolated", "test", "headless"}:
            settings.setdefault("deepseek", {})["enabled"] = False
        return settings

    def user_input(self, tid, answers, *, expected_revision=None):
        task = self.queue.get(tid)
        if expected_revision is None:
            expected_revision = task["revision"]
        if expected_revision is not None and task["revision"] != expected_revision:
            raise RuntimeError("stale task revision")
        if task["owner"] or task["stage"] != "NEEDS_USER_INPUT":
            raise ValueError("task is not waiting for facts")
        if not isinstance(answers, dict) or not answers or len(answers) > 100:
            raise ValueError("answers must be a canonical-key mapping")
        unresolved = set(task["details"].get("unresolved_keys", []))
        if not set(answers).issubset(unresolved):
            raise ValueError("answer keys must match pending facts")
        if any(not isinstance(v, (str, int, bool, float)) or len(str(v)) > 10000 for v in answers.values()):
            raise ValueError("invalid answer value")
        if any(any(x in k.lower() for x in ("password", "cookie", "token", "otp", "secret")) for k in answers):
            raise ValueError("use OTP ingestion for authentication")
        with self.answers_lock:
            previous = dict(self.answers.get(tid, {}))
            self.answers.setdefault(tid, {}).update(answers)
            try:
                return self.queue.resume(tid, expected_revision=expected_revision)
            except BaseException:
                if previous:
                    self.answers[tid] = previous
                else:
                    self.answers.pop(tid, None)
                raise

    def run_once(self):
        task = self.queue.claim("worker-" + str(os.getpid()))
        if not task:
            self.broker.expire()
            return False
        tid, owner, spec = task["task_id"], task["owner"], task["spec"]
        done, lost = threading.Event(), threading.Event()
        self.active = tid
        session_epoch = None
        attempt_id = None

        def guard():
            if self.stop_event.is_set() or lost.is_set():
                raise LeaseLost("worker interrupted")
            current = self.queue.get(tid)
            if current["owner"] != owner or current["lease_until"] <= self.queue.clock():
                raise LeaseLost("lease lost")
            if session_epoch is not None and browser.owned_cdp_fingerprint() != session_epoch:
                raise browser.BrowserOwnershipError("owned browser process changed")

        def keep_lease():
            while not done.wait(10):
                if not self.queue.renew(tid, owner):
                    lost.set()
                    break

        def checkpoint(stage, **kw):
            self.queue.checkpoint(tid, owner, stage, **kw)

        keeper = threading.Thread(target=keep_lease, daemon=True)
        keeper.start()
        try:
            guard()
            try:
                if spec.get("tenant") and spec.get("job_id"):
                    assert_target_not_protected(spec["target_url"],
                                                tenant=spec["tenant"],
                                                job_id=spec["job_id"],
                                                campaign=spec.get("campaign", ""))
                else:
                    assert_target_not_protected(spec["target_url"])
            except RuntimeError:
                checkpoint("BLOCKED", blocker="protected_target", release=True)
                return True
            if browser.browser_mode() in {"isolated", "test", "headless"} and self.runner_factory is ApplicationExecutor:
                target = urlsplit(spec["target_url"])
                if not (target.scheme == "file" or (
                    target.scheme in {"http", "https"}
                    and target.hostname in {"127.0.0.1", "localhost"}
                )):
                    checkpoint("BLOCKED", blocker="isolated_external_target", release=True)
                    return True
            if browser.browser_mode() not in {"isolated", "test", "headless"}:
                if not spec["live_authorized"]:
                    checkpoint("BLOCKED", blocker="live_not_authorized", release=True)
                    return True
                # Until a durable task-to-tab binding is observed, a prior
                # runner return cannot justify a second live browser write.
                if self.queue.run_attempts(tid):
                    checkpoint("BLOCKED", blocker="browser_ownership_unknown", release=True)
                    return True
                session_epoch = browser.owned_cdp_fingerprint()
                if session_epoch is None:
                    checkpoint("BLOCKED", blocker="session_unavailable", release=True)
                    return True
            audit = OperationalAudit(self.queue.root, tid, checkpoint, guard)
            bridge = BrokerBridge(self.broker, tid, self.queue, owner, guard, relay=self.relay)
            runner = self.runner_factory(spec["target_url"], spec["profile_ref"], self._runner_settings(), execution_id=tid,
                otp_bridge=bridge, audit_store=audit, guard=guard,
                resume_url=task.get("checkpoint_url"), existing_browser_only=True)
            if session_epoch is not None:
                runner.browser_session_epoch = session_epoch
                runner.browser_binding_get = lambda: self.queue.browser_binding(tid)
                runner.browser_binding_set = lambda target_id, previous_target_id=None: self.queue.bind_browser_page(
                    tid, owner, session_epoch, target_id,
                    previous_target_id=previous_target_id,
                )
                runner.browser_document_set = lambda target_id, document_epoch: self.queue.record_browser_document(
                    tid, owner, session_epoch, target_id, document_epoch,
                )
            with self.answers_lock:
                runner.user_answers = [{"canonical_key": k, "field_id": k, "value": v} for k, v in self.answers.get(tid, {}).items()]
            runner.plan.metadata["recovered_from_stage"] = task["checkpoint"]
            if spec["attachment_refs"]:
                for key, path in spec["attachment_refs"].items():
                    # References become assets in the existing profile/attachment resolver.
                    runner.profile.setdefault("assets", {})[key] = {"path": path, "kind": key}
                    runner.plan.attachments[key] = path
            attempt_id = self.queue.begin_run_attempt(tid, owner)
            plan = runner.run()
            stage, blocker = outcome(plan)
            checkpoint(stage, blocker=blocker, details={"unresolved_keys": [x.canonical_key or x.field_id for x in plan.unresolved_fields]}, release=True)
            self.queue.finish_run_attempt(attempt_id, "RETURNED_UNVERIFIED")
            if blocker == "otp_waiting" and self.broker.pending(tid):
                self.queue.resume(tid)
            if stage in STOPPED:
                with self.answers_lock:
                    self.answers.pop(tid, None)
                self.broker.discard(tid)
        except browser.BrowserOwnershipError:
            if attempt_id is not None:
                self.queue.finish_run_attempt(attempt_id, "UNKNOWN_OUTCOME")
            try:
                checkpoint("BLOCKED", blocker="browser_ownership_unknown", release=True)
            except RuntimeError:
                pass
        except LeaseLost:
            if attempt_id is not None:
                self.queue.finish_run_attempt(attempt_id, "UNKNOWN_OUTCOME")
            pass  # Cancellation/stop fences the next operation; stale lease is recoverable.
        except Exception:
            if attempt_id is not None:
                try:
                    self.queue.finish_run_attempt(attempt_id, "UNKNOWN_OUTCOME")
                except RuntimeError:
                    pass
            try:
                checkpoint("BLOCKED" if attempt_id is not None else "ERROR",
                           blocker="unknown_outcome" if attempt_id is not None else
                           ("retry_pending" if task["attempts"] < spec["max_attempts"] else "retry_exhausted"),
                           release=True)
            except RuntimeError:
                pass
        finally:
            done.set()
            keeper.join(timeout=1)
            self.active = None
        return True

    def run_forever(self):
        while not self.stop_event.is_set():
            if not self.run_once():
                self.stop_event.wait(0.5)
