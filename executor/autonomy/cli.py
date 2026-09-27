from __future__ import annotations

import argparse
import json
import os
import signal
import secrets
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .runtime_paths import RUNTIME, private_dir, local_token
from .process_entry import isolated_cli_command
from .consumer_presentation import ConsumerSurface, loopback_origin, present_surface


def browser_mode():
    from ..browser import browser_mode as observe_mode
    return observe_mode()


def ensure_chrome():
    from ..browser import ensure_chrome as start_owned_chrome
    return start_owned_chrome()


def request(root, port, path, data=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + local_token(root), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def _service_identity_valid(record):
    return (type(record) is dict and set(record) == {"pid", "port", "instance"}
            and type(record["pid"]) is int and 0 < record["pid"] <= 2147483647
            and type(record["port"]) is int and 0 < record["port"] < 65536
            and type(record["instance"]) is str and 32 <= len(record["instance"]) <= 128
            and record["instance"].isascii()
            and all(c.isalnum() or c in "_-" for c in record["instance"]))


def _service_record(path):
    """Read only an owned ordinary bounded registry; never follow a PID hint."""
    path = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("service_identity_unverified")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
            raise ValueError("service_identity_unverified")
        raw = handle.read(65537)
    current = path.stat(follow_symlinks=False)
    if len(raw) > 65536 or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise ValueError("service_identity_unverified")
    record = json.loads(raw)
    if not _service_identity_valid(record):
        raise ValueError("service_identity_unverified")
    return record


def _stop_owned_service(root, port):
    """Ask the authenticated exact service to retire; never signal a registry PID."""
    state = Path(root).expanduser().absolute() / "service.json"
    if type(port) is not int or not 0 < port < 65536:
        return {"ok": False, "reason": "service_identity_unverified"}
    try:
        record = _service_record(state)
        if record["port"] != port:
            raise ValueError("service_identity_unverified")
        observed = request(root, port, "/v1/service-identity")
        if not _service_identity_valid(observed) or observed != record or _service_record(state) != record:
            raise ValueError("service_identity_unverified")
        result = request(root, port, "/v1/service-stop", record)
        if type(result) is not dict or result.get("ok") is not True:
            reason = result.get("reason") if type(result) is dict else None
            return {"ok": False, "reason": reason if type(reason) is str and reason in {
                "service_identity_changed", "update_in_progress", "worker_active",
                "otp_in_flight", "runnable_task_pending"} else "service_stop_refused"}
        for _ in range(100):
            try:
                current = _service_record(state)
            except FileNotFoundError:
                if not state.exists() and not state.is_symlink():
                    return {"ok": True, "running": False}
                raise
            if current != record:
                return {"ok": False, "reason": "service_identity_changed"}
            time.sleep(.1)
        return {"ok": False, "reason": "worker_stopping_at_safe_checkpoint"}
    except FileNotFoundError:
        # No registry is permission to leave it alone, not to kill/adopt a PID.
        return {"ok": False, "reason": "service_record_missing"}
    except (OSError, ValueError, TypeError, UnicodeError, urllib.error.URLError):
        return {"ok": False, "reason": "service_identity_unverified"}


def serve(root, port):
    from ..otp.bridge import OtpBridge
    from .queue import TaskQueue
    from .supervisor import Supervisor, create_server
    from .worker import ProcessLock, Worker

    # Global repo lock also serializes daemons using different queue directories.
    isolated = browser_mode() in {"test", "isolated", "headless"}
    with ProcessLock((Path(root) if isolated else RUNTIME) / "worker.lock"):
        from .state_compatibility import _refuse_live_service
        _refuse_live_service(Path(root))
        queue = TaskQueue(root)
        worker = Worker(queue, relay=None if isolated else OtpBridge())
        supervisor = Supervisor(queue, worker)
        server = create_server(supervisor, port=port)
        state = private_dir(root) / "service.json"
        record = supervisor.service_identity()
        # Atomic private publication never opens an alias/old registry for write.
        temporary = state.with_name(".service-" + secrets.token_hex(16) + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            temporary.replace(state)
        finally:
            temporary.unlink(missing_ok=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: worker.stop_event.set())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            worker.run_forever()
        finally:
            server.shutdown()
            server.server_close()
            try:
                if _service_record(state) == record:
                    state.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError, UnicodeError):
                pass  # A replaced/ambiguous record belongs to no proven owner.


def lifecycle(action, root, port):
    if action in {"health", "status"}:
        try:
            return request(root, port, "/health")
        except (OSError, urllib.error.URLError):
            return {"ok": False, "running": False}
    if action in {"stop", "restart"}:
        stopped = _stop_owned_service(root, port)
        if not stopped.get("ok"):
            # A cold restart may start through the ordinary daemon lock, but
            # a live service without its exact registry remains unowned.
            if (action != "restart" or stopped.get("reason") != "service_record_missing"
                    or lifecycle("health", root, port).get("ok")):
                return stopped
        elif action == "stop":
            return stopped
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
            child = subprocess.Popen(isolated_cli_command("--runtime", str(Path(root).resolve()), "--port", str(port), "serve"),
                cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
        for _ in range(50):
            if child.poll() is not None:
                return {"ok": False, "reason": "service_start_failed"}
            try:
                return request(root, port, "/health")
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        return {"ok": False, "reason": "health_timeout"}


def open_ui(root, port, *, presenter=None):
    loopback_origin(port)  # Validate before issuing any local session capability.
    ticket = request(root, port, "/v1/ui-ticket", {})["ticket"]
    surface = ConsumerSurface.dashboard(port, ticket)
    opened = present_surface(surface, presenter=presenter)
    return {"ok": opened, "opened": opened}


def _consumer_release_identity():
    """Current disk identity for both ordinary launch and recovery retry."""
    from .release import is_packaged_source, read_release_identity, verify_runtime_candidate
    source = Path(__file__).resolve().parents[2]
    identity = read_release_identity(source)
    expected = identity.get("source_sha256") if identity.get("status") == "verified" else ""
    packaged = is_packaged_source(source)
    app_runtime = source.parent / "runtime"
    if packaged and (app_runtime.exists() or app_runtime.is_symlink()) and not verify_runtime_candidate(app_runtime, source):
        expected = ""
    return {"packaged": packaged, "expected": expected}


def _start_consumer_service(root, port):
    """Revalidate recovery admission; an old reason is never launch authority."""
    loopback_origin(port)
    identity = _consumer_release_identity()
    expected = identity["expected"]
    if identity["packaged"] and not expected:
        return {"ok": False, "reason": "release_unverified"}, {"ok": False}
    started = lifecycle("start", root, port)
    health = lifecycle("health", root, port)
    if expected and health.get("ok") and health.get("loaded_source_sha256") != expected:
        restarted = lifecycle("restart", root, port)
        health = lifecycle("health", root, port)
        if not restarted.get("ok") or health.get("loaded_source_sha256") != expected:
            return {"ok": False, "reason": "release_mismatch"}, {"ok": False}
    # Recheck after service startup/safe-point takeover, before minting a UI
    # capability. A changed/broken installed source cannot enter via recovery.
    if _consumer_release_identity() != identity:
        return {"ok": False, "reason": "release_unverified"}, {"ok": False}
    return started, health


def _open_dependency_recovery(root, port, *, presenter=None, reason="business_dependencies_unavailable"):
    from .bootstrap import open_bootstrap

    bootstrap = (open_bootstrap(root, port, reason) if presenter is None else
                 open_bootstrap(root, port, reason, presenter=presenter))
    return {
        "ok": bootstrap.get("ok") is True,
        "opened": bootstrap.get("opened") is True,
        "ready_for_live_e2e": False,
        "checks": {},
        "bootstrap_reason": reason,
        "message": ("应用文件未通过校验；请重新安装可信版本。" if reason == "release_unverified"
                    else "业务运行依赖暂不可用。现有任务未修改；可在恢复页查看诊断。"),
        "final_click_actor": "user",
        "submit_capability": False,
    }


def launch_consumer(root, port, *, presenter=None):
    loopback_origin(port)
    # Check disk integrity before starting an owned browser, then revalidate
    # again through the shared service/recovery admission path.
    identity = _consumer_release_identity()
    if identity["packaged"] and not identity["expected"]:
        return _open_dependency_recovery(root, port, presenter=presenter,
                                         reason="release_unverified")
    try:
        from .consumer import humanize_preflight
        from .preflight import collect_live_preflight
        live_mode = browser_mode() not in {"test", "isolated", "headless"}
    except Exception:
        # No service/browser/ticket action is admitted by a broken dependency.
        return _open_dependency_recovery(root, port, presenter=presenter)
    if live_mode and (not identity["packaged"] or identity["expected"]):
        try:
            ensure_chrome()
        except Exception:
            pass
    started, health = _start_consumer_service(root, port)
    try:
        result = collect_live_preflight(
            supervisor_running=bool(health.get("ok")),
        )
    except Exception:
        return _open_dependency_recovery(root, port, presenter=presenter)
    if not health.get("ok"):
        from .bootstrap import open_bootstrap

        reason = str(started.get("reason") or "service_unavailable")
        bootstrap = (open_bootstrap(root, port, reason) if presenter is None else
                     open_bootstrap(root, port, reason, presenter=presenter))
        return {
            **result,
            "message": humanize_preflight(result),
            "ok": bool(bootstrap.get("ok")),
            "opened": bool(bootstrap.get("opened")),
            "bootstrap_reason": started.get("reason") or "service_unavailable",
        }

    try:
        ui = (open_ui(root, port) if presenter is None else
              open_ui(root, port, presenter=presenter))
    except Exception:
        ui = {"ok": False, "opened": False}
    return {
        **result,
        "ok": bool(ui.get("ok")),
        "opened": bool(ui.get("opened")),
        "message": (
            humanize_preflight(result) if ui.get("opened")
            else "本地服务已启动，但没有成功打开面板。"
        ),
    }



def restore_installed_consumer(root):
    """Explicit cold-app rollback through the existing retained-state transaction.

    Derive the destination from this verified installed source, never a request
    path or Git checkout. No service stop, task restore, window, model or retry.
    """
    boundary = {"final_click_actor": "user", "submit_capability": False}
    if sys.platform != "darwin":
        return {"ok": False, "restored": False, "reason": "macos_required", **boundary}
    try:
        from .consumer import (
            APP_NAME, _trusted_bundle, _native_packaged_launcher,
            _packaged_launcher, rollback_macos_app,
        )
        source = Path(__file__).absolute().parents[2]
        app = source.parent.parent.parent
        executable = app / "Contents" / "MacOS" / "AIApplicationManager"
        if (app.name != APP_NAME + ".app"
                or source != app / "Contents" / "Resources" / "release"
                or any(path.is_symlink() for path in (source, *source.parents))):
            return {"ok": False, "restored": False,
                    "reason": "installed_restore_unverified", **boundary}
        identity = _consumer_release_identity()
        expected = identity["expected"]
        if (identity["packaged"] is not True
                or type(expected) is not str or len(expected) != 64
                or any(c not in "0123456789abcdef" for c in expected)
                or not _trusted_bundle(app)
                or executable.read_text(encoding="utf-8") not in {
                    _packaged_launcher(), _native_packaged_launcher()}):
            return {"ok": False, "restored": False,
                    "reason": "installed_restore_unverified", **boundary}
        # This uses the app/native/worker/migration locks and the original
        # durable WAL/answer-key backup, both release identities, final-path
        # health and compensation. Contention refuses without retiring an owner.
        result = rollback_macos_app(app.parent, task_state_root=root)
        if (type(result) is dict and result.get("ok") is True
                and result.get("restored") is True):
            return {"ok": True, "restored": True, **boundary}
        reason = result.get("reason") if type(result) is dict else None
        reasons = {
            "rollback_unavailable", "update_in_progress", "update_lock_unavailable",
            "task_state_in_use", "task_state_unavailable", "legacy_rollback_unsupported",
            "rollback_identity_unverified", "rollback_startup_isolation_unsupported",
            "rollback_unhealthy", "rollback_state_incompatible", "rollback_identity_changed",
            "rollback_start_failed", "rollback_activation_failed", "manual_recovery_required",
            "rollback_recovery_required", "rollback_post_activation_unhealthy",
        }
        return {"ok": False, "restored": False,
                "reason": reason if type(reason) is str and reason in reasons
                else "restore_unconfirmed", **boundary}
    except Exception:
        # An exception can occur after a move. Do not retry or claim unchanged
        # authority; preserved bundles/capsules remain available for diagnosis.
        return {"ok": False, "restored": False,
                "reason": "restore_unconfirmed", **boundary}


def launch_native_consumer(root, port, *, smoke=False):
    """Run the owned installed app through one verified native presenter."""
    from .consumer import _native_packaged_launcher, _trusted_bundle
    from .macos_host import NativePresenter

    source = Path(__file__).resolve().parents[2]
    app = source.parent.parent.parent
    executable = app / "Contents" / "MacOS" / "AIApplicationManager"
    if (type(smoke) is not bool or sys.platform != "darwin"
            or not _trusted_bundle(app)
            or executable.read_text(encoding="utf-8") != _native_packaged_launcher()):
        return {"ok": False, "opened": False, "reason": "native_bundle_unverified"}
    from .state_compatibility import native_window_guard
    from .native_reopen import create_owned_reopen_server, request_owned_focus

    try:
        # Acquire before service startup or issuing a UI ticket. A duplicate
        # invocation has no authority to reload the existing unsent workbench.
        with native_window_guard(root) as window_fd:
            presenter = NativePresenter(source.parent / "native-host",
                consumer_smoke=smoke, ownership_fd=window_fd)
            reopen = create_owned_reopen_server(root, window_fd, lambda: presenter.focus())
            reopen.start()  # Optional IPC refusal never closes the primary window.
            try:
                result = launch_consumer(root, port, presenter=presenter)
                opened = result.get("opened") is True and result.get("ok") is True
                closed = presenter.wait_for_close() if opened else False
                return {"ok": opened and closed, "opened": opened,
                        "native_window": opened, "native_page": closed if smoke else None,
                        "final_click_actor": "user"}
            finally:
                reopen.close()
                presenter.close()
    except BlockingIOError:
        if request_owned_focus(root):
            return {"ok": True, "opened": True, "native_window": True,
                    "focus_only": True, "final_click_actor": "user"}
        return {"ok": False, "opened": False, "reason": "native_window_already_open"}
    except (OSError, ValueError):
        return {"ok": False, "opened": False, "reason": "native_window_state_invalid"}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="application-autonomy")
    parser.add_argument("--runtime", type=Path, default=RUNTIME)
    parser.add_argument("--port", type=int, default=9344)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "start", "stop", "restart", "status", "health", "tasks", "events", "ui", "launch"):
        commands.add_parser(name)
    native = commands.add_parser("native-launch")
    native_mode = native.add_mutually_exclusive_group()
    native_mode.add_argument("--native-smoke", action="store_true")
    native_mode.add_argument("--restore-previous", action="store_true")
    commands.add_parser("restore-app")
    delivered = commands.add_parser("install-bundle")
    delivered.add_argument("--candidate", type=Path, required=True)
    delivered.add_argument("--destination", type=Path)
    installer = commands.add_parser("install-app")
    installer.add_argument("--standalone-runtime", type=Path)
    installer.add_argument("--native-presentation", action="store_true")
    bootstrap = commands.add_parser("bootstrap-serve")
    bootstrap.add_argument("--reason", default="service_unavailable")
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--start", action="store_true")
    commands.add_parser("chat")
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("--file", type=Path, required=True)
    for name in ("get", "observe", "resume", "pause", "cancel"):
        commands.add_parser(name).add_argument("task_id")
    answers = commands.add_parser("user-input")
    answers.add_argument("task_id")
    otp = commands.add_parser("otp")
    otp_commands = otp.add_subparsers(dest="otp_command", required=True)
    push = otp_commands.add_parser("push")
    push.add_argument("--task")
    push.add_argument("--hint")
    push.add_argument("--attempt", required=True)
    # stdin avoids retaining OTP text in shell history or process arguments.
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            serve(args.runtime, args.port)
            return 0
        if args.command == "bootstrap-serve":
            from .bootstrap import serve_bootstrap

            serve_bootstrap(args.runtime, args.port, args.reason)
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
        elif args.command == "native-launch":
            result = (restore_installed_consumer(args.runtime) if args.restore_previous else
                      launch_native_consumer(args.runtime, args.port, smoke=args.native_smoke))
        elif args.command == "restore-app":
            result = restore_installed_consumer(args.runtime)
        elif args.command == "install-bundle":
            from .consumer import install_macos_bundle

            supplied = sys.argv[1:] if argv is None else argv
            explicit_runtime = any(value == "--runtime" or value.startswith("--runtime=")
                                   for value in supplied)
            result = install_macos_bundle(args.candidate, destination=args.destination,
                task_state_root=args.runtime if explicit_runtime else None)
        elif args.command == "install-app":
            from .consumer import install_macos_app

            options = ({"standalone_runtime": args.standalone_runtime}
                       if args.standalone_runtime is not None else {})
            if args.native_presentation:
                options["native_presentation"] = True
            result = install_macos_app(Path(__file__).resolve().parents[2], **options)
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
            result = request(args.runtime, args.port, "/v1/otp", {"message": sys.stdin.read(4097), "task_id": args.task, "hint": args.hint, "attempt_id": args.attempt})
        elif args.command == "user-input":
            result = request(args.runtime, args.port, "/v1/tasks/" + args.task_id + "/user-input", {"answers": json.load(sys.stdin)})
        else:
            path = "/v1/tasks/" + args.task_id
            result = request(
                args.runtime, args.port,
                path if args.command == "get" else path + "/" + args.command,
                None if args.command in {"get", "observe"} else {},
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except Exception:
        # No exception repr: transport errors can contain payloads or private paths.
        print(json.dumps({"ok": False, "error": "operation_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
