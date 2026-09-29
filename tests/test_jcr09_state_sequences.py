"""Deterministic JCR09 state-sequence oracle over the real durable task queue.

Each seed owns a distinct synthetic task. The oracle tracks only the externally
visible stage, revision and receipt semantics; it does not call queue internals.
"""
from __future__ import annotations

import random
import sqlite3

import pytest

from executor.autonomy.queue import TaskQueue, TaskSpec


def test_one_thousand_independent_control_sequences_keep_human_submit_boundary(tmp_path):
    root = tmp_path / "synthetic-runtime"
    queue = TaskQueue(root)
    accepted = 0
    refused = 0
    replayed = 0
    stale = 0

    for seed in range(1000):
        original = queue.enqueue(TaskSpec(
            company="Synthetic", role="Engineer",
            target_url=f"https://jobs.example.test/apply?postId=jcr09-{seed:04d}",
            profile_ref=str(tmp_path / "synthetic-profile.json"),
        ))
        task_id = original["task_id"]
        expected_stage = "DISCOVERED"
        expected_revision = original["revision"]
        rng = random.Random(0x4A43523039 + seed)

        for step in range(6):
            action = rng.choice(("PAUSE", "RESUME", "CANCEL"))
            command_id = f"jcr09-sequence-{seed:04d}-{step:02d}"
            if step in (2, 4) and expected_revision > original["revision"]:
                with pytest.raises(RuntimeError, match="stale"):
                    queue.control(action, task_id, command_id=f"{command_id}-stale",
                                  expected_revision=expected_revision - 1)
                unchanged = queue.get(task_id)
                assert (unchanged["stage"], unchanged["revision"]) == (
                    expected_stage, expected_revision)
                assert queue.command_receipt(f"{command_id}-stale") is None
                stale += 1

            if expected_stage == "CANCELLED" and action != "CANCEL":
                with pytest.raises(ValueError, match="immutable"):
                    queue.control(action, task_id, command_id=command_id,
                                  expected_revision=expected_revision)
                assert queue.command_receipt(command_id) is None
                refused += 1
            else:
                receipt = queue.control(action, task_id, command_id=command_id,
                                        expected_revision=expected_revision)
                expected_revision += 1
                expected_stage = ("CANCELLED" if action == "CANCEL" else
                                  "BLOCKED" if action == "PAUSE" else "DISCOVERED")
                assert receipt == {
                    "command_id": command_id, "task_id": task_id, "action": action,
                    "status": "accepted", "revision": expected_revision,
                    "stage": expected_stage,
                }
                assert queue.command_receipt(command_id) == receipt
                assert queue.control(action, task_id, command_id=command_id,
                                     expected_revision=original["revision"]) == receipt
                replayed += 1
                accepted += 1

            observed = queue.get(task_id)
            assert (observed["stage"], observed["revision"]) == (
                expected_stage, expected_revision)
            assert observed["run_state"] == {
                "DISCOVERED": "RUNNABLE",
                "BLOCKED": "PAUSED",
                "CANCELLED": "CANCELLED",
            }[expected_stage]
            assert observed["stage"] not in {"READY_TO_SUBMIT", "SUBMITTED", "VERIFIED"}

        if seed % 100 == 99:
            queue = TaskQueue(root)
            reopened = queue.get(task_id)
            assert (reopened["stage"], reopened["revision"]) == (
                expected_stage, expected_revision)

    assert accepted + refused == 6000
    assert replayed == accepted
    assert stale > 0
    with sqlite3.connect(queue.path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone() == (1000,)
        assert db.execute(
            "SELECT COUNT(*) FROM tasks WHERE stage IN ('READY_TO_SUBMIT','SUBMITTED','VERIFIED')"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT COUNT(*) FROM events WHERE kind IN ('submitted','verified')"
        ).fetchone() == (0,)
