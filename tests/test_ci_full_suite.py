"""Complete-file allocation, raw identity receipts and real pytest failure probes."""
from collections import Counter
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
        (self.root / "scripts/ci_full_suite.py").write_bytes((ROOT / "scripts/ci_full_suite.py").read_bytes())
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
