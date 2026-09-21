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

from .dashboard import DASHBOARD_HTML
from .manager import ManagerController
from .queue import TaskQueue, TaskSpec, private_dir
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
        return {
            "ok": True,
            "worker_active": self.worker.active,
            **state,
        }

    def dispatch(self, method, path, data):
        parsed = urlsplit(path)
        parts = parsed.path.strip("/").split("/")
        if method == "GET" and parts == ["health"]:
            return {"ok": True, "worker_active": self.worker.active, "final_click_actor": "user"}
        if method == "GET" and parts == ["v1", "tasks"]:
            return {"tasks": self.queue.tasks()}
        if method == "GET" and parts == ["v1", "events"]:
            return {"events": self.queue.events(int(parse_qs(parsed.query).get("after", [0])[0]))}
        if method == "POST" and parts == ["v1", "tasks"]:
            return self.queue.enqueue(TaskSpec.model_validate(data))
        if method == "POST" and parts == ["v1", "chat"] and set(data) == {"message"}:
            return self.manager.handle(data["message"])
        if method == "POST" and parts == ["v1", "ui-ticket"] and not data:
            return {"ticket": self.issue_ui_ticket()}
        if method == "POST" and parts == ["v1", "otp"]:
            if set(data) - {"message", "task_id", "hint"} or "message" not in data:
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
                return self.queue.get(tid)
            if method == "POST" and len(parts) == 4:
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
                    if self.command == "POST" and parsed.path == "/ui/api/chat":
                        data = self._read_json()
                        if set(data) != {"message"}:
                            raise ValueError("invalid chat envelope")
                        self._send_json(200, supervisor.manager.handle(data["message"]))
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
