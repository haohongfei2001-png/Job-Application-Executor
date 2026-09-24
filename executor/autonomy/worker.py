from __future__ import annotations

import copy
import fcntl
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .. import browser
from ..application import ApplicationExecutor
from ..models import ApplicationStage
from ..protected_targets import assert_target_not_protected
from ..settings import load_settings
from ..evidence import BOOL_KEYS, set_user_confirmed_field
from ..profile import DEFAULT_ALIASES
from ..facts.answers import TaskAnswerStore
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
        if plan.metadata.get("block_reason") == "draft persistence unverified":
            return "BLOCKED", "draft_persistence_unverified"
        if plan.metadata.get("block_reason") == "attachment draft receipt unverified":
            return "BLOCKED", "attachment_persistence_unverified"
        if plan.metadata.get("block_reason") == "form observation unavailable":
            return "BLOCKED", "form_observation_unavailable"
        if plan.metadata.get("auth_kind"):
            if plan.metadata["auth_kind"] in {
                    "return_target_unverified", "account_identity_unverified"}:
                return "BLOCKED", ("auth_return_unverified"
                                   if plan.metadata["auth_kind"] == "return_target_unverified"
                                   else "account_identity_unverified")
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
        self.answer_store = TaskAnswerStore(queue)
        self.active = None
        self._otp_watch_lock = threading.RLock()
        self._otp_watchers = set()
        self._otp_watch_retry_after = {}

    def _watch_late_otp(self):
        """Listen after the short browser wait without holding a task lease."""
        if not self.relay or not self.relay.enabled:
            return
        with self._otp_watch_lock:
            now = time.monotonic()
            self._otp_watch_retry_after = {
                aid: retry_at for aid, retry_at in self._otp_watch_retry_after.items()
                if retry_at > now or aid in self._otp_watchers}
        for task in self.queue.tasks():
            if task["stage"] != "NEEDS_USER_ACTION" or task["blocker"] != "otp_waiting":
                continue
            tid = task["task_id"]
            attempt = self.broker.attempts.valid_wait(tid)
            if not attempt:
                continue
            aid = attempt["attempt_id"]
            with self._otp_watch_lock:
                if (aid in self._otp_watchers or
                        time.monotonic() < self._otp_watch_retry_after.get(aid, 0)):
                    continue
                self._otp_watchers.add(aid)

            def listen(task_id=tid, attempt_id=aid, origin=attempt["origin"],
                       requested_at=attempt["requested_at"], deadline=attempt["deadline"]):
                try:
                    remaining = min(300, max(1, int(deadline - self.queue.clock())))
                    result = self.relay.wait_for_code(
                        origin, timeout_seconds=remaining, attempt_id=attempt_id,
                        requested_at=requested_at)
                    if (self.stop_event.is_set() or not isinstance(result, dict)
                            or result.get("attempt_id") != attempt_id
                            or result.get("origin") != origin):
                        return
                    accepted = self.broker.push(task_id=task_id, attempt_id=attempt_id,
                                                message=str(result.get("code") or ""))
                    if accepted["accepted"]:
                        current = self.queue.get(task_id)
                        if not current["owner"] and current["stage"] == "NEEDS_USER_ACTION":
                            self.queue.resume(task_id)
                except Exception:
                    # A disconnected source leaves the task at a visible wait.
                    pass
                finally:
                    with self._otp_watch_lock:
                        self._otp_watchers.discard(attempt_id)
                        self._otp_watch_retry_after[attempt_id] = time.monotonic() + 15

            threading.Thread(target=listen, daemon=True).start()

    def _runner_settings(self):
        settings = copy.deepcopy(self.settings)
        # Synthetic/isolated tests must never call an external model or Keychain.
        if browser.browser_mode() in {"isolated", "test", "headless"}:
            settings.setdefault("deepseek", {})["enabled"] = False
        return settings

    def _promote_pending_facts(self, tid: str, profile_ref: str) -> bool:
        try:
            for pending in self.answer_store.pending_reuse(tid):
                note = f"task-answer:{tid}:{pending['key']}:{pending['version']}"
                self.queue.begin_profile_write(profile_ref, task_id=tid)
                set_user_confirmed_field(profile_ref, pending["key"], pending["value"],
                                         note=note)
                self.answer_store.mark_reuse_applied(
                    pending["sequence"], task_id=tid, profile_ref=profile_ref)
            self.answer_store.finish_pending_task(tid, profile_ref)
            return True
        except (OSError, ValueError, RuntimeError):
            # The answer remains task-scoped and the consent stays pending;
            # retrying never replays an unconfirmed website write.
            return False

    def user_input(self, tid, answers, *, expected_revision=None, remember=False):
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
        if type(remember) is not bool:
            raise ValueError("explicit fact scope required")
        if remember and any(key.startswith(("policy.", "declaration.", "legal.")) for key in answers):
            raise ValueError("one-time declarations cannot become reusable facts")
        if remember and any(key not in DEFAULT_ALIASES for key in answers):
            raise ValueError("reusable fact requires a known canonical key")
        normalized = dict(answers)
        for key, value in answers.items():
            if key not in BOOL_KEYS:
                continue
            if type(value) is bool:
                normalized[key] = value
            elif isinstance(value, str):
                chosen = value.strip().casefold()
                if chosen in {"true", "yes", "是", "同意", "1"}:
                    normalized[key] = True
                elif chosen in {"false", "no", "否", "不同意", "0"}:
                    normalized[key] = False
                else:
                    raise ValueError("boolean fact requires explicit yes or no")
            else:
                raise ValueError("boolean fact requires explicit yes or no")
        if remember:
            profile_path = Path(task["spec"]["profile_ref"]).expanduser().resolve()
            profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
            if not isinstance(profile_data, dict) or (profile_data and "fields" not in profile_data):
                raise ValueError("reusable fact requires a canonical profile")
        # Answer and transition share one SQLite transaction: rejected input
        # cannot be applied later by a restarted worker.
        resumed = self.answer_store.save_and_resume(
            tid, normalized, expected_revision=expected_revision, remember=remember)
        with self.answers_lock:
            self.answers.setdefault(tid, {}).update(normalized)
        if remember:
            resumed["fact_reuse_status"] = (
                "SAVED" if self._promote_pending_facts(tid, task["spec"]["profile_ref"])
                else "PENDING"
            )
            resumed.update(self.queue.get(tid))
        return resumed

    def run_once(self):
        for pending_tid in self.answer_store.pending_tasks():
            try:
                pending_task = self.queue.get(pending_tid)
                self._promote_pending_facts(
                    pending_tid, pending_task["spec"]["profile_ref"])
            except (KeyError, ValueError):
                pass
        task = self.queue.claim("worker-" + str(os.getpid()))
        if not task:
            self.broker.expire()
            self._watch_late_otp()
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
                session_epoch = browser.owned_cdp_fingerprint()
                if session_epoch is None:
                    checkpoint("BLOCKED", blocker="session_unavailable", release=True)
                    return True
                prior_runs = self.queue.run_attempts(tid)
                if prior_runs:
                    # The only permitted continuation is a code already bound
                    # to the one SMS request stopped at the original auth page.
                    # Read-only observation proves same process/tab/document
                    # and active OTP controls before another run intent exists.
                    auth_attempt = self.broker.attempts.valid_wait(tid)
                    binding = self.queue.browser_binding(tid)
                    if (len(prior_runs) != 1
                            or prior_runs[0]["outcome"] != "RETURNED_UNVERIFIED"
                            or task["checkpoint"] != "DISCOVERED"
                            or not auth_attempt
                            or auth_attempt["send_outcome"] not in {"CLICK_OBSERVED", "SEND_UNKNOWN"}
                            or not self.broker.pending(tid)
                            or not binding
                            or binding["process_epoch"] != session_epoch
                            or browser.observe_bound_auth(
                                spec["target_url"], binding,
                                auth_attempt["origin"]) != "BOUND_OTP_WAIT_OBSERVED"):
                        checkpoint("BLOCKED", blocker="browser_ownership_unknown", release=True)
                        return True
            audit = OperationalAudit(self.queue.root, tid, checkpoint, guard)
            bridge = BrokerBridge(self.broker, tid, self.queue, owner, guard,
                                  relay=self.relay, target_url=spec["target_url"])
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
            runner.user_answers = [
                {"canonical_key": key, "field_id": key, "value": value}
                for key, value in self.answer_store.load(tid).items()
            ]
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
