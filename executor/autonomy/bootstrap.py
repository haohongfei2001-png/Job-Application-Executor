"""Independent, loopback-only recovery page for an unavailable supervisor."""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .queue import private_dir
from .release import read_release_identity


def _state_path(root: str | Path) -> Path:
    return private_dir(root) / "bootstrap.json"


def _reason(code: str) -> str:
    return {
        "service_start_failed": "本地服务启动失败。现有任务没有被修改。",
        "health_timeout": "本地服务未能通过健康检查。现有任务没有被修改。",
        "worker_stopping_at_safe_checkpoint": "服务仍在等待安全停止点。请稍后重试。",
    }.get(code, "本地服务暂时不可用。现有任务没有被修改。")


def _version(root: str | Path | None = None) -> str:
    source = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    identity = read_release_identity(source)
    if identity.get("status") == "verified":
        return identity["source_sha256"][:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=source,
            capture_output=True, text=True, timeout=3,
        )
        sha = result.stdout.strip()
        return sha[:12] if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _page(reason: str, token: str) -> str:
    safe_reason = html.escape(_reason(reason))
    safe_token = html.escape(token, quote=True)
    version = _version()
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 投递经理 · 恢复</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f6f7f9;color:#111;margin:0}}
main{{max-width:540px;margin:10vh auto;background:white;border:1px solid #e5e7eb;border-radius:16px;padding:28px}}
h1{{font-size:21px}}p{{line-height:1.6}}button{{background:#111;color:white;border:0;border-radius:9px;padding:11px 17px;cursor:pointer}}
</style></head><body><main><h1>AI 投递经理</h1><p>{safe_reason}</p>
<p>可以重试本地服务。重试不会重放结果不明的浏览器写入，也不会提交申请。</p>
<form action="/retry?token={safe_token}" method="post"><button type="submit">重试并打开面板</button></form>
<p><small>本地版本：{version}</small></p>
</main></body></html>"""


def serve_bootstrap(root: str | Path, service_port: int, initial_reason: str = "service_unavailable") -> None:
    from .cli import lifecycle, request

    root = Path(root).expanduser().resolve()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status: int, body: str, *, location: str | None = None):
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            if location:
                self.send_header("Location", location)
            else:
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if not location:
                self.wfile.write(payload)

        def handle_request(self):
            expected_host = f"127.0.0.1:{self.server.server_address[1]}"
            parsed = urllib.parse.urlsplit(self.path)
            supplied = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            if self.headers.get("Host") != expected_host or not secrets.compare_digest(supplied, token):
                self.respond(403, "<h1>无法访问恢复页</h1>")
                return
            if self.command == "GET" and parsed.path == "/":
                self.respond(200, _page(initial_reason, token))
                return
            if self.command != "POST" or parsed.path != "/retry":
                self.respond(404, "<h1>页面不存在</h1>")
                return
            if self.headers.get("Origin") != "http://" + expected_host:
                self.respond(403, "<h1>无法访问恢复页</h1>")
                return
            if self.headers.get("Content-Length") not in {None, "0"}:
                self.respond(400, "<h1>请求无效</h1>")
                return
            started = lifecycle("start", root, service_port)
            health = lifecycle("health", root, service_port)
            if started.get("ok") and health.get("ok"):
                try:
                    ticket = request(root, service_port, "/v1/ui-ticket", {})["ticket"]
                    target = f"http://127.0.0.1:{service_port}/ui-login?ticket={urllib.parse.quote(ticket)}"
                    self.respond(303, "", location=target)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                except (KeyError, OSError, urllib.error.URLError):
                    pass
            self.respond(503, _page(str(started.get("reason") or "service_unavailable"), token))

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state = _state_path(root)
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "port": server.server_address[1], "token": token}))
    tmp.chmod(0o600)
    tmp.replace(state)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            if json.loads(state.read_text()).get("pid") == os.getpid():
                state.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def open_bootstrap(root: str | Path, service_port: int, reason: str = "service_unavailable") -> dict:
    root = Path(root).expanduser().resolve()
    state = _state_path(root)

    def current() -> dict | None:
        try:
            if state.is_symlink() or state.stat().st_mode & 0o077:
                return None
            record = json.loads(state.read_text())
            pid, port, token = int(record["pid"]), int(record["port"]), str(record["token"])
            if not (0 < port < 65536 and len(token) >= 32):
                return None
            command = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=3,
            ).stdout
            if "executor.autonomy.cli" not in command or "bootstrap-serve" not in command or str(root) not in command:
                return None
            url = f"http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}"
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status != 200:
                    return None
            return {"pid": pid, "url": url}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError):
            return None

    active = current()
    if active is None:
        state.unlink(missing_ok=True)
        log = root / "bootstrap.log"
        with log.open("ab") as stream:
            log.chmod(0o600)
            child = subprocess.Popen(
                [sys.executable, "-m", "executor.autonomy.cli", "--runtime", str(root),
                 "--port", str(service_port), "bootstrap-serve", "--reason", reason],
                cwd=Path(__file__).resolve().parents[2],
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                start_new_session=True,
            )
        for _ in range(300):
            if child.poll() is not None:
                break
            active = current()
            if active and active["pid"] == child.pid:
                break
            time.sleep(0.1)
    if not active:
        return {"ok": False, "opened": False, "reason": "bootstrap_start_failed"}
    opened = webbrowser.open(active["url"], new=2)
    return {"ok": bool(opened), "opened": bool(opened)}
