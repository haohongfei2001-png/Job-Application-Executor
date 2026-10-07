"""Complete-file allocation, raw identity receipts and real pytest failure probes."""
from collections import Counter
import copy
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from scripts.ci_full_suite import (FORMAT, OUTCOMES, SHARDS, aggregate, decode, digest,
                                  inventory, owner, partition, universe_inventory)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = {"sha": "a" * 40, "tree": "b" * 40, "requirements": "c" * 64}
RECORDS = [("tests/test_consumer_entry_v1.py", "tests/test_consumer_entry_v1.py::test_one"),
           ("tests/test_jcr_probe.py", "tests/test_jcr_probe.py::test_two"),
           ("tests/test_new.py", "tests/test_new.py::test_three[a]"),
           ("tests/test_new.py", "tests/test_new.py::test_three[b]")]


def receipts():
    groups = partition(RECORDS)
    result = {}
    for shard in SHARDS:
        selected = inventory(groups[shard])
        counts = dict.fromkeys(OUTCOMES, 0)
        counts["passed"] = selected["count"]
        result["receipt_" + shard] = {
            "format": FORMAT, "shard": shard, "source": SOURCE,
            "runtime": {"python": "3.12.14", "pytest": "9.1.1", "platform": "linux", "image": "synthetic"},
            "universe": universe_inventory(RECORDS), "plan": {s: inventory(groups[s]) for s in SHARDS},
            "selected": selected, "started": {"count": selected["count"], "digest": selected["digest"]},
            "finished": {"count": selected["count"], "digest": selected["digest"]},
            "exit_status": 0, "outcomes": counts, "subtests": dict.fromkeys(OUTCOMES, 0),
            "teardown": dict.fromkeys(OUTCOMES, 0), "collection": {"all": [], "owned": []},
        }
    return result


def encode(values):
    return {k: json.dumps(v) for k, v in values.items()}


class FullSuiteAllocationTests(unittest.TestCase):
    def test_production_cohort_pin_is_exact_and_has_no_environment_override(self):
        from scripts.ci_full_suite_cohort import PYTHON_VERSION
        self.assertEqual(PYTHON_VERSION, (3, 12, 14))
        source = (ROOT / "scripts/ci_full_suite_cohort.py").read_text()
        self.assertEqual(source.count("PYTHON_VERSION = (3, 12, 14)"), 1)
        self.assertIn("sys.version_info[:3] != PYTHON_VERSION", source)
        self.assertIn("python-version: '3.12.14'", (ROOT / ".github/workflows/application-executor-ci.yml").read_text())

    def test_disjoint_complete_file_union_preserves_order_and_discovers_future_files(self):
        original = RECORDS + [("new_location/test_future.py", "new_location/test_future.py::test_future")]
        groups = partition(original)
        self.assertEqual(Counter(x for group in groups.values() for x in group), Counter(original))
        self.assertEqual(groups["remainder"], original[2:])
        for name in ("test_consumer_recovery_transactions_v1.py", "test_task_state_compatibility_v1.py"):
            self.assertEqual(owner("tests/" + name), "consumer")
        for name in ("test_field_action_journal_v1.py", "test_generic_browser_v1.py", "test_jcr99_future.py"):
            self.assertEqual(owner("tests/" + name), "browser_contract")
        self.assertEqual(owner("other/test_jcr_future.py"), "remainder")

    def test_raw_identity_and_multiplicity_survive_masking_and_reordering(self):
        self.assertNotEqual(digest(["test[one]", "test[two]"]), digest(["test[***]", "test[***]"]))
        self.assertNotEqual(digest(["test", "test"]), digest(["test"]))
        self.assertEqual(digest(["a", "b"]), digest(["b", "a"]))
        self.assertNotEqual(digest(["a\nb"]), digest(["a", "b"]))
        with self.assertRaises(ValueError):
            partition(RECORDS[1:])

    def test_receipts_preserve_skip_xfail_xpass_subtests_and_teardown_diagnostics(self):
        values = receipts()
        for shard, outcome in zip(SHARDS, ("skipped", "xfailed", "xpassed")):
            values["receipt_" + shard]["outcomes"]["passed"] -= 1
            values["receipt_" + shard]["outcomes"][outcome] = 1
            values["receipt_" + shard]["subtests"]["passed"] = 2
            values["receipt_" + shard]["teardown"]["skipped"] = 1
        result = aggregate(encode(values), SOURCE, "success")
        self.assertEqual(result["outcomes"], dict(passed=1, skipped=1, xfailed=1, xpassed=1, failed=0, errors=0))
        self.assertEqual(result["subtests"]["passed"], 6)
        self.assertEqual(result["teardown"]["skipped"], 3)

    def test_missing_extra_failed_skipped_cancelled_owners_fail(self):
        for result in ("failure", "skipped", "cancelled", "pending", ""):
            with self.subTest(result=result), self.assertRaises(ValueError):
                aggregate(encode(receipts()), SOURCE, result)
        for shard in SHARDS:
            values = receipts()
            del values["receipt_" + shard]
            with self.subTest(missing=shard), self.assertRaises(ValueError):
                aggregate(encode(values), SOURCE, "success")
        values = encode(receipts())
        values["unexpected"] = values["receipt_consumer"]
        with self.assertRaises(ValueError):
            aggregate(values, SOURCE, "success")

    def test_image_only_and_python_patch_only_mismatches_are_not_normalized(self):
        for field, value in (("image", "ubuntu24:20261004.327.1"), ("python", "3.12.15")):
            values = receipts()
            for receipt in values.values():
                receipt["runtime"]["image"] = "ubuntu24:20260927.320.1"
            values["receipt_browser_contract"]["runtime"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "different source, runtime or collection"):
                aggregate(encode(values), SOURCE, "success")
            # The actual differing field survives intact; strict equality remains.
            self.assertEqual(values["receipt_browser_contract"]["runtime"][field], value)

    def test_collection_receipt_missing_duplicate_wrong_owner_or_error_cannot_pass(self):
        event = {"path": "tests/test_module_skip.py", "nodeid_sha256": "d" * 64, "outcome": "skipped"}
        values = receipts()
        for shard in SHARDS:
            values["receipt_" + shard]["collection"] = {"all": [event], "owned": [event] if shard == "remainder" else []}
        self.assertEqual(aggregate(encode(values), SOURCE, "success")["collection_outcomes"]["skipped"], 1)
        for fault in ("missing", "duplicate", "wrong_owner", "different_universe", "error"):
            broken = copy.deepcopy(values)
            if fault == "missing": broken["receipt_remainder"]["collection"]["owned"] = []
            if fault == "duplicate": broken["receipt_remainder"]["collection"]["owned"].append(event)
            if fault == "wrong_owner": broken["receipt_consumer"]["collection"]["owned"] = [event]
            if fault == "different_universe": broken["receipt_consumer"]["collection"]["all"] = []
            if fault == "error":
                for receipt in broken.values():
                    for row in receipt["collection"]["all"]: row["outcome"] = "errors"
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                aggregate(encode(broken), SOURCE, "success")

    def test_duplicate_receipt_fields_and_nonmapping_outputs_fail(self):
        for value in ('{"receipt_consumer":"one","receipt_consumer":"two"}', '{"x":{"a":1,"a":1}}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode(value)
        for value in (None, [], ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                aggregate(value, SOURCE, "success")

    def test_source_collection_execution_outcome_and_schema_faults_fail(self):
        mutations = [
            lambda r: r.update(format="other"), lambda r: r.update(shard="consumer"),
            lambda r: r.update(source=dict(SOURCE, sha="d" * 40)),
            lambda r: r["runtime"].update(python="3.13.0"), lambda r: r["runtime"].update(platform="darwin"),
            lambda r: r["universe"].update(digest="d" * 64), lambda r: r["universe"].update(count=5),
            lambda r: r["plan"]["consumer"].update(digest="d" * 64),
            lambda r: r["selected"].update(digest="d" * 64), lambda r: r["selected"].update(count=True),
            lambda r: r["started"].update(count=1),
            lambda r: r["started"].update(count=True), lambda r: r["finished"].update(digest="d" * 64),
            lambda r: r.update(exit_status=1), lambda r: r.update(exit_status=False),
            lambda r: r["outcomes"].update(passed=0), lambda r: r["outcomes"].update(failed=1),
            lambda r: r["subtests"].update(failed=1), lambda r: r["teardown"].update(errors=1),
            lambda r: r["outcomes"].update(passed=True),
            lambda r: r["plan"]["consumer"]["files"].append("tests/test_new.py"),
            lambda r: r["plan"]["consumer"]["files"].append("../escape.py"),
            lambda r: r.update(unexpected="field"),
        ]
        for index, mutate in enumerate(mutations):
            values = copy.deepcopy(receipts())
            mutate(values["receipt_remainder"])
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                aggregate(encode(values), SOURCE, "success")
        # All owners agreeing on an incorrect universe still fails independent
        # root verification; agreeing with one another is insufficient.
        values = receipts()
        for receipt in values.values():
            receipt["universe"]["digest"] = "0" * 64
        with self.assertRaises(ValueError):
            aggregate(encode(values), SOURCE, "success")
        values = receipts()
        for key in ("started", "finished"):
            values["receipt_consumer"][key]["count"] = True
        with self.assertRaises(ValueError):
            aggregate(encode(values), SOURCE, "success")


class FullSuitePytestProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jae-full-suite-probe-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/__init__.py").write_text("")
        for name in ("ci_full_suite.py", "ci_full_suite_cohort.py"):
            source = (ROOT / "scripts" / name).read_text()
            if name == "ci_full_suite_cohort.py":
                # This committed synthetic repository uses this test interpreter.
                # Production remains independently pinned; no runtime bypass.
                source = source.replace("PYTHON_VERSION = (3, 12, 14)",
                                        "PYTHON_VERSION = " + repr(tuple(sys.version_info[:3])), 1)
            (self.root / "scripts" / name).write_text(source)
        (self.root / "tests").mkdir()
        (self.root / "requirements.txt").write_text("pytest==9.1.1\n")
        (self.root / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
        (self.root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
        (self.root / "tests/test_consumer_entry_v1.py").write_text('''import pytest
@pytest.fixture(scope="module")
def module_owner():
    values = []
    yield values
    assert values == ["first", "second"]
def test_first(module_owner): module_owner.append("first")
def test_second(module_owner): module_owner.append("second")
''')
        (self.root / "tests/test_jcr_probe.py").write_text('''import pytest
@pytest.mark.skip(reason="existing platform gate")
def test_skip(): assert False
@pytest.mark.xfail(strict=False, raises=RuntimeError, reason="unsafe comparator")
def test_xpass(): pass
@pytest.mark.xfail(strict=False, raises=RuntimeError, reason="unsafe comparator")
def test_xfail(): raise RuntimeError("expected comparator")
''')
        (self.root / "tests/test_remainder.py").write_text('''import pytest
import unittest
@pytest.mark.parametrize("value", ["first", "second"], ids=["synthetic-first", "synthetic-second"])
def test_parameter(value): assert value
class TestSubcases(unittest.TestCase):
    def test_subtests(self):
        for n in (1, 2):
            with self.subTest(n=n): self.assertGreater(n, 0)
''')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("JAE_", "GITHUB_", "PYTEST_"))}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Synthetic fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Synthetic fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git("init", "--template=")
        self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env,
                                       stderr=subprocess.DEVNULL, text=True, timeout=15).strip()

    def commit(self):
        self.git("add", "scripts", "tests", "requirements.txt", "pytest.ini", ".gitignore")
        self.git("commit", "-qm", "Synthetic full-suite fixture")
        self.sha = self.git("rev-parse", "HEAD")

    def run_owner(self, shard, *, args=(), env=None):
        target = self.root / (shard + ".json")
        target.unlink(missing_ok=True)
        options = dict(self.env, JAE_FULL_SUITE_SHARD=shard,
                       JAE_FULL_SUITE_EXPECTED_SHA=self.sha, JAE_FULL_SUITE_RECEIPT=str(target),
                       GITHUB_OUTPUT=str(self.root / (shard + ".output")))
        options.update(env or {})
        run = subprocess.run([sys.executable, "-m", "pytest", "-v", "-p", "scripts.ci_full_suite", *args],
                             cwd=self.root, env=options, text=True, capture_output=True, timeout=30)
        return run, json.loads(target.read_text()) if target.exists() else None

    def cohort_options(self):
        for name in ("browsers", "standalone"):
            (self.root / name).mkdir(exist_ok=True)
        return dict(self.env, JAE_FULL_SUITE_COHORT=str(self.root / "cohort"),
                    JAE_FULL_SUITE_EXPECTED_SHA=self.sha,
                    PLAYWRIGHT_BROWSERS_PATH=str(self.root / "browsers"),
                    JAE_STANDALONE_RUNTIME=str(self.root / "standalone"))

    def cohort_command(self, action, env, owner=None):
        command = [sys.executable, "scripts/ci_full_suite_cohort.py", action]
        if owner is not None:
            command.append(owner)
        return subprocess.run(command, cwd=self.root, env=env, text=True, capture_output=True, timeout=45)

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_same_host_actual_concurrent_owners_have_independent_mutable_paths(self):
        probe = '''
import json, os, socket, tempfile, time
from pathlib import Path
def test_owner_resources():
    owner = os.environ["JAE_FULL_SUITE_SHARD"]
    source = Path(__file__).resolve().parents[1]
    home, temporary = Path.home(), Path(tempfile.gettempdir())
    for root in (source, home, temporary):
        assert not (root / "same-name-resource").exists()
        (root / "same-name-resource").write_text(owner)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen()
        for root in (source, home, temporary):
            assert (root / "same-name-resource").read_text() == owner
        Path(os.environ["JAE_FULL_SUITE_RECEIPT"] + ".probe").write_text(json.dumps({
            "pid": os.getpid(), "port": listener.getsockname()[1], "source": str(source),
            "home": str(home), "tmp": str(temporary), "display": os.environ.get("DISPLAY"),
            "browsers": os.environ["PLAYWRIGHT_BROWSERS_PATH"],
            "runtime": os.environ["JAE_STANDALONE_RUNTIME"],
        }))
        # All three sockets remain bound until every owner advertises its port.
        audit = Path(os.environ["JAE_FULL_SUITE_RECEIPT"]).parent
        deadline = time.monotonic() + 10
        while len(list(audit.glob("*.receipt.json.probe"))) != 3:
            assert time.monotonic() < deadline, "owners did not overlap"
            time.sleep(0.02)
'''
        for name in ("test_consumer_entry_v1.py", "test_jcr_probe.py", "test_remainder.py"):
            (self.root / "tests" / name).write_text(probe)
        self.commit()
        env = self.cohort_options()
        env["DISPLAY"] = ":99"
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        children = []
        for owner in SHARDS:
            options = dict(env, GITHUB_OUTPUT=str(self.root / (owner + ".output")))
            children.append(subprocess.Popen([sys.executable, "scripts/ci_full_suite_cohort.py", "owner", owner],
                cwd=self.root, env=options, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for child in children:
            out, err = child.communicate(timeout=45)
            self.assertEqual(child.returncode, 0, out + err)
        audit = self.root / "cohort" / "audit"
        probes = [json.loads((audit / (owner + ".receipt.json.probe")).read_text()) for owner in SHARDS]
        for key in ("pid", "port", "source", "home", "tmp"):
            self.assertEqual(len({p[key] for p in probes}), 3, key)
        for probe in probes:
            self.assertIsNone(probe["display"])
            self.assertEqual(probe["browsers"], env["PLAYWRIGHT_BROWSERS_PATH"])
            self.assertEqual(probe["runtime"], env["JAE_STANDALONE_RUNTIME"])
        for owner in SHARDS:
            isolation = json.loads((audit / (owner + ".isolation.json")).read_text())
            self.assertEqual(isolation["source"]["sha"], self.sha)
            self.assertEqual(json.loads((audit / (owner + ".execution.json")).read_text())["exit_status"], 0)
            self.assertTrue((self.root / (owner + ".output")).read_text().startswith("receipt_" + owner + "="))
            observations = [json.loads(line) for line in (audit / (owner + ".host.jsonl")).read_text().splitlines()]
            self.assertTrue(observations)
            for observation in observations:
                self.assertIn("/proc/meminfo", observation["kernel"])
                self.assertIn("/proc/net/tcp6", observation["kernel"])
                self.assertIn("/proc/net/udp", observation["kernel"])
                self.assertEqual(observation["disk"]["command"][:2], ["df", "-B1"])
                self.assertEqual(observation["sockets"]["command"], ["ss", "-lntup"])
                self.assertIn("browser_resources", observation)
        env["JAE_FULL_SUITE_OWNER_RESULTS"] = json.dumps(dict.fromkeys(SHARDS, "success"))
        verified = self.cohort_command("verify", env)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual(json.loads((audit / "summary.json").read_text())["collection"]["count"], 3)

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_cohort_failed_owner_preserves_other_receipts_and_summary_refuses_all_non_success(self):
        (self.root / "tests/test_consumer_entry_v1.py").write_text("def test_failure(): assert False\n")
        self.commit()
        env = self.cohort_options()
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        results = {}
        for owner in SHARDS:
            run = self.cohort_command("owner", env, owner)
            self.assertEqual(run.returncode, 1 if owner == "consumer" else 0, run.stdout + run.stderr)
            results[owner] = "failure" if run.returncode else "success"
        audit = self.root / "cohort" / "audit"
        snapshots = {owner: (audit / (owner + ".receipt.json")).read_bytes() for owner in SHARDS}
        self.assertEqual(json.loads(snapshots["consumer"])["exit_status"], 1)
        for state in ("failure", "cancelled", "skipped", "pending", ""):
            results["consumer"] = state
            env["JAE_FULL_SUITE_OWNER_RESULTS"] = json.dumps(results)
            result = self.cohort_command("verify", env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((audit / "summary.json").exists())
            for owner in SHARDS:
                self.assertEqual((audit / (owner + ".receipt.json")).read_bytes(), snapshots[owner])
        del results["consumer"]
        env["JAE_FULL_SUITE_OWNER_RESULTS"] = json.dumps(results)
        self.assertNotEqual(self.cohort_command("verify", env).returncode, 0)
        # Even forged successful native outcomes cannot overrule a failed receipt.
        env["JAE_FULL_SUITE_OWNER_RESULTS"] = json.dumps(dict.fromkeys(SHARDS, "success"))
        self.assertNotEqual(self.cohort_command("verify", env).returncode, 0)
        (audit / "consumer.receipt.json").unlink()
        self.assertNotEqual(self.cohort_command("verify", env).returncode, 0)
        for owner in ("browser_contract", "remainder"):
            self.assertEqual((audit / (owner + ".receipt.json")).read_bytes(), snapshots[owner])

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_actual_owner_cancellation_keeps_other_owners_and_refuses_summary(self):
        (self.root / "tests/test_consumer_entry_v1.py").write_text('''
import os, time
from pathlib import Path
def test_cancelled():
    Path(os.environ["JAE_FULL_SUITE_RECEIPT"] + ".started").write_text(str(os.getpid()))
    time.sleep(30)
''')
        self.commit()
        env = self.cohort_options()
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        audit = self.root / "cohort" / "audit"
        children = {}
        for owner in SHARDS:
            children[owner] = subprocess.Popen([sys.executable, "scripts/ci_full_suite_cohort.py", "owner", owner],
                cwd=self.root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            sentinel = audit / "consumer.receipt.json.started"
            deadline = time.monotonic() + 15
            while not sentinel.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(sentinel.exists())
            pytest_pid = int(sentinel.read_text())
            children["consumer"].send_signal(signal.SIGTERM)
            out, err = children["consumer"].communicate(timeout=15)
            self.assertEqual(children["consumer"].returncode, 143, out + err)
            with self.assertRaises(ProcessLookupError):
                os.kill(pytest_pid, 0)
            for owner in ("browser_contract", "remainder"):
                out, err = children[owner].communicate(timeout=30)
                self.assertEqual(children[owner].returncode, 0, out + err)
                self.assertEqual(json.loads((audit / (owner + ".receipt.json")).read_text())["exit_status"], 0)
            self.assertFalse((audit / "consumer.receipt.json").exists())
            self.assertTrue((audit / "consumer.host-end.json").exists())
            env["JAE_FULL_SUITE_OWNER_RESULTS"] = json.dumps(dict.fromkeys(SHARDS, "success") | {"consumer": "cancelled"})
            result = self.cohort_command("verify", env)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((audit / "summary.json").exists())
        finally:
            for child in children.values():
                if child.poll() is None:
                    child.terminate()
                child.communicate(timeout=15)

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_compile_gate_targets_each_tested_worktree_and_propagates_failure(self):
        probe = '''
import os
from pathlib import Path
def test_owner_compile():
    source = Path(__file__).resolve().parents[1]
    (source / "executor").mkdir(exist_ok=True)
    if os.environ["JAE_FULL_SUITE_SHARD"] == "consumer":
        (source / "executor/late_invalid.py").write_text("def broken(\\n")
'''
        for name in ("test_consumer_entry_v1.py", "test_jcr_probe.py", "test_remainder.py"):
            (self.root / "tests" / name).write_text(probe)
        self.commit()
        env = self.cohort_options()
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        audit = self.root / "cohort" / "audit"
        for owner in SHARDS:
            run = self.cohort_command("owner", env, owner)
            expected = 1 if owner == "consumer" else 0
            self.assertEqual(run.returncode, expected, run.stdout + run.stderr)
            self.assertEqual(json.loads((audit / (owner + ".execution.json")).read_text())["compile_status"], expected)
            self.assertEqual(json.loads((audit / (owner + ".receipt.json")).read_text())["exit_status"], 0)
        self.assertFalse((self.root / "executor/late_invalid.py").exists())
        self.assertTrue((self.root / "cohort/consumer/source/executor/late_invalid.py").exists())
        env["JAE_FULL_SUITE_OWNER_RESULTS"] = json.dumps(dict.fromkeys(SHARDS, "success") | {"consumer": "failure"})
        self.assertNotEqual(self.cohort_command("verify", env).returncode, 0)
        self.assertFalse((audit / "summary.json").exists())

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_detached_term_ignoring_descendants_are_reaped_after_leader_exit(self):
        probe = '''
import os, signal, subprocess, sys, threading, time
from pathlib import Path
def test_detached_tree():
    root = os.environ["JAE_FULL_SUITE_RECEIPT"]
    grandchild = "import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(" + repr(root + ".grandchild") + ").write_text(str(os.getpid()));time.sleep(60)"
    child = "import os,signal,subprocess,sys,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,'-c'," + repr(grandchild) + "],start_new_session=True);Path(" + repr(root + ".child") + ").write_text(str(os.getpid()));time.sleep(60)"
    thread = threading.Thread(target=lambda: subprocess.Popen(
        [sys.executable, "-c", child], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    thread.start(); thread.join()
    deadline = time.monotonic() + 10
    while not Path(root + ".grandchild").exists():
        assert time.monotonic() < deadline
        time.sleep(.02)
    # The pytest leader exits successfully before either detached descendant.
'''
        (self.root / "tests/test_consumer_entry_v1.py").write_text(probe)
        self.commit()
        env = self.cohort_options()
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        run = self.cohort_command("owner", env, "consumer")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        audit = self.root / "cohort" / "audit"
        for suffix in ("child", "grandchild"):
            pid = int((audit / ("consumer.receipt.json." + suffix)).read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        cleanup = json.loads((audit / "consumer.cleanup.json").read_text())
        self.assertTrue(cleanup["complete"])
        self.assertIsNone(cleanup["cancelled_signal"])
        self.assertGreaterEqual(sum(e.get("signal") == signal.SIGKILL for e in cleanup["events"]), 2)
        self.assertGreaterEqual(sum("reaped_pid" in e for e in cleanup["events"]), 2)

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_cancellation_during_compile_reaps_detached_tree_without_signalling_sibling(self):
        compile_body = '''
import os, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
root = os.environ["JAE_FULL_SUITE_RECEIPT"]
child = "import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(" + repr(root + ".compile-child") + ").write_text(str(os.getpid()));time.sleep(60)"
subprocess.Popen([sys.executable, "-c", child], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path(root + ".compile").write_text(str(os.getpid()))
time.sleep(60)
'''
        (self.root / "tests/test_consumer_entry_v1.py").write_text(
            "from pathlib import Path\ndef test_prepare_compile():\n"
            "    Path('compileall.py').write_text(" + repr(compile_body) + ")\n")
        sibling = '''
import os, time
from pathlib import Path
def test_sibling_stays_alive():
    root = Path(os.environ["JAE_FULL_SUITE_RECEIPT"])
    Path(str(root) + ".sibling").write_text(str(os.getpid()))
    deadline = time.monotonic() + 20
    while not Path(str(root) + ".release").exists():
        assert time.monotonic() < deadline
        time.sleep(.02)
'''
        (self.root / "tests/test_jcr_probe.py").write_text(sibling)
        self.commit()
        env = self.cohort_options()
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        children = {owner: subprocess.Popen([sys.executable, "scripts/ci_full_suite_cohort.py", "owner", owner],
            cwd=self.root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for owner in ("consumer", "browser_contract")}
        audit = self.root / "cohort" / "audit"
        try:
            targets = [audit / name for name in ("consumer.receipt.json.compile", "consumer.receipt.json.compile-child", "browser_contract.receipt.json.sibling")]
            deadline = time.monotonic() + 15
            while not all(path.exists() for path in targets) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(all(path.exists() for path in targets))
            pids = [int(path.read_text()) for path in targets]
            children["consumer"].send_signal(signal.SIGTERM)
            out, err = children["consumer"].communicate(timeout=15)
            self.assertEqual(children["consumer"].returncode, 143, out + err)
            for pid in pids[:2]:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            os.kill(pids[2], 0)
            self.assertIsNone(children["browser_contract"].poll())
            cleanup = json.loads((audit / "consumer.cleanup.json").read_text())
            self.assertTrue(cleanup["complete"])
            self.assertNotIn(pids[2], [e.get("pid") for e in cleanup["events"]])
            (audit / "browser_contract.receipt.json.release").write_text("finish")
            out, err = children["browser_contract"].communicate(timeout=15)
            self.assertEqual(children["browser_contract"].returncode, 0, out + err)
        finally:
            for child in children.values():
                if child.poll() is None:
                    child.terminate()
                child.communicate(timeout=15)

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_spawn_window_signals_are_deferred_until_child_is_owned(self):
        (self.root / "tests/test_consumer_entry_v1.py").write_text("import time\ndef test_wait(): time.sleep(30)\n")
        self.commit()
        env = self.cohort_options()
        prepared = self.cohort_command("prepare", env)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        harness = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import ci_full_suite_cohort as cohort
real = cohort.subprocess.Popen
phase = sys.argv[1]
def spawn(command, **kwargs):
    if command[1:3] != ["-m", "pytest"]:
        return real(command, **kwargs)
    if phase == "before": os.kill(os.getpid(), signal.SIGTERM)
    process = real(command, **kwargs)
    Path(os.environ["JAE_FULL_SUITE_COHORT"], "spawned-pid").write_text(str(process.pid))
    if phase == "after": os.kill(os.getpid(), signal.SIGTERM)
    return process
cohort.subprocess.Popen = spawn
raise SystemExit(cohort.run_owner(Path(os.environ["JAE_FULL_SUITE_COHORT"]), "consumer"))
'''
        for phase in ("before", "after"):
            run = subprocess.run([sys.executable, "-c", harness, phase], cwd=self.root,
                                 env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(run.returncode, 143, run.stdout + run.stderr)
            pid = int((self.root / "cohort/spawned-pid").read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            cleanup = json.loads((self.root / "cohort/audit/consumer.cleanup.json").read_text())
            self.assertTrue(cleanup["complete"])
            self.assertEqual(cleanup["cancelled_signal"], signal.SIGTERM)

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_unavailable_lifecycle_or_unproved_cleanup_never_falls_back_to_success(self):
        harness = '''
import os, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import ci_full_suite_cohort as cohort
mode = sys.argv[1]
if mode == "subreaper":
    class Unavailable:
        def prctl(self, *_): return -1
    cohort.ctypes.CDLL = lambda *_a, **_k: Unavailable()
elif mode == "pidfd":
    del cohort.os.pidfd_open
elif mode == "cleanup":
    # Even an erroneous child-list identity must never signal this process.
    cohort.OwnerLifecycle.children = lambda self: [os.getpid()]
with cohort.OwnerLifecycle(Path("lifecycle-" + mode + ".json")):
    pass
'''
        for mode in ("subreaper", "pidfd", "cleanup"):
            run = subprocess.run([sys.executable, "-c", harness, mode], cwd=self.root,
                                 env=self.env, text=True, capture_output=True, timeout=15)
            self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
            if mode == "cleanup":
                cleanup = json.loads((self.root / ("lifecycle-" + mode + ".json")).read_text())
                self.assertFalse(cleanup["complete"])
                self.assertEqual(cleanup["events"], [])

    @unittest.skipUnless(sys.platform == "linux", "Linux-only cohort ownership; native Mac gates are separate")
    def test_empty_proc_snapshot_is_not_evidence_of_no_children(self):
        harness = '''
import os, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, "scripts")
import ci_full_suite_cohort as cohort
sentinel = Path("racing-child.pid").resolve()
script = "import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(" + repr(str(sentinel)) + ").write_text(str(os.getpid()));time.sleep(60)"
real = cohort.OwnerLifecycle.children
missed = False
def snapshot(self):
    global missed
    if not missed:
        missed = True
        return []  # A deterministic miss in the non-atomic /proc candidate list.
    return real(self)
cohort.OwnerLifecycle.children = snapshot
with cohort.OwnerLifecycle(Path("racing-cleanup.json")):
    child = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    deadline = time.monotonic() + 10
    while not sentinel.exists():
        assert time.monotonic() < deadline
        time.sleep(.02)
assert missed
try: os.kill(int(sentinel.read_text()), 0)
except ProcessLookupError: pass
else: raise AssertionError("an empty snapshot lost a living owned child")
'''
        run = subprocess.run([sys.executable, "-c", harness], cwd=self.root,
                             env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(json.loads((self.root / "racing-cleanup.json").read_text())["complete"])

    def test_real_owner_union_matches_unpartitioned_collection_and_subtests(self):
        outputs = {}
        for shard in SHARDS:
            run, receipt = self.run_owner(shard)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(receipt["started"], receipt["finished"])
            outputs["receipt_" + shard] = json.dumps(receipt)
        summary = aggregate(outputs, receipt["source"], "success")
        self.assertEqual(summary["collection"]["count"], 8)
        self.assertEqual(summary["outcomes"], dict(passed=5, skipped=1, xpassed=1, xfailed=1, failed=0, errors=0))
        self.assertEqual(summary["subtests"]["passed"], 2)
        plain = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                               cwd=self.root, env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(plain.returncode, 0, plain.stdout + plain.stderr)
        nodes = [s for s in plain.stdout.splitlines() if s.startswith("tests/") and "::" in s]
        self.assertEqual(len(nodes), 8)
        self.assertEqual(universe_inventory([(s.split("::")[0], s) for s in nodes]), summary["collection"])
        # The actual stdlib-only aggregate CLI emits authority only after all
        # owners succeed on a labeled PR. Failure/missing output emits nothing.
        target = self.root / "aggregate.output"
        for event, action, result, missing in (
                ("push", "", "success", False),
                ("pull_request", "synchronize", "success", False),
                ("pull_request", "labeled", "success", False),
                ("pull_request", "labeled", "failure", False),
                ("pull_request", "labeled", "success", True)):
            target.unlink(missing_ok=True)
            payload = dict(outputs)
            if missing:
                del payload["receipt_consumer"]
            env = dict(self.env, JAE_FULL_SUITE_EXPECTED_SHA=self.sha,
                       JAE_FULL_SUITE_RECEIPTS=json.dumps(payload), JAE_FULL_SUITE_RESULT=result,
                       JAE_EVENT_ACTION=action, GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(target))
            command = subprocess.run([sys.executable, "-I", "scripts/ci_full_suite.py", "aggregate"],
                                     cwd=self.root, env=env, text=True, capture_output=True, timeout=15)
            success = result == "success" and not missing
            self.assertEqual(command.returncode == 0, success, command.stdout + command.stderr)
            expected = "sha=" + self.sha + "\n" if success and action == "labeled" else ""
            self.assertEqual(target.read_text() if target.exists() else "", expected)

    def test_real_failure_strict_xpass_early_exit_and_teardown_skip(self):
        probes = [
            ("def test_failure(): assert False\n", False),
            ("import pytest\n@pytest.mark.xfail(strict=True)\ndef test_strict(): pass\n", False),
            ("import pytest\ndef test_early(): pytest.exit('early', returncode=0)\ndef test_omitted(): pass\n", False),
            ("import pytest\n@pytest.fixture\ndef late():\n yield\n pytest.skip('teardown skip')\ndef test_late(late): pass\n", True),
        ]
        for body, success in probes:
            with self.subTest(body=body):
                (self.root / "tests/test_remainder.py").write_text(body)
                self.commit()
                run, receipt = self.run_owner("remainder")
                self.assertEqual(run.returncode == 0, success, run.stdout + run.stderr)
                self.assertEqual(receipt["exit_status"] == 0, success)
                if success:
                    self.assertEqual(sum(receipt["outcomes"].values()), 1)
                    self.assertEqual(receipt["teardown"]["skipped"], 1)

    def test_collection_skips_are_owned_once_and_importorskip_is_preserved(self):
        (self.root / "tests/test_module_skip.py").write_text(
            "import pytest\npytest.skip('synthetic module gate', allow_module_level=True)\n")
        (self.root / "tests/test_jcr_optional.py").write_text(
            "import pytest\npytest.importorskip('jae_nonexistent_synthetic_dependency_7a7f')\n")
        self.commit()
        outputs, diagnostics = {}, []
        for shard in SHARDS:
            run, receipt = self.run_owner(shard)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(len(receipt["collection"]["all"]), 2)
            diagnostics.extend(receipt["collection"]["owned"])
            outputs["receipt_" + shard] = json.dumps(receipt)
        self.assertEqual(len(diagnostics), 2)
        summary = aggregate(outputs, receipt["source"], "success")
        self.assertEqual(summary["collection_outcomes"], {"skipped": 2, "errors": 0})
        self.assertEqual(summary["collection"]["count"], 8)
        self.assertEqual(summary["outcomes"]["skipped"], 1)

    def test_collection_error_anywhere_fails_another_owner(self):
        (self.root / "tests/test_remainder.py").write_text("def broken(\n")
        self.commit()
        plain = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=self.root,
                               env=self.env, text=True, capture_output=True, timeout=30)
        diagnostics = []
        for shard in SHARDS:
            run, receipt = self.run_owner(shard)
            self.assertEqual(run.returncode, plain.returncode, run.stdout + run.stderr)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(receipt["exit_status"], 2)
            self.assertEqual(receipt["started"]["count"], 0)
            diagnostics.extend(receipt["collection"]["owned"])
            self.assertEqual(len(receipt["collection"]["all"]), 1)
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["outcome"], "errors")

    def test_selectors_collection_only_and_options_cannot_shrink_coverage(self):
        for args, extra in ((("-k", "first"), {}), (("--collect-only",), {}), ((), {"PYTEST_ADDOPTS": "-k first"})):
            with self.subTest(args=args, extra=extra):
                run, receipt = self.run_owner("consumer", args=args, env=extra)
                self.assertNotEqual(run.returncode, 0)
                self.assertIsNone(receipt)

    def test_wrong_source_and_modified_tracked_checkout_fail(self):
        run, receipt = self.run_owner("consumer", env={"JAE_FULL_SUITE_EXPECTED_SHA": "0" * 40})
        self.assertNotEqual(run.returncode, 0)
        self.assertIsNone(receipt)
        (self.root / "requirements.txt").write_text("changed\n")
        run, receipt = self.run_owner("consumer")
        self.assertNotEqual(run.returncode, 0)
        self.assertIsNone(receipt)


if __name__ == "__main__":
    unittest.main()
