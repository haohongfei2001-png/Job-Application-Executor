from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import browser
from .dashboard import DASHBOARD_HTML
from .commands import CommandEnvelope
from .diagnostics import collect_diagnostics
from .manager import ManagerController, safe_task_view
from .queue import TaskQueue, TaskSpec, private_dir
from .updater import reconciled_update_state, safe_to_update, spawn_update
from .worker import Worker


UI_COOKIE = "application_executor_session"


def local_token(root):
    token = os.getenv("APPLICATION_EXECUTOR_LOCAL_TOKEN")
    if token:
        if len(token) < 32:
            raise ValueError("local auth token must have at least 32 characters")
        return token
    path = private_dir(root) / "auth.token"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("private token permissions required")
    token = path.read_text().strip()
    if len(token) < 32:
        raise ValueError("invalid private auth token")
    return token


class Supervisor:
    def __init__(self, queue, worker=None, token=None, manager=None):
        self.queue, self.worker = queue, worker or Worker(queue)
        self.token = token or local_token(queue.root)
        if len(self.token) < 32:
            raise ValueError("local auth token too short")
        self.manager = manager or ManagerController(queue, self.worker)
        self._ui_lock = threading.RLock()
        self._mutation_lock = threading.RLock()
        self._command_lock = threading.RLock()
        self._ui_tickets: dict[str, float] = {}
        self._ui_sessions: dict[str, float] = {}

    def _expire_ui(self):
        now = time.monotonic()
        self._ui_tickets = {k: v for k, v in self._ui_tickets.items() if v > now}
        self._ui_sessions = {k: v for k, v in self._ui_sessions.items() if v > now}

    def issue_ui_ticket(self) -> str:
        with self._ui_lock:
            self._expire_ui()
            ticket = secrets.token_urlsafe(24)
            self._ui_tickets[ticket] = time.monotonic() + 60
            return ticket

    def consume_ui_ticket(self, ticket: str) -> str | None:
        if not ticket:
            return None
        with self._ui_lock:
            self._expire_ui()
            expiry = self._ui_tickets.pop(ticket, 0)
            if expiry <= time.monotonic():
                return None
            session = secrets.token_urlsafe(24)
            self._ui_sessions[session] = time.monotonic() + 3600
            return session

    def valid_ui_session(self, session: str) -> bool:
        if not session:
            return False
        with self._ui_lock:
            self._expire_ui()
            expiry = self._ui_sessions.get(session, 0)
            if expiry <= time.monotonic():
                self._ui_sessions.pop(session, None)
                return False
            return True

    def ui_state(self):
        state = self.manager.state()
        for task in state.get("tasks", []):
            if task.get("stage") == "READY_TO_SUBMIT":
                queue_task = self.queue.get(task["task_id"])
                spec = queue_task.get("spec") or {}
                task["can_confirm_submission"] = bool(
                    spec.get("target_verified") is True
                    and spec.get("tenant") and spec.get("job_id"))
                details = queue_task.get("details") or {}
                review = details.get("final_review") or {}
                if not isinstance(review, dict):
                    review = {}
                certificate = review.get("certificate")
                checks = certificate.get("checks") if isinstance(certificate, dict) else None
                required_checks = {
                    "target_account_draft", "complete_fields_defaults",
                    "structured_rows", "attachments", "validation_save",
                    "manual_submit_boundary",
                }
                if (review.get("validated") is True
                        and review.get("final_click_actor") == "user"
                        and isinstance(checks, dict) and set(checks) == required_checks
                        and set(checks.values()) == {"PASS"}
                        and all(type(certificate.get(key)) is int and certificate[key] >= 0
                                for key in ("field_count", "attachment_count", "row_count"))):
                    task["review_summary"] = {
                        "status": "last_verified",
                        "field_count": certificate["field_count"],
                        "attachment_count": certificate["attachment_count"],
                        "row_count": certificate["row_count"],
                        "check_count": len(checks),
                    }
                else:
                    task["review_summary"] = {"status": "unavailable"}
                continue
            if task.get("stage") != "NEEDS_USER_ACTION" or task.get("blocker") != "otp_waiting":
                continue
            attempt = self.worker.broker.attempts.valid_wait(task["task_id"])
            if not attempt:
                previous = self.worker.broker.attempts.current(task["task_id"])
                task["otp_status"] = "attempt_expired_or_unverified"
                if previous and previous["send_outcome"] in {"CLICK_OBSERVED", "SEND_UNKNOWN"}:
                    task["resend_eligible"] = True
                    task["resend_wait_seconds"] = max(
                        0, int((previous.get("cooldown_until") or self.queue.clock())
                               - self.queue.clock()))
                continue
            task["auth_attempt_id"] = attempt["attempt_id"]
            task["otp_status"] = "waiting"
            task["resend_eligible"] = attempt["send_outcome"] in {
                "CLICK_OBSERVED", "SEND_UNKNOWN"}
            task["resend_wait_seconds"] = max(
                0, int((attempt.get("cooldown_until") or self.queue.clock()) - self.queue.clock()))
            relay = self.worker.relay
            local_ready = bool(relay and getattr(relay, "local_messages_enabled", False)
                               and all(getattr(relay, "_rule_for", lambda _site: {})(
                                   attempt["origin"]).get(key)
                                       for key in ("sender_hint", "body_keyword")))
            task["otp_source"] = (
                "configured_unverified" if relay and (
                    getattr(relay, "relay_enabled", False) or local_ready)
                else "local_input_only")
        return {
            "ok": True,
            "worker_active": self.worker.active,
            "update": reconciled_update_state(self.queue.root),
            **state,
        }

    def diagnostics(self):
        return collect_diagnostics(
            self,
            repo_root=Path(__file__).resolve().parents[2],
        )

    def readiness(self):
        from .consumer import humanize_preflight
        from .preflight import collect_live_preflight

        result = collect_live_preflight(supervisor_running=True)
        return {**result, "message": humanize_preflight(result)}

    def observe_task(self, tid: str):
        task = self.queue.get(tid)
        if task["owner"] or task["stage"] != "BLOCKED" or task["blocker"] not in {
            "unknown_outcome", "browser_ownership_unknown",
            "user_paused_from_unknown_outcome", "user_paused_from_browser_ownership_unknown",
        }:
            raise ValueError("task is not waiting for read-only reconciliation")
        observed = browser.observe_bound_draft(
            task["spec"]["target_url"], self.queue.browser_binding(tid)
        )
        return {"ok": True, "task_id": tid, **observed}

    def observe_submission(self, tid: str):
        """Inspect the already-owned task tab after a possible human submit."""
        task = self.queue.get(tid)
        if task["stage"] != "READY_TO_SUBMIT" or task["owner"]:
            raise ValueError("task is not at the human submit boundary")
        observed = browser.observe_bound_submission(
            task["spec"]["target_url"], self.queue.browser_binding(tid),
        )
        return {"ok": True, "task_id": tid, **observed}

    def confirm_human_submission(self, tid: str, expected_revision: int):
        """Accept only a local user's after-the-fact confirmation."""
        observed = self.observe_submission(tid)
        task = self.queue.confirm_human_submission(
            tid, expected_revision=expected_revision, user_confirmed=True,
            observation=observed,
        )
        submission = task["details"]["submission"]
        return {
            "ok": True, "task_id": tid, "stage": task["stage"],
            "revision": task["revision"], "level": submission["level"],
            "page_signal": submission["page_signal"],
            "server_verified": False,
        }

    def update_state(self):
        return reconciled_update_state(self.queue.root)

    def update_in_progress(self) -> bool:
        return self.update_state().get("status") in {
            "checking",
            "updating",
            "restarting",
        }

    def mutation_fenced(self) -> bool:
        return self.update_state().get("status") in {
            "checking",
            "updating",
            "restarting",
            "restart_required",
        }

    def run_mutation(self, callback):
        with self._mutation_lock:
            if self.mutation_fenced():
                raise RuntimeError("update in progress")
            return callback()

    def begin_update(self, port: int):
        with self._mutation_lock:
            with self._command_lock:
                if self.update_in_progress():
                    return {
                        "ok": False,
                        "status": "denied",
                        "reason": "update_in_progress",
                    }
                safe, reason = safe_to_update(self)
                if not safe:
                    return {
                        "ok": False,
                        "status": "denied",
                        "reason": reason,
                    }
                return spawn_update(
                    repo_root=Path(__file__).resolve().parents[2],
                    runtime=self.queue.root,
                    port=port,
                )

    def run_local_command(self, command: CommandEnvelope):
        # Serialize admission with the updater's final safety check and spawn.
        # Model calls hold neither this lock nor a task lease.
        with self._command_lock:
            if self.mutation_fenced():
                raise RuntimeError("update in progress")
            receipt = self.queue.control(**command.model_dump())
            if command.action == "CANCEL":
                self.worker.broker.discard(command.task_id)
                with self.worker.answers_lock:
                    self.worker.answers.pop(command.task_id, None)
            return receipt

    def run_local_fact(self, task_id, field_key, value, revision, *, remember=False):
        with self._command_lock:
            if self.mutation_fenced():
                raise RuntimeError("update in progress")
            updated = self.worker.user_input(task_id, {field_key: value},
                                             expected_revision=revision,
                                             remember=remember)
            return {"status": "accepted", "task": safe_task_view(updated)}

    def dispatch(self, method, path, data):
        parsed = urlsplit(path)
        parts = parsed.path.strip("/").split("/")
        if method == "POST" and parts == ["v1", "commands"]:
            return self._dispatch_unlocked(method, path, data)
        if method == "POST" and parts != ["v1", "ui-ticket"]:
            return self.run_mutation(
                lambda: self._dispatch_unlocked(method, path, data)
            )
        return self._dispatch_unlocked(method, path, data)

    def _dispatch_unlocked(self, method, path, data):
        parsed = urlsplit(path)
        parts = parsed.path.strip("/").split("/")
        if method == "GET" and parts == ["health"]:
            return {"ok": True, "worker_active": self.worker.active, "final_click_actor": "user"}
        if method == "GET" and parts == ["v1", "tasks"]:
            return {"tasks": self.queue.tasks()}
        if method == "GET" and len(parts) == 3 and parts[:2] == ["v1", "commands"]:
            return self.queue.command_receipt(parts[2])
        if method == "POST" and parts == ["v1", "commands"]:
            command = CommandEnvelope.model_validate(data)
            return self.run_local_command(command)
        if method == "GET" and parts == ["v1", "events"]:
            return {"events": self.queue.events(int(parse_qs(parsed.query).get("after", [0])[0]))}
        if method == "GET" and parts == ["v1", "diagnostics"]:
            return self.diagnostics()
        if method == "GET" and parts == ["v1", "update-status"]:
            return reconciled_update_state(self.queue.root)
        if method == "POST" and parts == ["v1", "tasks"]:
            return self.queue.enqueue(TaskSpec.model_validate(data))
        if method == "POST" and parts == ["v1", "chat"] and set(data) == {"message"}:
            return self.manager.handle(data["message"])
        if method == "POST" and parts == ["v1", "ui-ticket"] and not data:
            return {"ticket": self.issue_ui_ticket()}
        if method == "POST" and parts == ["v1", "otp"]:
            if set(data) - {"message", "task_id", "hint", "attempt_id"} or "message" not in data:
                raise ValueError("invalid OTP envelope")
            result = self.worker.broker.push(**data)
            if result["accepted"]:
                task = self.queue.get(result["task_id"])
                if not task["owner"]:
                    self.queue.resume(task["task_id"])
            return result
        if len(parts) >= 3 and parts[:2] == ["v1", "tasks"]:
            tid = parts[2]
            if method == "GET" and len(parts) == 3:
                task = self.queue.get(tid)
                if task["blocker"] == "otp_waiting":
                    attempt = self.worker.broker.attempts.valid_wait(tid)
                    task["auth_attempt_id"] = attempt["attempt_id"] if attempt else None
                return task
            if method == "GET" and len(parts) == 4 and parts[3] == "observe":
                return self.observe_task(tid)
            if method == "POST" and len(parts) == 4:
                if parts[3] == "authorize-otp-resend" and set(data) == {"command_id", "expected_revision"}:
                    attempt = self.worker.broker.attempts.authorize_resend(
                        tid, command_id=data["command_id"],
                        expected_revision=data["expected_revision"])
                    self.worker.broker.discard(tid)
                    return {"task_id": tid, "attempt_id": attempt["attempt_id"],
                            "status": "RESEND_AUTHORIZED_ONCE"}
                if parts[3] == "resume" and not data:
                    return self.queue.resume(tid)
                if parts[3] == "pause" and not data:
                    return self.queue.pause(tid)
                if parts[3] == "cancel" and not data:
                    self.worker.broker.discard(tid)
                    with self.worker.answers_lock:
                        self.worker.answers.pop(tid, None)
                    return self.queue.cancel(tid)
                if parts[3] == "user-input" and set(data) == {"answers"}:
                    return self.worker.user_input(tid, data["answers"])
        raise KeyError("route not found")


def create_server(supervisor, host="127.0.0.1", port=9344):
    if host != "127.0.0.1":
        raise ValueError("supervisor must bind to 127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # HTTP paths, bodies and tokens must not become access logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def _send_json(self, status, result, *, extra_headers=None):
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status, html, *, extra_headers=None):
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
            )
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _cookie_session(self):
            raw = self.headers.get("Cookie", "")
            cookie = SimpleCookie()
            try:
                cookie.load(raw)
            except Exception:
                return ""
            morsel = cookie.get(UI_COOKIE)
            return morsel.value if morsel else ""

        def _read_json(self):
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 65536 or self.headers.get("Transfer-Encoding"):
                raise ValueError("invalid request size")
            if self.command == "POST" and self.headers.get_content_type() != "application/json":
                raise ValueError("JSON required")
            data = json.loads(self.rfile.read(size)) if size else {}
            if not isinstance(data, dict):
                raise ValueError("object required")
            return data

        def handle_request(self):
            parsed = urlsplit(self.path)
            expected_host = f"127.0.0.1:{self.server.server_address[1]}"
            expected_origin = "http://" + expected_host
            if self.headers.get("Host") != expected_host:
                self._send_json(403, {"error": "local_client_required"})
                return

            if self.command == "GET" and parsed.path == "/ui-login":
                ticket = parse_qs(parsed.query).get("ticket", [""])[0]
                session = supervisor.consume_ui_ticket(ticket)
                if not session:
                    self._send_json(401, {"error": "invalid_ui_ticket"})
                    return
                self.send_response(303)
                self.send_header("Location", "/ui")
                self.send_header(
                    "Set-Cookie",
                    f"{UI_COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600",
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                return

            is_ui = parsed.path == "/ui" or parsed.path.startswith("/ui/api/")
            if is_ui:
                if not supervisor.valid_ui_session(self._cookie_session()):
                    if parsed.path == "/ui":
                        self._send_html(401, "<h1>AI 投递经理</h1><p>请从本地 CLI 重新打开面板。</p>")
                    else:
                        self._send_json(401, {"error": "ui_session_required"})
                    return
                origin = self.headers.get("Origin")
                if origin and origin != expected_origin:
                    self._send_json(403, {"error": "local_origin_required"})
                    return
                try:
                    if self.command == "GET" and parsed.path == "/ui":
                        self._send_html(200, DASHBOARD_HTML)
                        return
                    if self.command == "GET" and parsed.path == "/ui/api/state":
                        self._send_json(200, supervisor.ui_state())
                        return
                    if self.command == "GET" and parsed.path == "/ui/api/readiness":
                        self._send_json(200, supervisor.readiness())
                        return
                    if self.command == "GET" and parsed.path == "/ui/api/observe":
                        task_id = parse_qs(parsed.query).get("task_id", [""])[0]
                        self._send_json(200, supervisor.observe_task(task_id))
                        return
                    if self.command == "GET" and parsed.path == "/ui/api/submission-observation":
                        task_id = parse_qs(parsed.query).get("task_id", [""])[0]
                        self._send_json(200, supervisor.observe_submission(task_id))
                        return
                    if self.command == "GET" and parsed.path == "/ui/api/diagnostics":
                        self._send_json(200, supervisor.diagnostics())
                        return
                    if self.command == "GET" and parsed.path == "/ui/api/update-status":
                        self._send_json(200, reconciled_update_state(supervisor.queue.root))
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/update":
                        data = self._read_json()
                        if data:
                            raise ValueError("invalid update envelope")
                        result = supervisor.begin_update(self.server.server_address[1])
                        self._send_json(200 if result.get("ok") else 409, result)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/chat":
                        data = self._read_json()
                        if set(data) != {"message"}:
                            raise ValueError("invalid chat envelope")
                        prepared = supervisor.manager.prepare_chat_discovery(data["message"])
                        result = supervisor.run_mutation(
                            lambda: supervisor.manager.finish_chat_discovery(prepared)
                            if prepared is not None else supervisor.manager.handle(data["message"])
                        )
                        self._send_json(200, result)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/tasks":
                        data = self._read_json()
                        required = {"company", "role"}
                        optional = {"target_url", "location", "campaign", "employment_type", "selected_candidate_id"}
                        if not required <= set(data) or set(data) - required - optional:
                            raise ValueError("invalid local task envelope")
                        request, discovery = supervisor.manager.prepare_local_form(
                            data["company"], data["role"], data.get("target_url", ""),
                            location=data.get("location", ""), campaign=data.get("campaign", ""),
                            employment_type=data.get("employment_type", ""),
                            selected_candidate_id=data.get("selected_candidate_id", ""),
                        )
                        result = supervisor.run_mutation(lambda: supervisor.manager.commit_local_form(
                            request, discovery, selected_candidate_id=data.get("selected_candidate_id", "")))
                        response = {"discovery": result.get("discovery")}
                        if "task_id" in result:
                            response.update(task_id=result["task_id"], revision=result["revision"])
                        self._send_json(200, response)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/human-submission":
                        data = self._read_json()
                        if set(data) != {"task_id", "expected_revision", "user_confirmed"}:
                            raise ValueError("invalid human submission envelope")
                        if (not isinstance(data["task_id"], str)
                                or type(data["expected_revision"]) is not int
                                or data["user_confirmed"] is not True):
                            raise ValueError("explicit human confirmation required")
                        result = supervisor.run_mutation(lambda: supervisor.confirm_human_submission(
                            data["task_id"], data["expected_revision"]))
                        self._send_json(200, result)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/command":
                        command = CommandEnvelope.model_validate(self._read_json())
                        receipt = supervisor.run_local_command(command)
                        self._send_json(200, receipt)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/user-input":
                        data = self._read_json()
                        if set(data) not in (
                            {"task_id", "field_key", "value", "expected_revision"},
                            {"task_id", "field_key", "value", "expected_revision", "remember"},
                        ):
                            raise ValueError("invalid local fact envelope")
                        tid, key = data["task_id"], data["field_key"]
                        if not isinstance(tid, str) or not isinstance(key, str):
                            raise ValueError("invalid local fact target")
                        revision = data["expected_revision"]
                        if type(revision) is not int or revision < 0:
                            raise ValueError("expected revision required")
                        remember = data.get("remember", False)
                        if type(remember) is not bool:
                            raise ValueError("invalid fact scope")
                        result = supervisor.run_local_fact(
                            tid, key, data["value"], revision, remember=remember)
                        self._send_json(200, result)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/otp":
                        data = self._read_json()
                        if set(data) != {"task_id", "attempt_id", "message"}:
                            raise ValueError("invalid local OTP envelope")
                        result = supervisor.dispatch("POST", "/v1/otp", data)
                        self._send_json(200 if result.get("accepted") else 409, result)
                        return
                    if self.command == "POST" and parsed.path == "/ui/api/otp-resend":
                        data = self._read_json()
                        if set(data) != {"task_id", "command_id", "expected_revision"}:
                            raise ValueError("invalid local resend envelope")
                        tid = data.pop("task_id")
                        if not isinstance(tid, str):
                            raise ValueError("invalid local resend target")
                        result = supervisor.dispatch(
                            "POST", f"/v1/tasks/{tid}/authorize-otp-resend", data)
                        self._send_json(200, result)
                        return
                    self._send_json(404, {"error": "not_found"})
                except (ValueError, TypeError):
                    self._send_json(400, {"error": "invalid_request"})
                except RuntimeError:
                    self._send_json(409, {"error": "state_conflict"})
                except Exception:
                    self._send_json(500, {"error": "internal_error"})
                return

            status, result = 200, None
            try:
                if self.headers.get("Origin"):
                    status, result = 403, {"error": "local_client_required"}
                elif not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + supervisor.token):
                    status, result = 401, {"error": "unauthorized"}
                else:
                    result = supervisor.dispatch(self.command, self.path, self._read_json())
            except KeyError:
                status, result = 404, {"error": "not_found"}
            except (ValueError, TypeError):
                status, result = 400, {"error": "invalid_request"}
            except RuntimeError:
                status, result = 409, {"error": "state_conflict"}
            except Exception:
                status, result = 500, {"error": "internal_error"}
            self._send_json(status, result)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
