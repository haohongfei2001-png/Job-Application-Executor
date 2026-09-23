from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from ..otp.bridge import OtpBridge
from ..browser import browser_mode, ensure_chrome
from .queue import RUNTIME, TaskQueue, private_dir
from .supervisor import Supervisor, create_server, local_token
from .worker import ProcessLock, Worker


def request(root, port, path, data=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + local_token(root), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def serve(root, port):
    # Global repo lock also serializes daemons using different queue directories.
    isolated = browser_mode() in {"test", "isolated", "headless"}
    with ProcessLock((Path(root) if isolated else RUNTIME) / "worker.lock"):
        queue = TaskQueue(root)
        worker = Worker(queue, relay=None if isolated else OtpBridge())
        server = create_server(Supervisor(queue, worker), port=port)
        state = private_dir(root) / "service.json"
        state.write_text(json.dumps({"pid": os.getpid(), "port": server.server_address[1]}))
        state.chmod(0o600)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: worker.stop_event.set())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            worker.run_forever()
        finally:
            server.shutdown()
            server.server_close()
            state.unlink(missing_ok=True)


def lifecycle(action, root, port):
    state = Path(root) / "service.json"
    if action in {"health", "status"}:
        try:
            return request(root, port, "/health")
        except (OSError, urllib.error.URLError):
            return {"ok": False, "running": False}
    if action in {"stop", "restart"}:
        if state.exists():
            info = json.loads(state.read_text())
            pid = int(info["pid"])
            # Refuse stale PID reuse: exact module and runtime must be present.
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
            if "executor.autonomy.cli" in command and "serve" in command and str(Path(root).resolve()) in command:
                os.kill(pid, signal.SIGTERM)
                for _ in range(100):
                    if not state.exists():
                        break
                    time.sleep(.1)
                if state.exists():
                    return {"ok": False, "reason": "worker_stopping_at_safe_checkpoint"}
            else:
                state.unlink(missing_ok=True)
        if action == "stop":
            return {"ok": True, "running": False}
    if action in {"start", "restart"}:
        try:
            return request(root, port, "/health")
        except (OSError, urllib.error.URLError):
            pass
        private_dir(root)
        local_token(root)
        log = Path(root) / "service.log"
        with log.open("ab") as stream:
            log.chmod(0o600)
            child = subprocess.Popen([sys.executable, "-m", "executor.autonomy.cli", "--runtime", str(Path(root).resolve()), "--port", str(port), "serve"],
                cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
        for _ in range(50):
            if child.poll() is not None:
                return {"ok": False, "reason": "service_start_failed"}
            try:
                return request(root, port, "/health")
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        return {"ok": False, "reason": "health_timeout"}


def open_ui(root, port):
    ticket = request(root, port, "/v1/ui-ticket", {})["ticket"]
    opened = webbrowser.open(
        f"http://127.0.0.1:{port}/ui-login?ticket={ticket}",
        new=2,
    )
    return {"ok": bool(opened), "opened": bool(opened)}


def launch_consumer(root, port):
    from .consumer import humanize_preflight
    from .preflight import collect_live_preflight

    if browser_mode() in {"test", "isolated", "headless"}:
        result = collect_live_preflight(
            supervisor_running=False,
            browser_mode_value=browser_mode(),
            chrome_exists=False,
            cdp_alive=False,
            deepseek_available=False,
            profile_exists=False,
            profile_loadable=False,
        )
        return {
            **result,
            "message": humanize_preflight(result),
            "opened": False,
        }

    try:
        ensure_chrome()
    except Exception:
        result = {
            "ok": False,
            "ready_for_live_e2e": False,
            "checks": {"existing_cdp_session": False},
            "remediation": ["start_dedicated_chrome_cdp"],
            "final_click_actor": "user",
            "submit_capability": False,
        }
        return {
            **result,
            "message": humanize_preflight(result),
            "opened": False,
        }

    started = lifecycle("start", root, port)
    health = lifecycle("health", root, port)
    result = collect_live_preflight(
        supervisor_running=bool(started.get("ok") and health.get("ok")),
    )
    if not result.get("ready_for_live_e2e"):
        return {
            **result,
            "message": humanize_preflight(result),
            "opened": False,
        }

    ui = open_ui(root, port)
    return {
        **result,
        "ok": bool(ui.get("ok")),
        "opened": bool(ui.get("opened")),
        "message": "已就绪" if ui.get("opened") else "本地服务已就绪，但没有成功打开面板。",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="application-autonomy")
    parser.add_argument("--runtime", type=Path, default=RUNTIME)
    parser.add_argument("--port", type=int, default=9344)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "start", "stop", "restart", "status", "health", "tasks", "events", "ui", "launch", "install-app"):
        commands.add_parser(name)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--start", action="store_true")
    commands.add_parser("chat")
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--file", type=Path, required=True)
    for name in ("get", "resume", "pause", "cancel"):
        commands.add_parser(name).add_argument("task_id")
    answers = commands.add_parser("user-input")
    answers.add_argument("task_id")
    otp = commands.add_parser("otp")
    otp_commands = otp.add_subparsers(dest="otp_command", required=True)
    push = otp_commands.add_parser("push")
    push.add_argument("--task")
    push.add_argument("--hint")
    # stdin avoids retaining OTP text in shell history or process arguments.
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            serve(args.runtime, args.port)
            return 0
        if args.command in {"start", "stop", "restart", "status", "health"}:
            result = lifecycle(args.command, args.runtime, args.port)
        elif args.command == "preflight":
            from .preflight import collect_live_preflight

            if args.start:
                lifecycle("start", args.runtime, args.port)
            health = lifecycle("health", args.runtime, args.port)
            result = collect_live_preflight(
                supervisor_running=bool(health.get("ok")),
            )
        elif args.command == "launch":
            result = launch_consumer(args.runtime, args.port)
        elif args.command == "install-app":
            from .consumer import install_macos_app

            result = install_macos_app(Path(__file__).resolve().parents[2])
        elif args.command == "enqueue":
            result = request(args.runtime, args.port, "/v1/tasks", json.loads(args.file.read_text()))
        elif args.command in {"tasks", "events"}:
            result = request(args.runtime, args.port, "/v1/" + args.command)
        elif args.command == "chat":
            message = sys.stdin.read(4001)
            result = request(args.runtime, args.port, "/v1/chat", {"message": message})
        elif args.command == "ui":
            result = open_ui(args.runtime, args.port)
        elif args.command == "otp":
            result = request(args.runtime, args.port, "/v1/otp", {"message": sys.stdin.read(4097), "task_id": args.task, "hint": args.hint})
        elif args.command == "user-input":
            result = request(args.runtime, args.port, "/v1/tasks/" + args.task_id + "/user-input", {"answers": json.load(sys.stdin)})
        else:
            path = "/v1/tasks/" + args.task_id
            result = request(args.runtime, args.port, path if args.command == "get" else path + "/" + args.command, None if args.command == "get" else {})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except Exception:
        # No exception repr: transport errors can contain payloads or private paths.
        print(json.dumps({"ok": False, "error": "operation_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
