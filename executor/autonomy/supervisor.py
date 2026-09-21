from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .queue import TaskQueue, TaskSpec, private_dir
from .worker import Worker


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
    def __init__(self, queue, worker=None, token=None):
        self.queue, self.worker = queue, worker or Worker(queue)
        self.token = token or local_token(queue.root)
        if len(self.token) < 32:
            raise ValueError("local auth token too short")

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

        def handle_request(self):
            status, result = 200, None
            try:
                expected_host = f"127.0.0.1:{self.server.server_address[1]}"
                if self.headers.get("Host") != expected_host or self.headers.get("Origin"):
                    status, result = 403, {"error": "local_client_required"}
                elif not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + supervisor.token):
                    status, result = 401, {"error": "unauthorized"}
                else:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 0 or size > 65536 or self.headers.get("Transfer-Encoding"):
                        raise ValueError("invalid request size")
                    if self.command == "POST" and self.headers.get_content_type() != "application/json":
                        raise ValueError("JSON required")
                    data = json.loads(self.rfile.read(size)) if size else {}
                    if not isinstance(data, dict):
                        raise ValueError("object required")
                    result = supervisor.dispatch(self.command, self.path, data)
            except KeyError:
                status, result = 404, {"error": "not_found"}
            except (ValueError, TypeError):
                status, result = 400, {"error": "invalid_request"}
            except RuntimeError:
                status, result = 409, {"error": "state_conflict"}
            except Exception:
                status, result = 500, {"error": "internal_error"}
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
