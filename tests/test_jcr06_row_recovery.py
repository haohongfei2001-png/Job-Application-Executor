"""Synthetic server rows are the independent oracle for idempotent recovery."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from executor.forms import (DesiredRow, RowActionJournal, RowInventory,
                            RowOutcomeUnknown, RowReconciler,
                            RowReconciliationBlocked, RowExecutionContract, SiteRow)
from executor.application import ApplicationExecutor
from executor.autonomy.worker import outcome
from executor.models import ApplicationStage, ValidationResult, WebField


SCOPE = "synthetic-ats/task-one/draft-one"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


TARGET_DIGEST = digest("synthetic-target")
DRAFT_DIGEST = digest("synthetic-draft")


def reconciler(driver, journal, *, guard=None):
    return RowReconciler(driver, journal, target_sha256=TARGET_DIGEST,
                         draft_id_digest=DRAFT_DIGEST, mutation_guard=guard)


class RowSite(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/rows":
            self.send_error(404)
            return
        self._json({"rows": self.server.rows, "revision": self.server.revision,
                    "complete": self.server.complete,
                    "target_sha256": self.server.target_sha256,
                    "draft_id_digest": self.server.draft_id_digest})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/add":
            # Deliberately non-idempotent: replaying add would create a duplicate.
            self.server.rows.append({"record_id": data["record_id"],
                                     "site_row_id": uuid.uuid4().hex,
                                     "values": data["values"], "managed": True})
            self.server.add_count += 1
        elif self.path == "/delete":
            self.server.rows = [row for row in self.server.rows
                                if row["site_row_id"] != data["site_row_id"]]
            self.server.delete_count += 1
        elif self.path == "/reorder":
            by_id = {row["record_id"]: row for row in self.server.rows}
            self.server.rows = [by_id[rid] for rid in data["record_ids"]]
            self.server.reorder_count += 1
        else:
            self.send_error(404)
            return
        self.server.revision += 1
        self._json({"ok": True})

    def _json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RowHttpDriver:
    capabilities = frozenset({"add", "delete", "reorder"})

    def __init__(self, base):
        self.base = base
        self.fail_after_add = False

    def read_rows(self):
        result = json.load(urllib.request.urlopen(self.base + "/rows"))
        return RowInventory(result["revision"], result["complete"], tuple(
            SiteRow(row["record_id"], row["site_row_id"],
                    digest(row["values"]), row["managed"])
            for row in result["rows"]), result["target_sha256"],
            result["draft_id_digest"])

    def _post(self, path, value):
        request = urllib.request.Request(self.base + path,
            data=json.dumps(value).encode(), headers={"Content-Type": "application/json"},
            method="POST")
        urllib.request.urlopen(request).read()

    def add_row(self, row):
        self._post("/add", {"record_id": row.record_id, "values": dict(row.values)})
        if self.fail_after_add:
            raise ConnectionError("response lost after site committed")

    def delete_row(self, site_row_id):
        self._post("/delete", {"site_row_id": site_row_id})

    def reorder_rows(self, record_ids):
        self._post("/reorder", {"record_ids": record_ids})


@pytest.fixture
def row_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RowSite)
    server.rows, server.revision, server.complete = [], 0, True
    server.target_sha256, server.draft_id_digest = TARGET_DIGEST, DRAFT_DIGEST
    server.add_count = server.delete_count = server.reorder_count = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, RowHttpDriver(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()


def test_reconcile_explicit_delete_add_reorder_and_repeat_without_duplicate(tmp_path, row_site):
    server, driver = row_site
    server.rows = [
        {"record_id": "A", "site_row_id": "site-a", "values": {"school": "A School"},
         "managed": True},
        {"record_id": "C", "site_row_id": "site-c", "values": {"school": "C School"},
         "managed": True},
    ]
    server.revision = 2
    desired = (DesiredRow("B", {"school": "B School"}),
               DesiredRow("A", {"school": "A School"}))
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    receipt = reconciler(driver, journal, guard=lambda: None).reconcile(
        desired, delete_ids=frozenset({"C"}))
    assert (receipt.add_count, receipt.delete_count, receipt.reorder_count) == (1, 1, 1)
    assert [(row["record_id"], row["values"]) for row in server.rows] == [
        ("B", {"school": "B School"}), ("A", {"school": "A School"})]
    assert (server.add_count, server.delete_count, server.reorder_count) == (1, 1, 1)
    repeat = reconciler(driver, RowActionJournal(journal.path, scope=SCOPE)).reconcile(desired)
    assert (repeat.add_count, repeat.delete_count, repeat.reorder_count) == (0, 0, 0)
    assert (server.add_count, server.delete_count, server.reorder_count) == (1, 1, 1)
    assert "B School".encode() not in journal.path.read_bytes()
    server.revision = 0
    with pytest.raises(RowReconciliationBlocked, match="revision regressed"):
        reconciler(driver, RowActionJournal(journal.path, scope=SCOPE)).reconcile(desired)


def test_pending_add_after_crash_reconciles_only_from_readback(tmp_path, row_site):
    server, driver = row_site
    desired = (DesiredRow("B", {"school": "B School"}),)
    journal_path = tmp_path / "private" / "rows.sqlite3"
    child = subprocess.run([sys.executable, "-c", """
import hashlib, json, os, sys, urllib.request
from executor.forms import RowActionJournal
def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()
journal = RowActionJournal(sys.argv[1], scope='synthetic-ats/task-one/draft-one')
journal.begin('add', digest('B'), digest({'school': 'B School'}), 0)
request = urllib.request.Request(sys.argv[2] + '/add',
    data=json.dumps({'record_id': 'B', 'values': {'school': 'B School'}}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
urllib.request.urlopen(request).read()
os._exit(23)
""", str(journal_path), driver.base], capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode(errors="replace")
    # The process died after site commit but before journal completion. A new
    # reconciler may settle read-only; it must not replay the non-idempotent add.
    receipt = reconciler(driver, RowActionJournal(journal_path, scope=SCOPE)).reconcile(desired)
    assert receipt.add_count == 0
    assert server.add_count == 1
    assert not RowActionJournal(journal_path, scope=SCOPE).pending()

    absent_path = tmp_path / "other-private" / "rows.sqlite3"
    RowActionJournal(absent_path, scope=SCOPE).begin("add", digest("MISSING"), digest({"school": "M"}),
                                        server.revision)
    with pytest.raises(RowOutcomeUnknown, match="replay disabled"):
        reconciler(driver, RowActionJournal(absent_path, scope=SCOPE)).reconcile(
            (DesiredRow("B", {"school": "B School"}),
             DesiredRow("MISSING", {"school": "M"})))
    assert server.add_count == 1


def test_lost_response_and_unmanaged_extra_are_safe(tmp_path, row_site):
    server, driver = row_site
    driver.fail_after_add = True
    desired = (DesiredRow("A", {"school": "A School"}),)
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    receipt = reconciler(driver, journal, guard=lambda: None).reconcile(desired)
    assert receipt.add_count == 1
    assert server.add_count == 1

    server.rows.append({"record_id": "OTHER", "site_row_id": "other",
                        "values": {"school": "Other School"}, "managed": False})
    server.revision += 1
    with pytest.raises(RowReconciliationBlocked, match="unbound or unmanaged"):
        reconciler(driver, journal).reconcile(desired)
    assert server.add_count == 1
    assert server.delete_count == 0


def test_unmanaged_matching_row_blocks_before_adding_another_record(tmp_path, row_site):
    server, driver = row_site
    server.rows = [{"record_id": "A", "site_row_id": "someone-elses-row",
                    "values": {"school": "A School"}, "managed": False}]
    server.revision = 1
    desired = (DesiredRow("A", {"school": "A School"}),
               DesiredRow("B", {"school": "B School"}))
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    with pytest.raises(RowReconciliationBlocked, match="unbound or unmanaged"):
        reconciler(driver, journal, guard=lambda: None).reconcile(desired)
    assert server.add_count == 0
    assert journal.pending() == ()


def test_malformed_row_inventory_cannot_assert_management_or_revision(tmp_path, row_site):
    server, driver = row_site
    server.rows = [{"record_id": "A", "site_row_id": "site-a",
                    "values": {"school": "A School"}, "managed": "false"}]
    server.revision = 1
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    with pytest.raises(RowReconciliationBlocked, match="inventory incomplete"):
        reconciler(driver, journal, guard=lambda: None).reconcile(
            (DesiredRow("A", {"school": "A School"}),))
    server.rows[0]["managed"] = True
    server.revision = True
    with pytest.raises(RowReconciliationBlocked, match="inventory incomplete"):
        reconciler(driver, journal, guard=lambda: None).reconcile(
            (DesiredRow("A", {"school": "A School"}),))
    server.revision = 1
    server.rows[0]["record_id"] = ["A"]
    with pytest.raises(RowReconciliationBlocked, match="inventory incomplete"):
        reconciler(driver, journal, guard=lambda: None).reconcile(
            (DesiredRow("A", {"school": "A School"}),))
    assert server.add_count == 0 and journal.pending() == ()


def test_concurrent_row_intents_cannot_both_start(tmp_path):
    path = tmp_path / "private" / "rows.sqlite3"
    RowActionJournal(path, scope=SCOPE)

    def start():
        try:
            return RowActionJournal(path, scope=SCOPE).begin("add", digest("A"),
                                                digest({"school": "A School"}), 0)
        except RowOutcomeUnknown:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: start(), range(2)))
    assert sum(value is not None for value in outcomes) == 1
    assert len(RowActionJournal(path, scope=SCOPE).pending()) == 1
    with pytest.raises(RowReconciliationBlocked, match="scope changed"):
        RowActionJournal(path, scope="different target/draft")
    pending_id = RowActionJournal(path, scope=SCOPE).pending()[0][0]
    RowActionJournal(path, scope=SCOPE).observed(pending_id, 1)
    with pytest.raises(RowReconciliationBlocked, match="stale"):
        RowActionJournal(path, scope=SCOPE).begin("add", digest("A"),
                                                 digest({"school": "A School"}), 0)


def test_read_only_row_recovery_settles_effect_without_site_write(tmp_path, row_site):
    server, driver = row_site
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    desired = DesiredRow("A", {"school": "A School"})
    journal.begin("add", digest("A"), desired.values_digest, 0)
    driver.add_row(desired)  # A prior process completed the external effect.
    assert server.add_count == 1
    observed = reconciler(driver, journal).reconcile_pending_read_only()
    assert observed.revision == 1
    assert journal.pending() == ()
    assert server.add_count == 1


def test_revoked_row_lease_after_intent_never_calls_site(tmp_path, row_site):
    server, driver = row_site
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    checks = 0

    def guard():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("lease revoked")

    with pytest.raises(RuntimeError, match="lease revoked"):
        reconciler(driver, journal, guard=guard).reconcile(
            (DesiredRow("A", {"school": "A School"}),))
    assert server.add_count == 0
    assert len(journal.pending()) == 1
    with pytest.raises(RowOutcomeUnknown, match="replay disabled"):
        reconciler(driver, journal).reconcile_pending_read_only()
    assert server.add_count == 0


def test_row_write_requires_explicit_task_mutation_guard(tmp_path, row_site):
    server, driver = row_site
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    with pytest.raises(RowReconciliationBlocked, match="mutation guard unavailable"):
        reconciler(driver, journal).reconcile(
            (DesiredRow("A", {"school": "A School"}),))
    assert server.add_count == 0
    assert journal.pending() == ()


def test_row_inventory_wrong_draft_blocks_before_intent_or_site_write(tmp_path, row_site):
    server, driver = row_site
    journal = RowActionJournal(tmp_path / "private" / "rows.sqlite3", scope=SCOPE)
    server.draft_id_digest = digest("another-draft")
    with pytest.raises(RowReconciliationBlocked, match="inventory incomplete"):
        reconciler(driver, journal, guard=lambda: None).reconcile(
            (DesiredRow("A", {"school": "A School"}),))
    assert server.add_count == 0
    assert journal.pending() == ()


def test_executor_certified_education_rows_reconcile_once_and_bind_draft(
        tmp_path, monkeypatch, row_site):
    server, driver = row_site
    target = f"http://127.0.0.1:{server.server_port}/apply"
    server.target_sha256 = hashlib.sha256(target.encode()).hexdigest()
    server.full_name = ""
    server.submit_count = 0
    server.corrupt_contract = False
    server.omit_record = False
    server.lose_rows_before_final = False
    server.wrong_draft_receipt = False
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "fields": {"identity.full_name": {"value": "Synthetic Person",
                                         "confidence": 1.0}},
        "collections": {"education_records": [
            {"id": "A", "fields": {"school": {"value": "A School"}}},
            {"id": "B", "fields": {"school": {"value": "B School"}}},
        ]},
    }))

    class CertifiedRowsAdapter:
        def __init__(self, target_url):
            self.target_url = target_url

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def auth_challenge(self):
            return False

        def structured_row_contract(self, applicant_profile):
            records = applicant_profile["collections"]["education_records"]
            desired = tuple(DesiredRow(record["id"], {
                "school": record["fields"]["school"]["value"]})
                for record in records)
            if server.omit_record:
                desired = desired[:1]
            if server.corrupt_contract:
                desired = (DesiredRow("A", {"school": "Invented School"}),) + desired[1:]
            return RowExecutionContract(driver, desired, DRAFT_DIGEST,
                                        "education_records")

        def discover_fields(self):
            return [WebField(field_id="full_name", selector="#name",
                             label="Full name", required=True)]

        def apply_resolutions(self, resolutions):
            server.full_name = resolutions[0].value
            return [{"field_id": resolutions[0].field_id, "ok": True}]

        def validate(self, _plan):
            return ValidationResult(ok=server.full_name == "Synthetic Person")

        def final_submit_control(self):
            if server.lose_rows_before_final:
                server.rows.pop()
                server.revision += 1
                server.lose_rows_before_final = False
            return "Submit"

        def verify_draft_persistence(self, _plan):
            if (server.full_name != "Synthetic Person"
                    or [(row["record_id"], row["values"]) for row in server.rows] != [
                        ("A", {"school": "A School"}), ("B", {"school": "B School"})]):
                return None
            return {"verified": True, "level": "server_readback",
                    "revision": server.revision,
                    "draft_id_digest": digest("wrong-draft")
                        if server.wrong_draft_receipt else server.draft_id_digest}

        def screenshot(self, _path):
            pass

    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: CertifiedRowsAdapter(url))
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    journal_path = tmp_path / "private" / "task-rows.sqlite3"

    def run():
        runner = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                     execution_id="synthetic-task")
        runner.row_journal_path = journal_path
        return runner.run(max_pages=1)

    first = run()
    assert first.stage == ApplicationStage.READY_TO_SUBMIT
    assert first.metadata["row_reconciliation"][0]["add_count"] == 2
    assert server.add_count == 2 and server.submit_count == 0
    second = run()
    assert second.stage == ApplicationStage.READY_TO_SUBMIT
    assert second.metadata["row_reconciliation"][0]["add_count"] == 0
    assert server.add_count == 2

    server.wrong_draft_receipt = True
    wrong_receipt = run()
    assert wrong_receipt.stage == ApplicationStage.BLOCKED
    assert wrong_receipt.metadata["block_reason"] == "draft persistence unverified"
    assert server.add_count == 2 and server.submit_count == 0
    server.wrong_draft_receipt = False

    server.lose_rows_before_final = True
    lost_row = run()
    assert lost_row.stage == ApplicationStage.BLOCKED
    assert lost_row.metadata["block_reason"] == "row reconciliation unverified"
    assert server.add_count == 2 and server.submit_count == 0

    server.draft_id_digest = digest("wrong-draft")
    wrong = run()
    assert wrong.stage == ApplicationStage.BLOCKED
    assert wrong.metadata["block_reason"] == "row reconciliation unverified"
    assert outcome(wrong) == ("BLOCKED", "row_reconciliation_unverified")
    assert server.add_count == 2 and server.submit_count == 0

    server.draft_id_digest = DRAFT_DIGEST
    server.corrupt_contract = True
    invented = run()
    assert invented.stage == ApplicationStage.BLOCKED
    assert server.add_count == 2
    server.corrupt_contract = False
    server.omit_record = True
    omitted = run()
    assert omitted.stage == ApplicationStage.BLOCKED
    assert server.add_count == 2 and server.submit_count == 0

    server.omit_record = False
    changed_profile = json.loads(profile.read_text())
    changed_profile["collections"]["education_records"][0]["fields"]["degree"] = {
        "value": "Master"}
    profile.write_text(json.dumps(changed_profile))
    omitted_field = run()
    assert omitted_field.stage == ApplicationStage.BLOCKED
    assert omitted_field.metadata["block_reason"] == "row reconciliation unverified"
    assert server.add_count == 2 and server.submit_count == 0


def test_executor_certified_education_and_project_contracts_use_distinct_journals(
        tmp_path, monkeypatch, row_site):
    education, education_driver = row_site
    projects = ThreadingHTTPServer(("127.0.0.1", 0), RowSite)
    projects.rows, projects.revision, projects.complete = [], 0, True
    projects.draft_id_digest = DRAFT_DIGEST
    projects.wrong_contract = False
    projects.add_count = projects.delete_count = projects.reorder_count = 0
    threading.Thread(target=projects.serve_forever, daemon=True).start()
    try:
        project_driver = RowHttpDriver(f"http://127.0.0.1:{projects.server_port}")
        target = f"http://127.0.0.1:{education.server_port}/apply"
        target_hash = hashlib.sha256(target.encode()).hexdigest()
        education.target_sha256 = projects.target_sha256 = target_hash
        profile = tmp_path / "profile.json"
        profile.write_text(json.dumps({"fields": {"identity.full_name": {
            "value": "Synthetic Person", "confidence": 1.0}}, "collections": {
            "education_records": [{"id": "school-a", "fields": {
                "school": {"value": "School A"}}}],
            "projects": [{"id": "project-a", "fields": {
                "title": {"value": "Project A"}}}],
        }}))

        class TwoCollectionAdapter:
            def __init__(self, url):
                self.target_url = url

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def auth_challenge(self):
                return False

            def structured_row_contract(self, applicant_profile):
                return tuple(RowExecutionContract(driver, tuple(DesiredRow(
                    record["id"], {key: field["value"] for key, field in
                                    record["fields"].items()}) for record in
                    applicant_profile["collections"][key]),
                    digest("wrong-project-draft") if key == "projects" and
                    projects.wrong_contract else DRAFT_DIGEST, key)
                    for key, driver in (("education_records", education_driver),
                                        ("projects", project_driver)))

            def discover_fields(self):
                return [WebField(field_id="full_name", selector="#name",
                                 label="Full name", required=True)]

            def apply_resolutions(self, resolutions):
                return [{"field_id": item.field_id, "ok": True}
                        for item in resolutions]

            def validate(self, _plan):
                return ValidationResult(ok=True)

            def final_submit_control(self):
                return "Submit"

            def verify_draft_persistence(self, _plan):
                if ([(row["record_id"], row["values"]) for row in education.rows]
                        != [("school-a", {"school": "School A"})]
                        or [(row["record_id"], row["values"]) for row in projects.rows]
                        != [("project-a", {"title": "Project A"})]):
                    return None
                return {"verified": True, "level": "server_readback",
                        "draft_id_digest": DRAFT_DIGEST,
                        "revision": max(education.revision, projects.revision)}

            def screenshot(self, _path):
                pass

        monkeypatch.setattr("executor.application.adapter_for_url",
                            lambda url: TwoCollectionAdapter(url))
        monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
        journal = tmp_path / "private" / "task-rows.sqlite3"

        def run():
            runner = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                         execution_id="two-collections")
            runner.row_journal_path = journal
            return runner.run(max_pages=1)

        projects.wrong_contract = True
        mismatched = run()
        assert mismatched.stage == ApplicationStage.BLOCKED
        assert mismatched.metadata["block_reason"] == "row reconciliation unverified"
        assert education.add_count == projects.add_count == 0
        projects.wrong_contract = False
        assert run().stage == ApplicationStage.READY_TO_SUBMIT
        assert education.add_count == projects.add_count == 1
        assert journal.with_name("task-rows.education_records.sqlite3").exists()
        assert journal.with_name("task-rows.projects.sqlite3").exists()
        assert run().stage == ApplicationStage.READY_TO_SUBMIT
        assert education.add_count == projects.add_count == 1
    finally:
        projects.shutdown()
        projects.server_close()
