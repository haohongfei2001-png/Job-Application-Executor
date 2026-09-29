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
    restart_replays = 0
    persisted = {}

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
                with pytest.raises(KeyError, match="command not found"):
                    queue.command_receipt(f"{command_id}-stale")
                stale += 1

            if expected_stage == "CANCELLED" and action != "CANCEL":
                with pytest.raises(ValueError, match="immutable"):
                    queue.control(action, task_id, command_id=command_id,
                                  expected_revision=expected_revision)
                with pytest.raises(KeyError, match="command not found"):
                    queue.command_receipt(command_id)
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
                if step == 0 and seed % 20 == 0:
                    # Simulate a committed control whose response was lost before
                    # a service restart. The stale retry must recover its durable
                    # receipt without creating another transition.
                    queue = TaskQueue(root)
                    restart_replays += 1
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

        persisted[task_id] = (expected_stage, expected_revision)
        if seed % 100 == 99:
            queue = TaskQueue(root)
            # Reopening must preserve every earlier task, not only the last task
            # in this batch. A migration or replay can otherwise silently lose
            # older durable control state while the newest task still looks safe.
            assert len(persisted) == seed + 1
            for previous_id, expected in persisted.items():
                reopened = queue.get(previous_id)
                assert (reopened["stage"], reopened["revision"]) == expected
                assert reopened["stage"] not in {
                    "READY_TO_SUBMIT", "SUBMITTED", "VERIFIED"}

    assert accepted + refused == 6000
    assert replayed == accepted
    assert stale > 0
    assert restart_replays == 50
    with sqlite3.connect(queue.path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone() == (1000,)
        assert db.execute(
            "SELECT COUNT(*) FROM tasks WHERE stage IN ('READY_TO_SUBMIT','SUBMITTED','VERIFIED')"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT COUNT(*) FROM events WHERE kind IN ('submitted','verified')"
        ).fetchone() == (0,)

def test_interleaved_controls_preserve_one_hundred_task_receipts_across_restarts(tmp_path):
    """Cross-task command replay must not change another queued application."""
    root = tmp_path / "interleaved-runtime"
    queue = TaskQueue(root)
    tasks = {}
    for seed in range(100):
        row = queue.enqueue(TaskSpec(
            company="Synthetic", role="Engineer",
            target_url=f"https://jobs.example.test/apply?postId=interleaved-{seed:04d}",
            profile_ref=str(tmp_path / "synthetic-profile.json"),
        ))
        tasks[row["task_id"]] = (seed, row["revision"])
    assert len(tasks) == 100

    rng = random.Random(0x4A43523039)
    order = list(tasks)
    rng.shuffle(order)
    receipts = {}
    for offset, task_id in enumerate(order):
        seed, initial_revision = tasks[task_id]
        command_id = f"jcr09-interleave-pause-{seed:04d}"
        receipt = queue.control("PAUSE", task_id, command_id=command_id,
                                expected_revision=initial_revision)
        assert receipt == {
            "command_id": command_id, "task_id": task_id, "action": "PAUSE",
            "status": "accepted", "revision": initial_revision + 1,
            "stage": "BLOCKED",
        }
        receipts[task_id] = receipt
        if offset % 17 == 16:
            queue = TaskQueue(root)

    rng.shuffle(order)
    final = {}
    for offset, task_id in enumerate(order):
        seed, initial_revision = tasks[task_id]
        action = "RESUME" if seed % 2 else "CANCEL"
        stage = "DISCOVERED" if action == "RESUME" else "CANCELLED"
        command_id = f"jcr09-interleave-final-{seed:04d}"
        receipt = queue.control(action, task_id, command_id=command_id,
                                expected_revision=initial_revision + 1)
        assert receipt == {
            "command_id": command_id, "task_id": task_id, "action": action,
            "status": "accepted", "revision": initial_revision + 2,
            "stage": stage,
        }
        final[task_id] = receipt
        if offset % 19 == 18:
            queue = TaskQueue(root)

    queue = TaskQueue(root)
    first, second = order[:2]
    first_seed, first_revision = tasks[first]
    old_command = f"jcr09-interleave-pause-{first_seed:04d}"
    assert queue.control("PAUSE", first, command_id=old_command,
                         expected_revision=first_revision) == receipts[first]
    for wrong_action, wrong_task in (("CANCEL", first), ("PAUSE", second)):
        with pytest.raises(ValueError, match="command ID reused"):
            queue.control(wrong_action, wrong_task, command_id=old_command,
                          expected_revision=first_revision)
    with pytest.raises(RuntimeError, match="stale"):
        queue.control("PAUSE", first, command_id="jcr09-interleave-stale",
                      expected_revision=first_revision)
    with pytest.raises(KeyError, match="command not found"):
        queue.command_receipt("jcr09-interleave-stale")

    for task_id, (seed, initial_revision) in tasks.items():
        current = queue.get(task_id)
        expected = final[task_id]
        assert (current["stage"], current["revision"]) == (
            expected["stage"], initial_revision + 2)
        assert current["spec"]["target_url"].endswith(
            f"postId=interleaved-{seed:04d}")
        assert queue.command_receipt(receipts[task_id]["command_id"]) == receipts[task_id]
        assert queue.command_receipt(expected["command_id"]) == expected
        assert current["stage"] not in {
            "READY_TO_SUBMIT", "SUBMITTED", "VERIFIED"}
    with sqlite3.connect(queue.path) as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone() == (100,)
        assert db.execute("SELECT COUNT(*) FROM commands").fetchone() == (200,)
        assert db.execute(
            "SELECT COUNT(*) FROM events WHERE kind IN ('submitted','verified')"
        ).fetchone() == (0,)
