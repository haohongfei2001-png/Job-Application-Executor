"""Run complete-file owners on one host without sharing mutable fixture state.

GitHub owns concurrency and failure propagation (background steps + explicit wait).
This wrapper never selects tests, changes receipts, virtualizes networking, or
relaxes the original pytest command. Audit artifacts contain only synthetic CI
resource identities; no environment dump, profile content or process arguments.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

if __package__:
    from .ci_full_suite import SHARDS, aggregate, decode, source_identity
else:
    from ci_full_suite import SHARDS, aggregate, decode, source_identity

PYTEST_COMMAND = ("-m", "pytest", "-v", "-p", "scripts.ci_full_suite")
PYTHON_VERSION = (3, 12, 14)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def owner_paths(cohort, owner):
    if owner not in SHARDS:
        raise ValueError("unknown full-suite owner")
    base = cohort / owner
    return {"source": base / "source", "home": base / "home", "tmp": base / "tmp",
            "xdg": base / "xdg", "receipt": cohort / "audit" / (owner + ".receipt.json")}


def owner_environment(cohort, owner, inherited):
    paths = owner_paths(cohort, owner)
    env = dict(inherited)
    env.update(HOME=str(paths["home"]), TMPDIR=str(paths["tmp"]),
               TMP=str(paths["tmp"]), TEMP=str(paths["tmp"]),
               XDG_CACHE_HOME=str(paths["xdg"] / "cache"),
               XDG_CONFIG_HOME=str(paths["xdg"] / "config"),
               XDG_DATA_HOME=str(paths["xdg"] / "data"),
               XDG_STATE_HOME=str(paths["xdg"] / "state"),
               XDG_RUNTIME_DIR=str(paths["xdg"] / "run"),
               APPLICATION_EXECUTOR_BROWSER_MODE="isolated", JAE_FULL_SUITE_SHARD=owner,
               JAE_FULL_SUITE_RECEIPT=str(paths["receipt"]))
    # Linux owners use headless Chromium/installed Chrome. Existing headed Mac
    # eligibility remains untouched; no owner may inherit a shared X display.
    env.pop("DISPLAY", None)
    env.pop("XAUTHORITY", None)
    # setup-python's PATH and these absolute, prepared read-only inputs survive
    # the HOME change. Never link one owner's caches/profiles to another's.
    for key in ("PLAYWRIGHT_BROWSERS_PATH", "JAE_STANDALONE_RUNTIME"):
        if not Path(env[key]).is_absolute() or not Path(env[key]).is_dir():
            raise ValueError("missing absolute prepared runtime: " + key)
    return env


def raw_command(*args):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=1)
        return {"command": list(args), "status": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": list(args), "error": type(exc).__name__}


def host_snapshot(cohort, *, commands=True):
    # Numeric process identity/RSS and kernel sockets permit independent capacity
    # and interference review without logging arbitrary argv or private values.
    paths = ("/proc/meminfo", "/proc/loadavg", "/proc/pressure/cpu",
             "/proc/pressure/memory", "/proc/pressure/io",
             "/proc/net/tcp", "/proc/net/tcp6", "/proc/net/udp", "/proc/net/udp6")
    raw = {}
    for name in paths:
        try:
            raw[name] = Path(name).read_text()
        except OSError as exc:
            raw[name] = {"error": type(exc).__name__}
    browser_resources = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            args = (entry / "cmdline").read_bytes().decode("utf-8", errors="replace").split("\0")
            if not args or not any(name in Path(args[0]).name for name in ("chrome", "chromium", "headless_shell")):
                continue
            # Only these nonsecret browser resource flags, never URL/token args.
            flags = [arg for arg in args if arg.startswith(("--user-data-dir=", "--remote-debugging-port=", "--display="))]
            browser_resources.append({"pid": int(entry.name), "executable": args[0], "resource_flags": flags})
        except (OSError, ProcessLookupError):
            continue
    return {"time_ns": time.time_ns(), "image_os": os.environ.get("ImageOS"),
            "image_version": os.environ.get("ImageVersion"), "cpu_count": os.cpu_count(),
            "kernel": raw, "disk": raw_command("df", "-B1", str(cohort)) if commands else None,
            "processes": raw_command("ps", "-eo", "pid,ppid,pgid,stat,rss,comm") if commands else None,
            "sockets": raw_command("ss", "-lntup") if commands else None, "browser_resources": browser_resources}


def prepare(cohort):
    identity = source_identity(Path.cwd())
    if sys.platform != "linux" or sys.version_info[:3] != PYTHON_VERSION:
        raise ValueError("full-suite cohort requires Linux Python 3.12.14")
    cohort.mkdir(parents=True, exist_ok=False)
    (cohort / "audit").mkdir()
    for owner in SHARDS:
        paths = owner_paths(cohort, owner)
        for name in ("home", "tmp", "xdg"):
            paths[name].mkdir(parents=True, mode=0o700)
        for name in ("cache", "config", "data", "state", "run"):
            (paths["xdg"] / name).mkdir(mode=0o700)
        env = owner_environment(cohort, owner, os.environ)
        subprocess.run(["git", "worktree", "add", "--detach", str(paths["source"]), identity["sha"]],
                       check=True, timeout=60)
        if source_identity(paths["source"]) != identity:
            raise ValueError("owner source identity differs")
        write_json(cohort / "audit" / (owner + ".isolation.json"), {
            "source": identity, "paths": {key: str(value) for key, value in paths.items()},
            "python": sys.executable, "python_version": list(sys.version_info[:3]),
            "browser_path": env["PLAYWRIGHT_BROWSERS_PATH"],
            "standalone_runtime": env["JAE_STANDALONE_RUNTIME"],
            "display": env.get("DISPLAY"), "network_namespace": os.readlink("/proc/self/ns/net"),
        })
    write_json(cohort / "audit" / "host-start.json", host_snapshot(cohort))
    write_json(cohort / "audit" / "packages.json", raw_command(sys.executable, "-m", "pip", "freeze", "--all"))


class OwnerLifecycle:
    """Unprivileged Linux subreaper; signal only kernel-proven direct children.

    Detached descendants become our direct children when their parent exits.
    pidfds bind signals to a process, not a reusable numeric PID. No name/env
    matching, runner-wide kill, cgroup, network or sandbox policy is involved.
    """
    def __init__(self, audit):
        self.audit, self.cancelled, self.previous = audit, None, {}
        self.events = []

    def __enter__(self):
        # Install deferred handlers BEFORE Popen: a signal cannot interrupt the
        # gap between a successful spawn and recording its returned handle.
        def cancel(signum, _frame):
            self.cancelled = self.cancelled or signum
        for signum in (signal.SIGTERM, signal.SIGINT):
            self.previous[signum] = signal.signal(signum, cancel)
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            state = ctypes.c_int()
            if (libc.prctl(36, 1, 0, 0, 0) != 0  # PR_SET_CHILD_SUBREAPER
                    or libc.prctl(37, ctypes.byref(state), 0, 0, 0) != 0  # PR_GET_CHILD_SUBREAPER
                    or state.value != 1):
                raise OSError(ctypes.get_errno(), "cannot establish owner subreaper")
            # Refuse unsupported kernels before any test or compile process.
            fd = os.pidfd_open(os.getpid())
            os.close(fd)
            if not hasattr(signal, "pidfd_send_signal"):
                raise RuntimeError("pidfd signalling unavailable")
            self.check_cancelled()
        except BaseException as error:
            write_json(self.audit, {"complete": False, "cancelled_signal": self.cancelled,
                                    "error": type(error).__name__, "events": []})
            for signum, previous in self.previous.items():
                signal.signal(signum, previous)
            raise
        return self

    def check_cancelled(self):
        if self.cancelled is not None:
            raise SystemExit(128 + self.cancelled)

    def run(self, command, *, cwd, env, sample=None):
        self.check_cancelled()
        child = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
        self.check_cancelled()
        next_sample = 0.0
        while True:
            self.check_cancelled()
            code = child.poll()
            if code is not None:
                return code
            if sample is not None and time.monotonic() >= next_sample:
                sample()
                next_sample = time.monotonic() + 10
            time.sleep(0.1)

    def children(self):
        # PPID is kernel ownership across ALL wrapper threads, unlike the
        # optional per-thread /proc/.../children file. Names/env are irrelevant.
        owned = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rpartition(")")[2].split()
            except FileNotFoundError:
                continue
            if int(fields[1]) == os.getpid():
                owned.append(int(entry.name))
        return owned

    def cleanup(self):
        started = time.monotonic()
        sent = set()
        while True:
            # Reap exited leaders AND adopted orphans, including zombie children.
            while True:
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if not pid:
                    break
                self.events.append({"reaped_pid": pid, "wait_status": status})
            children = self.children()
            if not children:
                # /proc enumeration is a snapshot: a child may fork and exit
                # while scanned. Only kernel ECHILD, after adoption/reaping,
                # proves there are no remaining descendants to clean up.
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    return
                if pid:
                    self.events.append({"reaped_pid": pid, "wait_status": status})
            elapsed = time.monotonic() - started
            if elapsed >= 5:
                raise RuntimeError("owned descendants remain after bounded cleanup")
            signum = signal.SIGTERM if elapsed < 2 else signal.SIGKILL
            for pid in children:
                try:
                    fd = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                try:
                    try:
                        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
                    except FileNotFoundError:
                        continue
                    # Open the pidfd BEFORE proving current parent ownership. If
                    # the numeric PID was reused, a handle can only reference the
                    # old exited child or the presently proven direct child.
                    if int(fields[1]) != os.getpid():
                        continue
                    identity = (pid, fields[19], int(signum))
                    if identity not in sent:
                        try:
                            signal.pidfd_send_signal(fd, signum)
                        except ProcessLookupError:
                            continue
                        sent.add(identity)
                        self.events.append({"pid": pid, "start_ticks": fields[19], "signal": int(signum)})
                finally:
                    os.close(fd)
            time.sleep(0.02)

    def __exit__(self, exc_type, exc, traceback):
        complete, failure = False, None
        try:
            self.cleanup()
            complete = True
        except BaseException as error:
            failure = type(error).__name__
            raise
        finally:
            write_json(self.audit, {"complete": complete, "cancelled_signal": self.cancelled,
                                    "error": failure, "events": self.events})
            for signum, previous in self.previous.items():
                signal.signal(signum, previous)
        # A signal arriving during cleanup still cannot publish a success.
        self.check_cancelled()


def run_owner(cohort, owner):
    paths = owner_paths(cohort, owner)
    env = owner_environment(cohort, owner, os.environ)
    expected = source_identity(Path.cwd())
    if source_identity(paths["source"]) != expected:
        raise ValueError("owner source identity differs")
    audit = cohort / "audit"
    started = time.monotonic()
    try:
        with OwnerLifecycle(audit / (owner + ".cleanup.json")) as lifecycle:
            with (audit / (owner + ".host.jsonl")).open("w", encoding="utf-8") as handle:
                def sample():
                    handle.write(json.dumps(host_snapshot(cohort), sort_keys=True) + "\n")
                    handle.flush()
                code = lifecycle.run([sys.executable, *PYTEST_COMMAND],
                                     cwd=paths["source"], env=env, sample=sample)
                # Preserve each successful owner's original compile gate on the
                # actual tested worktree, under the same owned-child lifecycle.
                compile_status = None
                if code == 0:
                    compile_status = lifecycle.run(
                        [sys.executable, "-m", "compileall", "-q", "executor", "tests"],
                        cwd=paths["source"], env=env, sample=sample)
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        write_json(audit / (owner + ".execution.json"), {
            "owner": owner, "exit_status": code, "compile_status": compile_status,
            "elapsed_seconds": time.monotonic() - started,
            "max_rss_kib": usage.ru_maxrss, "user_seconds": usage.ru_utime,
            "system_seconds": usage.ru_stime, "command": [sys.executable, *PYTEST_COMMAND],
            "source": source_identity(paths["source"]),
        })
        result = code if code else compile_status
        return result if result >= 0 else 128 - result
    finally:
        # Cancellation diagnostics must not spawn another unmanaged process.
        write_json(audit / (owner + ".host-end.json"), host_snapshot(cohort, commands=False))


def verify(cohort, results):
    # Native wait already fails on an owner failure. This explicit check also
    # refuses absent/skipped/cancelled owners, preserving all available receipts.
    if results != dict.fromkeys(SHARDS, "success"):
        raise ValueError("failed, skipped, cancelled or missing cohort owner")
    outputs = {"receipt_" + owner: owner_paths(cohort, owner)["receipt"].read_text() for owner in SHARDS}
    summary = aggregate(outputs, source_identity(Path.cwd()), "success")
    write_json(cohort / "audit" / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "owner", "verify"))
    parser.add_argument("owner", nargs="?", choices=SHARDS)
    args = parser.parse_args()
    cohort = Path(os.environ["JAE_FULL_SUITE_COHORT"]).resolve()
    if args.action == "prepare":
        prepare(cohort)
    elif args.action == "owner":
        raise SystemExit(run_owner(cohort, args.owner))
    else:
        verify(cohort, decode(os.environ["JAE_FULL_SUITE_OWNER_RESULTS"]))


if __name__ == "__main__":
    main()
