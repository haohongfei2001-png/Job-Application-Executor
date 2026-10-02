#!/usr/bin/env python3
"""Fresh-home synthetic service endurance; partial Z-04 evidence, never certification.

Run with the project interpreter and -I. This script uses only paused synthetic
local tasks and loopback health/retirement. It never opens a browser or application.
A stopped/failed run cannot be resumed or combined to claim continuous elapsed time.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
STOP_REQUESTED = False


class SoakFailure(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise SoakFailure(reason)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def fresh_workspace(path):
    path = Path(path).expanduser().absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), "workspace_alias")
    require(path.parent.is_dir(), "workspace_parent_missing")
    path.mkdir(mode=0o700, exist_ok=False)
    require(path.resolve() == path, "workspace_alias")
    (path / "home").mkdir(mode=0o700)
    return path


def write_report(root, report):
    # The directory was exclusively created by this invocation. Never replace an
    # existing arbitrary output; keep all checkpoints until the run is inspected.
    require(report["checkpoint"] < 50000, "checkpoint_budget")
    prior_bytes = report.get("evidence_bytes", 0)
    # Include this checkpoint's own cumulative-byte field in its exact count.
    for _ in range(5):
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        total = prior_bytes + len(encoded.encode())
        if report.get("evidence_bytes") == total:
            break
        report["evidence_bytes"] = total
    else:
        raise SoakFailure("report_byte_accounting")
    require(report["evidence_bytes"] <= 128 * 1024 * 1024, "evidence_budget")
    path = root / f"checkpoint-{report['checkpoint']:06d}.json"
    temporary = root / ("." + path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path, follow_symlinks=False)
    temporary.unlink()
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    report["checkpoint"] += 1


def snapshot(database, expected):
    with sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True) as db:
        require(db.execute("PRAGMA integrity_check").fetchone() == ("ok",), "journal_integrity")
        tasks = db.execute("SELECT task_id,spec,stage,run_state,revision,owner,lease_until,attempts FROM tasks ORDER BY task_id").fetchall()
        require(len(tasks) == len(expected) and {r[0] for r in tasks} == set(expected), "task_identity_changed")
        require(all(r[2] == "BLOCKED" and r[3] == "PAUSED" and r[4] == 2
                    and r[5] is None and r[6] is None and r[7] == 0 for r in tasks), "task_not_paused")
        for row in tasks:
            spec = json.loads(row[1])
            require((spec["target_url"], spec["profile_ref"]) == expected[row[0]][:2], "task_binding_changed")
        require(all(json.loads(r[1])["live_authorized"] is False for r in tasks), "task_live_authorized")
        require(db.execute("SELECT COUNT(*) FROM events WHERE kind IN ('submitted','verified')").fetchone() == (0,), "submit_event")
        for table in ("run_attempts", "field_actions", "browser_bindings", "preparation_approvals",
                      "preparation_final_requests", "preparation_resume_uploads", "preparation_resume_stages"):
            require(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,), "unexpected_effect_record")
        commands = db.execute("SELECT command_id,task_id,action,receipt FROM commands ORDER BY command_id").fetchall()
        require(len(commands) == len(expected), "command_count_changed")
        for command_id, task_id, action, receipt in commands:
            require(task_id in expected and command_id == expected[task_id][2] and action == "PAUSE", "command_binding_changed")
            require(json.loads(receipt) == {"command_id": command_id, "task_id": task_id,
                    "action": "PAUSE", "status": "accepted", "revision": 2, "stage": "BLOCKED"}, "receipt_changed")
        events = db.execute("SELECT task_id,kind,stage FROM events ORDER BY seq").fetchall()
    return hashlib.sha256(json.dumps([tasks, commands, events], sort_keys=True).encode()).hexdigest()


def resource_sample(child):
    # Current Linux observations only, not lifetime high-water marks or a leak
    # certificate. Other platforms explicitly retain unavailable measurements.
    result = {"rss_bytes": None, "open_fds": None, "measurement": "unavailable"}
    if sys.platform == "linux" and child.poll() is None:
        try:
            proc = Path(f"/proc/{child.pid}")
            result.update(rss_bytes=int((proc / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE"),
                          open_fds=len(list((proc / "fd").iterdir())), measurement="linux_proc_current")
            if child.poll() is not None:
                return {"rss_bytes": None, "open_fds": None, "measurement": "child_exited"}
        except (OSError, ValueError, IndexError):
            pass
    return result


def run(root, duration, hold, count):
    global STOP_REQUESTED
    # No executor imports occurred before fresh HOME. Strip inherited credentials,
    # provider configuration, local tokens and proxy variables from both processes.
    os.environ.clear()
    os.environ.update(HOME=str(root / "home"), PATH=os.defpath,
                      APPLICATION_EXECUTOR_BROWSER_MODE="isolated", LANG="C.UTF-8", NO_PROXY="127.0.0.1,localhost")
    sys.path.insert(0, str(SOURCE))
    from executor.autonomy import cli
    from executor.autonomy.queue import TaskQueue, TaskSpec
    from executor.autonomy.release import source_manifest, copy_source_candidate, verify_source_candidate
    from executor.autonomy.worker import ProcessLock

    source = root / "source"
    manifest = copy_source_candidate(SOURCE, source)
    start = time.monotonic()
    report = {"format": "jae-lifecycle-soak-v1", "status": "RUNNING", "started_at": utc_now(),
              "host": {"os": sys.platform, "architecture": platform.machine()},
              "interpreter": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                              "binary_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                              "runner_isolated": bool(sys.flags.isolated)},
              "requested_seconds": duration, "observed_seconds": 0, "completed_cycles": 0,
              "checkpoint": 0, "tasks": count, "certification": "NOT_CERTIFIED",
              "scope": "paused synthetic queue and owned isolated service lifecycle only",
              "missing_scope": ["browser/task execution", "updates/rollback", "crash recovery", "real Mac", "live sites", "leak trend acceptance"],
              "full_z04_pass": False, "continuous_24h_observed": False,
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_sha256": manifest["source_sha256"],
              "requirements_sha256": next(item["sha256"] for item in manifest["files"] if item["path"] == "requirements.txt"),
              "service_running_seconds": 0, "health_observations": 0,
              "rss_limit_bytes": 512 * 1024 * 1024, "fd_limit": 128, "log_bytes": 0, "log_limit_bytes": 16 * 1024 * 1024,
              "samples": [], "failure_reason": None, "cleanup": "not_needed"}
    runtime = root / "runtime"
    child = None
    identity = None
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SOURCE, capture_output=True, text=True, timeout=5)
        report["source_git_head"] = git.stdout.strip() if git.returncode == 0 else None
        report["checkout_dirty"] = subprocess.run(["git", "status", "--porcelain"], cwd=SOURCE, capture_output=True, text=True, timeout=5).stdout.strip() != ""
        queue = TaskQueue(runtime)
        expected = {}
        for index in range(count):
            item = queue.enqueue(TaskSpec(company="Synthetic soak", role="Synthetic role",
                target_url=f"https://jobs.example.test/apply?postId=soak-{index}",
                profile_ref=str(root / "synthetic-profile-unused.json"), live_authorized=False))
            require(item["revision"] == 1 and item["stage"] == "DISCOVERED", "initial_task_unverified")
            receipt = queue.control("PAUSE", item["task_id"], command_id=f"soak-pause-{index}", expected_revision=1)
            require(receipt == {"command_id": f"soak-pause-{index}", "task_id": item["task_id"],
                               "action": "PAUSE", "status": "accepted", "revision": 2, "stage": "BLOCKED"}, "initial_receipt_unverified")
            expected[item["task_id"]] = (f"https://jobs.example.test/apply?postId=soak-{index}",
                                          str(root / "synthetic-profile-unused.json"), f"soak-pause-{index}")
        baseline = snapshot(queue.path, expected)
        report["journal_snapshot_sha256"] = baseline
        write_report(root, report)
        while time.monotonic() - start < duration and not STOP_REQUESTED:
            require(verify_source_candidate(source) and source_manifest(source)["source_sha256"] == report["source_sha256"], "source_changed")
            require(snapshot(queue.path, expected) == baseline, "journal_changed_before_start")
            identity = None
            log = root / f"service-{report['completed_cycles']:06d}.log"
            with log.open("xb") as stream:
                os.chmod(log, 0o600)
                child = subprocess.Popen(cli.isolated_cli_command("--runtime", str(runtime), "--port", "0", "serve", source=source),
                    cwd=source, env=dict(os.environ), stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                    start_new_session=True)
            report["cleanup"] = "owned_child_running"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                require(child.poll() is None, "service_exited_before_ready")
                try:
                    current = cli._service_record(runtime / "service.json")
                    require(current["pid"] == child.pid, "service_child_identity_mismatch")
                    observed = cli.request(runtime, current["port"], "/v1/service-identity", timeout=1)
                    if observed == current:
                        identity = current
                        break
                except (FileNotFoundError, OSError, ValueError):
                    pass
                time.sleep(.05)
            require(identity is not None, "service_start_timeout")
            observed_start = time.monotonic()
            hold_until = min(start + duration, observed_start + hold)
            while time.monotonic() < hold_until and not STOP_REQUESTED:
                require(child.poll() is None, "service_exited_while_observed")
                require(cli.request(runtime, identity["port"], "/v1/service-identity", timeout=1) == identity, "service_identity_changed")
                health = cli.request(runtime, identity["port"], "/health", timeout=1)
                require(health.get("ok") is True and health.get("final_click_actor") == "user" and health.get("worker_active") is None, "health_unverified")
                require(health.get("loaded_source_sha256") == report["source_sha256"], "loaded_source_changed")
                require(snapshot(queue.path, expected) == baseline, "journal_changed_while_running")
                sample = resource_sample(child)
                if sample["rss_bytes"] is not None:
                    require(sample["rss_bytes"] <= report["rss_limit_bytes"], "rss_limit_exceeded")
                if sample["open_fds"] is not None:
                    require(sample["open_fds"] <= report["fd_limit"], "fd_limit_exceeded")
                require(log.stat().st_size <= 1024 * 1024 and report["log_bytes"] + log.stat().st_size <= report["log_limit_bytes"], "service_log_budget")
                report["health_observations"] += 1
                sample["observed_seconds"] = round(time.monotonic() - start, 3)
                report["samples"] = [sample]  # Each durable checkpoint retains its own sample.
                report["observed_seconds"] = sample["observed_seconds"]
                write_report(root, report)
                sample_deadline = min(hold_until, time.monotonic() + 5)
                while time.monotonic() < sample_deadline and not STOP_REQUESTED:
                    time.sleep(min(.1, sample_deadline - time.monotonic()))
            report["service_running_seconds"] += round(time.monotonic() - observed_start, 3)
            require(cli.lifecycle("stop", runtime, identity["port"]) == {"ok": True, "running": False}, "authenticated_stop_unconfirmed")
            require(child.wait(timeout=10) == 0, "owned_child_exit_failed")
            with ProcessLock(runtime / "worker.lock"):
                pass
            require(not (runtime / "service.json").exists(), "registry_retained_after_exit")
            queue = TaskQueue(runtime)
            require(snapshot(queue.path, expected) == baseline, "journal_changed_after_restart")
            report["log_bytes"] += log.stat().st_size
            report["completed_cycles"] += 1
            report["cleanup"] = "child_exited_and_lock_released"
            child = None
        if not STOP_REQUESTED:
            require(report["completed_cycles"] > 0 and report["health_observations"] > 0, "no_complete_observed_cycle")
        report["status"] = "INTERRUPTED" if STOP_REQUESTED else "COMPLETED_PARTIAL_WINDOW"
    except Exception as exc:
        report["status"] = "FAILED"
        report["failure_reason"] = str(exc) if isinstance(exc, SoakFailure) else type(exc).__name__
    finally:
        # Only this invocation's authenticated child is eligible for a graceful
        # stop. If that fails, terminate only this retained Popen child and
        # retain FAILED/INTERRUPTED; cleanup cannot manufacture a passed cycle.
        if child is not None and child.poll() is None:
            report["cleanup"] = "UNCONFIRMED_OWNED_CHILD"
            if identity is not None and identity["pid"] == child.pid:
                try:
                    stopped = cli.lifecycle("stop", runtime, identity["port"])
                    if stopped == {"ok": True, "running": False} and child.wait(timeout=10) == 0:
                        report["cleanup"] = "child_exited_after_failure"
                except Exception:
                    pass
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                    report["cleanup"] = "forced_owned_child_cleanup"
                except subprocess.TimeoutExpired:
                    report["cleanup"] = "UNCONFIRMED_OWNED_CHILD"
        report["observed_seconds"] = round(time.monotonic() - start, 3)
        report["continuous_24h_observed"] = report["status"] == "COMPLETED_PARTIAL_WINDOW" and duration >= 86400 and report["observed_seconds"] >= 86400
        report["service_running_seconds"] = round(report["service_running_seconds"], 3)
        report["finished_at"] = utc_now()
        write_report(root, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True, help="New directory; existing paths are refused")
    parser.add_argument("--duration-seconds", type=float, default=3600)
    parser.add_argument("--hold-seconds", type=float, default=60)
    parser.add_argument("--tasks", type=int, default=20)
    args = parser.parse_args()
    if not (math.isfinite(args.duration_seconds) and 0 < args.duration_seconds <= 172800
            and math.isfinite(args.hold_seconds) and .1 <= args.hold_seconds <= 3600
            and 2 <= args.tasks <= 100):
        parser.error("invalid finite duration, hold or task count")
    if not sys.flags.isolated:
        parser.error("run the project interpreter with -I")
    global STOP_REQUESTED
    def interrupt(*_):
        global STOP_REQUESTED
        STOP_REQUESTED = True
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    def deadline(*_):
        raise SoakFailure("run_deadline_exceeded")
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, args.duration_seconds + 60)
    try:
        root = fresh_workspace(args.workspace)
        result = run(root, args.duration_seconds, args.hold_seconds, args.tasks)
    except Exception as exc:
        result = {"status": "REFUSED", "reason": type(exc).__name__, "certification": "NOT_CERTIFIED", "full_z04_pass": False}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "COMPLETED_PARTIAL_WINDOW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
