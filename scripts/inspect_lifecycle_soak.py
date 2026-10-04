#!/usr/bin/env python3
"""Read only value-free soak checkpoints; never adopt, resume or stop a runtime.

Reports are self-reported evidence, not an independent execution certificate.
A RUNNING tail without a terminal report is incomplete, however much wall time
has passed. Runtime registries, keys, tokens, logs and databases are never read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

NAME = re.compile(r"checkpoint-([0-9]{6})\.json\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
STATUSES = {"RUNNING", "COMPLETED_PARTIAL_WINDOW", "INTERRUPTED", "FAILED"}


class InvalidEvidence(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise InvalidEvidence(reason)


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def read_checkpoint(directory_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == os.geteuid() and not before.st_mode & 0o077,
                "checkpoint_not_private_owned_regular_file")
        require(0 < before.st_size <= 65536, "checkpoint_size")
        data = stream.read(65537)
    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "checkpoint_changed_during_read")
    require(len(data) == before.st_size, "checkpoint_size_changed")
    return json.loads(data), data, after.st_mtime


def inspect(workspace, *, now=None, stale_after=300):
    result = {"format": "jae-lifecycle-soak-inspection-v1", "classification": "INVALID_EVIDENCE",
              "certification": "NOT_CERTIFIED", "full_z04_pass": False,
              "execution_independently_verified": False,
              "runtime_files_read": False, "runtime_actions_performed": False}
    directory_fd = None
    try:
        require(number(stale_after) and 0 < stale_after <= 86400, "invalid_freshness_bound")
        current_time = time.time() if now is None else now
        require(number(current_time), "invalid_observation_time")
        root = Path(workspace).expanduser().absolute()
        require(not any(p.is_symlink() for p in (root, *root.parents)), "workspace_alias")
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        meta = os.fstat(directory_fd)
        require(meta.st_uid == os.geteuid() and not meta.st_mode & 0o077, "workspace_not_private_owned")
        names = []
        with os.scandir(directory_fd) as entries:
            for scanned, entry in enumerate(entries, 1):
                require(scanned <= 100100, "workspace_entry_budget")
                if NAME.fullmatch(entry.name):
                    names.append(entry.name)
                    require(len(names) <= 50000, "checkpoint_count")
        names.sort()
        require(names, "checkpoint_count")
        total = 0
        identity = None
        previous = None
        max_gap = 0
        last = None
        for index, name in enumerate(names):
            require(int(NAME.fullmatch(name).group(1)) == index, "checkpoint_sequence_gap")
            report, raw, modified = read_checkpoint(directory_fd, name)
            total += len(raw)
            require(total <= 128 * 1024 * 1024, "evidence_budget")
            require(type(report) is dict and report.get("format") == "jae-lifecycle-soak-v1", "report_format")
            require(type(report.get("checkpoint")) is int and report["checkpoint"] == index, "checkpoint_index")
            require(type(report.get("evidence_bytes")) is int and report["evidence_bytes"] == total, "byte_accounting")
            require(report.get("certification") == "NOT_CERTIFIED" and report.get("full_z04_pass") is False, "certification_claim")
            require(report.get("status") in STATUSES, "unknown_status")
            require(number(report.get("observed_seconds")) and number(report.get("requested_seconds"))
                    and 0 < report["requested_seconds"] <= 172800, "invalid_duration")
            expected_24h = (report["status"] == "COMPLETED_PARTIAL_WINDOW"
                            and report["requested_seconds"] >= 86400
                            and report["observed_seconds"] >= 86400)
            require(type(report.get("continuous_24h_observed")) is bool
                    and report["continuous_24h_observed"] == expected_24h, "inconsistent_24h_claim")
            require(all(type(report.get(k)) is int and report[k] >= 0 for k in
                        ("completed_cycles", "health_observations", "tasks")), "invalid_progress")
            require(2 <= report["tasks"] <= 100, "invalid_task_count")
            for key in ("source_sha256", "harness_sha256", "requirements_sha256"):
                require(type(report.get(key)) is str and HASH.fullmatch(report[key]), "invalid_digest")
            head = report.get("source_git_head")
            require(head is None or type(head) is str and SHA.fullmatch(head), "invalid_git_identity")
            require(type(report.get("checkout_dirty")) is bool, "invalid_checkout_state")
            current_identity = tuple(report.get(k) for k in ("source_git_head", "source_sha256", "harness_sha256",
                                     "requirements_sha256", "checkout_dirty", "requested_seconds", "tasks", "started_at"))
            if identity is None:
                identity = current_identity
            else:
                require(identity == current_identity, "identity_changed")
            progress = tuple(report[k] for k in ("observed_seconds", "completed_cycles", "health_observations"))
            if previous is not None:
                require(all(a <= b for a, b in zip(previous, progress)), "progress_regressed")
                max_gap = max(max_gap, progress[0] - previous[0])
            if index < len(names) - 1:
                require(report["status"] == "RUNNING", "data_after_terminal")
            previous = progress
            last = report
            last_digest = hashlib.sha256(raw).hexdigest()
            last_modified = modified
        terminal = last["status"] != "RUNNING"
        age = current_time - last_modified
        result.update(checkpoint_count=len(names), evidence_bytes=total,
                      terminal_report_present=terminal,
                      actual_observation_seconds_reported=last["observed_seconds"],
                      requested_seconds=last["requested_seconds"],
                      completed_cycles_reported=last["completed_cycles"],
                      health_observations_reported=last["health_observations"],
                      maximum_reported_observation_gap_seconds=round(max_gap, 3),
                      last_checkpoint_age_seconds=round(age, 3) if age >= 0 else None,
                      freshness="CLOCK_UNVERIFIED" if age < 0 else "STALE" if age > stale_after else "RECENT",
                      last_checkpoint_sha256=last_digest, source_git_head=last.get("source_git_head"),
                      source_sha256=last["source_sha256"], harness_sha256=last["harness_sha256"],
                      checkout_dirty=last["checkout_dirty"])
        if not terminal:
            result["classification"] = "INCOMPLETE_NO_TERMINAL"
        elif last["status"] in {"FAILED", "INTERRUPTED"}:
            result["classification"] = last["status"] + "_REPORTED"
        elif max_gap > stale_after:
            result["classification"] = "INCOMPLETE_OBSERVATION_GAP"
        else:
            require(last["completed_cycles"] > 0 and last["health_observations"] > 0
                    and last["observed_seconds"] >= last["requested_seconds"]
                    and last.get("cleanup") == "child_exited_and_lock_released", "inconsistent_completion")
            result["classification"] = "COMPLETED_PARTIAL_REPORTED"
        return result
    except Exception as exc:
        # Never echo paths, untrusted report strings or exception bodies.
        result["classification"] = "INVALID_EVIDENCE"
        result["reason"] = str(exc) if isinstance(exc, InvalidEvidence) else type(exc).__name__
        return result
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--stale-after-seconds", type=float, default=300)
    args = parser.parse_args()
    report = inspect(args.workspace, stale_after=args.stale_after_seconds)
    print(json.dumps(report, sort_keys=True))
    if report["classification"] == "COMPLETED_PARTIAL_REPORTED":
        return 0
    return 1 if report["classification"] == "INVALID_EVIDENCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
