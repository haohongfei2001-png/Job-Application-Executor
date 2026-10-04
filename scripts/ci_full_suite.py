"""Three complete-file Linux CI owners and fail-closed, value-free receipts.

Loaded explicitly with ``python -m pytest -v -p scripts.ci_full_suite`` only in
full-suite jobs. Other jobs and normal pytest invocations are unchanged. The
aggregate CLI uses only stdlib and never evaluates receipt strings as code.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SHARDS = ("consumer", "browser_contract", "remainder")
CONSUMER = frozenset({
    "tests/test_consumer_entry_v1.py",
    "tests/test_consumer_recovery_transactions_v1.py",
    "tests/test_task_state_compatibility_v1.py",
})
BROWSER = frozenset({"tests/test_field_action_journal_v1.py", "tests/test_generic_browser_v1.py"})
FORMAT = "jae-full-suite-three-shards-v1"
OUTCOMES = ("passed", "skipped", "xfailed", "xpassed", "failed", "errors")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def owner(path):
    if path in CONSUMER:
        return "consumer"
    if path in BROWSER or (path.startswith("tests/test_jcr") and path.endswith(".py") and path.count("/") == 1):
        return "browser_contract"
    return "remainder"


def digest(values):
    # Sort a LIST, not a set: repeated parametrizations retain multiplicity.
    raw = json.dumps(sorted(values), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def inventory(records):
    return {"files": sorted({path for path, _ in records}),
            "count": len(records), "digest": digest([node for _, node in records])}


def collection_root(plan):
    # A domain-separated root binds the full collection to all three raw-nodeID
    # multiset leaves, without copying thousands of IDs into Actions outputs.
    value = [[s, plan[s]["count"], plan[s]["digest"]] for s in SHARDS]
    raw = FORMAT + ":collection:" + json.dumps(value, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def universe_inventory(records):
    result = inventory(records)
    leaves = {s: inventory([r for r in records if owner(r[0]) == s]) for s in SHARDS}
    result["digest"] = collection_root(leaves)
    return result


def partition(records, *, collection_failed=False):
    groups = {shard: [] for shard in SHARDS}
    for path, node in records:
        groups[owner(path)].append((path, node))
    covered = [record for shard in SHARDS for record in groups[shard]]
    if Counter(covered) != Counter(records):
        raise ValueError("incomplete or duplicated full collection")
    files = [path for shard in SHARDS for path in inventory(groups[shard])["files"]]
    if len(files) != len(set(files)):
        raise ValueError("a file was split between owners")
    if not collection_failed and any(not groups[shard] for shard in SHARDS):
        raise ValueError("empty full-suite owner")
    return groups


def source_identity(root):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True, timeout=30).strip()
    expected = os.environ["JAE_FULL_SUITE_EXPECTED_SHA"]
    sha = git("rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", expected) or sha != expected:
        raise ValueError("full-suite checkout identity mismatch")
    # A SHA alone must never attest to a modified tracked checkout.
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], cwd=root,
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    return {"sha": sha, "tree": git("rev-parse", "HEAD^{tree}"),
            "requirements": hashlib.sha256((root / "requirements.txt").read_bytes()).hexdigest()}


def validate_inventory(value):
    if (not isinstance(value, dict) or set(value) != {"files", "count", "digest"}
            or type(value["count"]) is not int or value["count"] < 1
            or not isinstance(value["digest"], str) or not HEX.fullmatch(value["digest"])
            or not isinstance(value["files"], list) or not value["files"]
            or any(not isinstance(p, str) or not p or p.startswith("/")
                   or any(x in {"", ".", ".."} for x in p.split("/")) for p in value["files"])
            or value["files"] != sorted(set(value["files"]))):
        raise ValueError("malformed full-suite inventory")


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate receipt field")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def aggregate(outputs, expected_source, result):
    if not isinstance(outputs, dict) or result != "success" or set(outputs) != {"receipt_" + s for s in SHARDS}:
        raise ValueError("failed, skipped, cancelled or missing full-suite owner")
    common = None
    totals = dict.fromkeys(OUTCOMES, 0)
    subtests = dict.fromkeys(OUTCOMES, 0)
    teardown = dict.fromkeys(OUTCOMES, 0)
    collection_counts = {"skipped": 0, "errors": 0}
    for shard in SHARDS:
        receipt = decode(outputs["receipt_" + shard])
        if (not isinstance(receipt, dict) or set(receipt) != {
                "format", "shard", "source", "runtime", "universe", "plan", "selected",
                "started", "finished", "exit_status", "outcomes", "subtests", "teardown", "collection"}
                or receipt["format"] != FORMAT or receipt["shard"] != shard
                or receipt["source"] != expected_source
                or type(receipt["exit_status"]) is not int or receipt["exit_status"] != 0):
            raise ValueError("invalid or unsuccessful full-suite receipt")
        collection = receipt["collection"]
        if (not isinstance(collection, dict) or set(collection) != {"all", "owned"}
                or not isinstance(collection["all"], list) or not isinstance(collection["owned"], list)):
            raise ValueError("malformed collection diagnostics")
        for event in collection["all"]:
            if (not isinstance(event, dict) or set(event) != {"path", "nodeid_sha256", "outcome"}
                    or not isinstance(event["path"], str) or not event["path"]
                    or event["path"].startswith("/")
                    or any(p in {"", ".", ".."} for p in event["path"].split("/"))
                    or not isinstance(event["nodeid_sha256"], str) or not HEX.fullmatch(event["nodeid_sha256"])
                    or event["outcome"] not in {"skipped", "errors"}):
                raise ValueError("malformed collection event")
            if event["outcome"] == "errors":
                raise ValueError("collection error cannot pass the full gate")
        owned = [e for e in collection["all"] if owner(e["path"]) == shard]
        if collection["owned"] != owned:
            raise ValueError("collection diagnostics have wrong or duplicate ownership")
        for event in owned:
            collection_counts[event["outcome"]] += 1
        validate_inventory(receipt["universe"])
        if not isinstance(receipt["plan"], dict) or set(receipt["plan"]) != set(SHARDS):
            raise ValueError("missing partition in full-suite plan")
        for name, selected in receipt["plan"].items():
            validate_inventory(selected)
            if any(owner(path) != name for path in selected["files"]):
                raise ValueError("wrong complete-file owner")
        files = [p for name in SHARDS for p in receipt["plan"][name]["files"]]
        if (len(files) != len(set(files)) or sorted(files) != receipt["universe"]["files"]
                or sum(receipt["plan"][s]["count"] for s in SHARDS) != receipt["universe"]["count"]
                or collection_root(receipt["plan"]) != receipt["universe"]["digest"]):
            raise ValueError("partition is not a disjoint complete union")
        selected = receipt["plan"][shard]
        validate_inventory(receipt["selected"])
        for key in ("started", "finished"):
            execution = receipt[key]
            if (not isinstance(execution, dict) or set(execution) != {"count", "digest"}
                    or type(execution["count"]) is not int or execution["count"] < 1
                    or not isinstance(execution["digest"], str) or not HEX.fullmatch(execution["digest"])):
                raise ValueError("malformed execution inventory")
        if (receipt["selected"] != selected
                or receipt["started"] != {"count": selected["count"], "digest": selected["digest"]}
                or receipt["finished"] != receipt["started"]):
            raise ValueError("selected and executed raw nodeID multisets differ")
        if (not isinstance(receipt["runtime"], dict)
                or set(receipt["runtime"]) != {"python", "pytest", "platform", "image"}
                or any(not isinstance(v, str) or not v for v in receipt["runtime"].values())
                or receipt["runtime"]["platform"] != "linux"):
            raise ValueError("missing Linux runtime identity")
        identity = {k: receipt[k] for k in ("source", "runtime", "universe", "plan")}
        identity["collection"] = collection["all"]
        if common is not None and identity != common:
            raise ValueError("different source, runtime or collection across owners")
        common = identity
        for key, total in (("outcomes", totals), ("subtests", subtests), ("teardown", teardown)):
            counts = receipt[key]
            if (not isinstance(counts, dict) or set(counts) != set(OUTCOMES)
                    or any(type(v) is not int or v < 0 for v in counts.values())
                    or counts["failed"] or counts["errors"]):
                raise ValueError("invalid or failed test outcomes")
            for outcome in OUTCOMES:
                total[outcome] += counts[outcome]
        if sum(receipt["outcomes"].values()) != selected["count"]:
            raise ValueError("missing terminal outcome")
    return {"source": expected_source, "collection": common["universe"],
            "outcomes": totals, "subtests": subtests, "teardown": teardown,
            "collection_outcomes": collection_counts}


class FullSuite:
    def __init__(self, config):
        self.root = Path(config.rootpath)
        self.shard = os.environ["JAE_FULL_SUITE_SHARD"]
        if self.shard not in SHARDS:
            raise ValueError("unknown full-suite owner")
        if (tuple(config.invocation_params.args) != ("-v", "-p", "scripts.ci_full_suite")
                or config.getini("addopts") or os.environ.get("PYTEST_ADDOPTS")):
            raise ValueError("full-suite collection cannot use additional selectors/options")
        self.source = source_identity(self.root)
        import pytest
        self.runtime = {"python": ".".join(map(str, sys.version_info[:3])),
                        "pytest": pytest.__version__, "platform": sys.platform,
                        "image": os.environ.get("ImageOS", "local") + ":" + os.environ.get("ImageVersion", "local")}
        self.collected, self.selected, self.started, self.finished = [], [], [], []
        self.outcomes, self.subtests, self.teardown = Counter(), Counter(), Counter()
        self.groups = None
        self.collection_diagnostics = []

    def record(self, item):
        return (item.path.relative_to(self.root).as_posix(), item.nodeid)

    def pytest_itemcollected(self, item):
        self.collected.append(self.record(item))

    def pytest_collectreport(self, report):
        if report.skipped or report.failed:
            self.collection_diagnostics.append({
                "path": report.nodeid.split("::", 1)[0],
                "nodeid_sha256": hashlib.sha256(report.nodeid.encode("utf-8")).hexdigest(),
                "outcome": "errors" if report.failed else "skipped",
            })

    def pytest_collection_modifyitems(self, session, config, items):
        if [self.record(item) for item in items] != self.collected:
            raise ValueError("full collection was changed before allocation")
        self.groups = partition(self.collected, collection_failed=any(
            event["outcome"] == "errors" for event in self.collection_diagnostics))
        self.selected = self.groups[self.shard]
        kept, other = [], []
        for item in items:
            (kept if owner(self.record(item)[0]) == self.shard else other).append(item)
        items[:] = kept
        config.hook.pytest_deselected(items=other)

    def pytest_collection_finish(self, session):
        if [self.record(item) for item in session.items] != self.selected:
            raise ValueError("allocated collection was changed")

    def pytest_runtest_logstart(self, nodeid, location):
        self.started.append(nodeid)

    def pytest_runtest_logfinish(self, nodeid, location):
        self.finished.append(nodeid)

    def pytest_runtest_logreport(self, report):
        counts = (self.subtests if hasattr(report, "context") else
                  self.teardown if report.when == "teardown" else self.outcomes)
        if report.failed:
            counts["failed" if report.when == "call" else "errors"] += 1
        elif hasattr(report, "wasxfail"):
            counts["xfailed" if report.skipped else "xpassed"] += 1
        elif report.skipped:
            counts["skipped"] += 1
        elif report.when in {"call", "teardown"}:
            counts["passed"] += 1

    def pytest_sessionfinish(self, session, exitstatus):
        expected = [node for _, node in self.selected]
        # Preserve serial order as well as multiplicity. A premature successful
        # exit or later selection change cannot publish a successful receipt.
        if int(session.exitstatus) == 0 and (self.groups is None or self.started != expected
                                            or self.finished != expected or session.testsfailed):
            session.exitstatus = 1
        if self.groups is None:
            return
        receipt = {"format": FORMAT, "shard": self.shard, "source": self.source,
                   "runtime": self.runtime, "universe": universe_inventory(self.collected),
                   "plan": {s: inventory(self.groups[s]) for s in SHARDS},
                   "selected": inventory(self.selected),
                   "started": {"count": len(self.started), "digest": digest(self.started)},
                   "finished": {"count": len(self.finished), "digest": digest(self.finished)},
                   "exit_status": int(session.exitstatus),
                   "outcomes": {k: self.outcomes[k] for k in OUTCOMES},
                   "subtests": {k: self.subtests[k] for k in OUTCOMES},
                   "teardown": {k: self.teardown[k] for k in OUTCOMES},
                   # All owners perform full collection. Keep a shared diagnostic
                   # universe, but count each event only on its complete-file owner.
                   "collection": {"all": self.collection_diagnostics,
                                  "owned": [e for e in self.collection_diagnostics
                                            if owner(e["path"]) == self.shard]}}
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        Path(os.environ["JAE_FULL_SUITE_RECEIPT"]).write_text(raw + "\n", encoding="utf-8")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
                handle.write("receipt_" + self.shard + "=" + raw + "\n")
        print("\nFULL_SUITE_RECEIPT " + raw)


def pytest_configure(config):
    config.pluginmanager.register(FullSuite(config), "jae-complete-file-owner")


def main():
    if sys.argv[1:] != ["aggregate"]:
        raise SystemExit("expected aggregate")
    outputs = decode(os.environ["JAE_FULL_SUITE_RECEIPTS"])
    summary = aggregate(outputs, source_identity(Path.cwd()), os.environ["JAE_FULL_SUITE_RESULT"])
    print(json.dumps(summary, sort_keys=True))
    # Only successful exact-head closure runs carry authority to the Mac build.
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request" and os.environ.get("JAE_EVENT_ACTION") == "labeled":
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("sha=" + summary["source"]["sha"] + "\n")


if __name__ == "__main__":
    main()
