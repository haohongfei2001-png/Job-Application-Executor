"""Two attachment slots require independent server and same-draft readback."""
from __future__ import annotations

import base64
import hashlib
import json
import stat
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from executor.application import ApplicationExecutor
from executor.adapters.generic_web import GenericWebAdapter
from executor.browser import BrowserOwnershipError
from executor.forms.attachment_manifest import (AttachmentManifestError,
                                                load_attachment_manifest)
from executor.forms import (DesiredRow, RowActionJournal, RowExecutionContract, RowInventory,
                            SiteRow)
from executor.autonomy.worker import _task_attachment_asset, outcome
from executor.models import ApplicationStage, ValidationResult, WebField


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_task_attachment_reference_keeps_matching_hash_and_binds_new_bytes(tmp_path):
    original = tmp_path / "original.pdf"
    replacement = tmp_path / "replacement.pdf"
    original.write_bytes(b"original synthetic resume")
    replacement.write_bytes(b"replacement synthetic resume")
    prior = {"path": str(original), "kind": "resume", "sha256": digest("prior verified")}
    same = _task_attachment_asset("resume", str(original), prior)
    assert same["sha256"] == prior["sha256"]
    changed = _task_attachment_asset("resume", str(replacement), prior)
    assert changed["sha256"] == hashlib.sha256(replacement.read_bytes()).hexdigest()
    assert changed["sha256"] != prior["sha256"]
    assert "sha256" not in _task_attachment_asset("resume", str(tmp_path / "missing.pdf"), None)


class UploadATS(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path == "/add-row":
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.rows.append({"record_id": value["record_id"],
                                     "site_row_id": uuid.uuid4().hex,
                                     "values": value["values"]})
            self.server.row_add_count += 1
            self.server.revision += 1
            self.send_response(204)
            self.end_headers()
            return
        if self.path != "/upload":
            self.send_error(404)
            return
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        slot = value["slot"]
        if slot != getattr(self.server, "drop_slot", None):
            payload = base64.b64decode(value["bytes"])
            self.server.attachments[slot] = {
                "file_sha256": hashlib.sha256(payload).hexdigest(),
                "asset_id_digest": digest(f"server-asset-{slot}"),
            }
            self.server.revision += 1
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        if self.path == "/rows":
            body = json.dumps({"rows": self.server.rows,
                               "revision": self.server.revision,
                               "target_sha256": self.server.target_sha256,
                               "draft_id_digest": digest("server-draft-one")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/apply":
            body = b"""<!doctype html><meta charset='utf-8'><body>
              <label>Resume<input id='resume' type='file' accept='.pdf' required></label>
              <label>Photo<input id='photo' type='file' accept='.png' required></label>
              <button type='button'>Submit application</button>
              <script>
              for (const slot of ['resume', 'photo']) {
                document.getElementById(slot).addEventListener('change', async event => {
                  const file = event.target.files[0];
                  const reader = new FileReader();
                  reader.onload = async () => {
                    await fetch('/upload', {method:'POST', headers:{'Content-Type':'application/json'},
                      body:JSON.stringify({slot, bytes:String(reader.result).split(',')[1]})});
                  };
                  reader.readAsDataURL(file);
                });
              }
              </script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/draft":
            self.send_error(404)
            return
        attachments = {key: value.copy() for key, value in self.server.attachments.items()}
        if getattr(self.server, "corrupt_photo_readback", False) and "photo" in attachments:
            attachments["photo"]["file_sha256"] = digest("wrong-photo")
        body = json.dumps({"draft_id_digest": digest("server-draft-one"),
                           "revision": self.server.revision,
                           "review_token": self.server.review_token,
                           "attachments": attachments,
                           "submit_count": self.server.submit_count}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def upload_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), UploadATS)
    server.attachments, server.revision, server.submit_count = {}, 0, 0
    server.drop_slot = None
    server.corrupt_photo_readback = False
    server.rows, server.row_add_count, server.target_sha256 = [], 0, ""
    server.review_token = "synthetic-review"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


class CertifiedUploadAdapter:
    """Synthetic certified driver; generic file input remains unsupported."""
    def __init__(self, target_url, *, mismatch=False):
        self.target_url = target_url
        self.base = target_url.removesuffix("/apply")
        self.mismatch = mismatch

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def auth_challenge(self):
        return False

    def start_application(self):
        return False

    def discover_fields(self):
        return [WebField(field_id="resume", selector="#resume", label="Resume",
                         input_type="file", required=True, metadata={"accept": ".pdf"}),
                WebField(field_id="photo", selector="#photo", label="Photo",
                         input_type="file", required=True, metadata={"accept": ".png"})]

    def apply_resolutions(self, resolutions):
        actions = []
        for item in resolutions:
            slot = item.canonical_key.removeprefix("assets.")
            data = json.dumps({"slot": slot, "bytes": base64.b64encode(
                Path(item.value).read_bytes()).decode()}).encode()
            request = urllib.request.Request(self.base + "/upload", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request).read()
            actions.append({"field_id": item.field_id, "ok": True})
        return actions

    def _draft(self):
        return json.load(urllib.request.urlopen(self.base + "/draft"))

    def verify_attachment_receipts(self, _plan, selected):
        draft = self._draft()
        receipts = []
        for item in selected:
            slot = item.canonical_key.removeprefix("assets.")
            stored = draft["attachments"].get(slot)
            if stored is None:
                continue
            receipts.append({"slot": slot, "source": "server_readback",
                             "target_sha256": digest(self.target_url),
                             "draft_id_digest": draft["draft_id_digest"],
                             "draft_revision": draft["revision"],
                             "file_sha256": stored["file_sha256"],
                             "asset_id_digest": stored["asset_id_digest"],
                             "upload_complete": True, "retained_in_draft": True})
        return receipts

    def validate(self, _plan):
        return ValidationResult(ok=True)

    def final_submit_control(self):
        return "Submit"

    def verify_draft_persistence(self, _plan):
        draft = self._draft()
        return {"verified": True, "level": "server_readback",
                "revision": draft["revision"],
                "draft_id_digest": digest("wrong-draft") if self.mismatch
                                   else draft["draft_id_digest"]}

    def observe_review_draft(self, plan):
        draft = self._draft()
        expected = [item for item in plan.fields
                    if str(item.status) in {"RESOLVED", "KEEP_EXISTING"}
                    and not (item.canonical_key or "").startswith("assets.")]
        return {
            "source": "server_readback",
            "target_sha256": digest(plan.target_url),
            "draft_id_digest": draft["draft_id_digest"],
            "revision": draft["revision"],
            "account_verified": True,
            "account_identity_digest": digest(
                "identity.email:synthetic@example.test"),
            "complete_pages": True, "complete_required": True,
            "save_status": "VERIFIED", "validation_error_count": 0,
            "hidden_required_count": 0, "unverified_default_count": 0,
            "document_epoch": f"synthetic-page-{getattr(self, 'page_index', 0)}",
            "driver_version": "upload-fixture-v1",
            "fields": [
                {"index": i, "field_id": item.field_id,
                 "selector": item.selector, "required": item.required,
                 "value": draft["review_token"]
                    if item.field_id == "review-token" else None,
                 "default_confirmed": str(item.status) == "KEEP_EXISTING"}
                for i, item in enumerate(expected)],
            "attachments": {
                slot: receipt["file_sha256"] for slot, receipt
                in draft["attachments"].items()},
            "rows": {},
        }

    def screenshot(self, _path):
        pass


class TwoPageUploadAdapter(CertifiedUploadAdapter):
    def __init__(self, target_url, server, *, lose_photo):
        super().__init__(target_url)
        self.server = server
        self.page_index = 0
        self.lose_photo = lose_photo

    def discover_fields(self):
        return super().discover_fields() if self.page_index == 0 else []

    def next_control(self):
        return self.page_index == 0

    def final_submit_control(self):
        return "Submit" if self.page_index == 1 else None

    def advance(self):
        if self.page_index != 0:
            return False
        self.page_index = 1
        if self.lose_photo:
            self.server.attachments.pop("photo", None)
            self.server.revision += 1
        return True


class BrowserUploadAdapter(GenericWebAdapter):
    """Synthetic site-specific upload driver using actual browser file inputs."""
    def __init__(self, target_url):
        super().__init__(target_url)
        self.base = target_url.removesuffix("/apply")
        self.mismatch = False

    _draft = CertifiedUploadAdapter._draft
    verify_attachment_receipts = CertifiedUploadAdapter.verify_attachment_receipts
    verify_draft_persistence = CertifiedUploadAdapter.verify_draft_persistence
    observe_review_draft = CertifiedUploadAdapter.observe_review_draft

    def apply_resolutions(self, resolutions):
        actions = []
        for item in resolutions:
            slot = item.canonical_key.removeprefix("assets.")
            before = self._draft()["revision"]
            self.mutation_guard()
            try:
                self.page.locator(item.selector).set_input_files(item.value)
            except Exception:
                raise BrowserOwnershipError("upload selection outcome unknown") from None
            deadline = time.monotonic() + 3
            while self._draft()["revision"] <= before and time.monotonic() < deadline:
                time.sleep(.02)
            actions.append({"field_id": item.field_id, "ok": True})
        return actions

    def validate(self, _plan):
        return ValidationResult(ok=True)


def test_two_uploads_need_retained_same_draft_receipts(tmp_path, monkeypatch, upload_site):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 synthetic canary")
    photo.write_bytes(b"\x89PNG synthetic canary")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.email": {
        "value": "synthetic@example.test"}}, "assets": {
        "resume": {"path": str(resume), "kind": "resume_pdf",
                   "sha256": hashlib.sha256(resume.read_bytes()).hexdigest()},
        "photo": {"path": str(photo), "kind": "photo_png",
                  "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
    }}))
    target = f"http://127.0.0.1:{upload_site.server_port}/apply"
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: CertifiedUploadAdapter(url))
    ready = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert ready.stage == ApplicationStage.READY_TO_SUBMIT, ready.metadata.get("block_reason")
    assert ready.metadata["attachment_persistence"] == {
        "verified": True, "slot_count": 2, "minimum_draft_revision": 2,
        "level": "server_readback"}
    assert upload_site.submit_count == 0


    upload_site.attachments.clear()
    upload_site.revision = 0
    upload_site.drop_slot = "photo"
    missing = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert missing.stage == ApplicationStage.BLOCKED
    assert missing.metadata["block_reason"] == "attachment draft receipt unverified"
    assert outcome(missing) == ("BLOCKED", "attachment_persistence_unverified")
    assert upload_site.submit_count == 0

    upload_site.attachments.clear()
    upload_site.revision = 0
    upload_site.drop_slot = None
    upload_site.corrupt_photo_readback = True
    wrong_bytes = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert wrong_bytes.stage == ApplicationStage.BLOCKED
    assert wrong_bytes.metadata["block_reason"] == "attachment draft receipt unverified"
    assert upload_site.submit_count == 0

    upload_site.attachments.clear()
    upload_site.revision = 0
    upload_site.corrupt_photo_readback = False
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: CertifiedUploadAdapter(url, mismatch=True))
    wrong_draft = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert wrong_draft.stage == ApplicationStage.BLOCKED
    assert wrong_draft.metadata["block_reason"] == "draft persistence unverified"
    assert upload_site.submit_count == 0


@pytest.mark.parametrize("before_navigation", [False, True])
def test_draft_readback_exception_blocks_without_claiming_save(
        tmp_path, monkeypatch, upload_site, before_navigation):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 readback failure")
    photo.write_bytes(b"\x89PNG readback failure")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.email": {
        "value": "synthetic@example.test"}}, "assets": {
        "resume": {"path": str(resume), "sha256": hashlib.sha256(resume.read_bytes()).hexdigest()},
        "photo": {"path": str(photo), "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
    }}))
    target = f"http://127.0.0.1:{upload_site.server_port}/apply"
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")

    class BrokenReadback(TwoPageUploadAdapter if before_navigation else CertifiedUploadAdapter):
        def verify_draft_persistence(self, _plan):
            raise TimeoutError("synthetic private draft payload")

    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: BrokenReadback(url, upload_site, lose_photo=False)
                        if before_navigation else BrokenReadback(url))
    plan = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(
        max_pages=2 if before_navigation else 1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "draft persistence unverified"
    assert plan.metadata["draft_persistence"] == "UNVERIFIED"
    assert outcome(plan) == ("BLOCKED", "draft_persistence_unverified")
    assert "synthetic private draft payload" not in repr(plan.metadata)
    assert upload_site.submit_count == 0


def test_next_page_rechecks_earlier_attachments_before_ready(tmp_path, monkeypatch, upload_site):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 earlier page")
    photo.write_bytes(b"\x89PNG earlier page")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.email": {
        "value": "synthetic@example.test"}}, "assets": {
        "resume": {"path": str(resume), "kind": "resume_pdf",
                   "sha256": hashlib.sha256(resume.read_bytes()).hexdigest()},
        "photo": {"path": str(photo), "kind": "photo_png",
                  "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
    }}))
    target = f"http://127.0.0.1:{upload_site.server_port}/apply"
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: TwoPageUploadAdapter(url, upload_site,
                                                         lose_photo=True))
    lost = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=2)
    assert lost.stage == ApplicationStage.BLOCKED
    assert lost.metadata["block_reason"] == "attachment draft receipt unverified"
    assert upload_site.submit_count == 0

    upload_site.attachments.clear()
    upload_site.revision = 0
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: TwoPageUploadAdapter(url, upload_site,
                                                         lose_photo=False))
    retained = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=2)
    assert retained.stage == ApplicationStage.READY_TO_SUBMIT, retained.metadata.get("block_reason")
    assert retained.metadata["attachment_persistence"]["slot_count"] == 2
    assert upload_site.submit_count == 0


def test_real_browser_file_inputs_require_independent_upload_oracle(tmp_path, monkeypatch, upload_site):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 browser upload")
    photo.write_bytes(b"\x89PNG browser upload")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.email": {
        "value": "synthetic@example.test"}}, "assets": {
        "resume": {"path": str(resume), "kind": "resume_pdf",
                   "sha256": hashlib.sha256(resume.read_bytes()).hexdigest()},
        "photo": {"path": str(photo), "kind": "photo_png",
                  "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
    }}))
    target = f"http://127.0.0.1:{upload_site.server_port}/apply"
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: BrowserUploadAdapter(url))
    ready = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert ready.stage == ApplicationStage.READY_TO_SUBMIT
    assert set(upload_site.attachments) == {"resume", "photo"}
    assert upload_site.attachments["resume"]["file_sha256"] == hashlib.sha256(resume.read_bytes()).hexdigest()
    assert upload_site.attachments["photo"]["file_sha256"] == hashlib.sha256(photo.read_bytes()).hexdigest()
    assert upload_site.submit_count == 0

    upload_site.attachments.clear()
    upload_site.revision = 0
    upload_site.drop_slot = "photo"
    dropped = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert dropped.stage == ApplicationStage.BLOCKED
    assert dropped.metadata["block_reason"] == "attachment draft receipt unverified"
    assert "photo" not in upload_site.attachments
    assert upload_site.submit_count == 0


def test_restart_uses_private_attachment_manifest_without_reupload(tmp_path, monkeypatch, upload_site):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 restart")
    photo.write_bytes(b"\x89PNG restart")
    profile = tmp_path / "profile.json"
    assets = {
        "resume": {"path": str(resume), "kind": "resume_pdf",
                   "sha256": hashlib.sha256(resume.read_bytes()).hexdigest()},
        "photo": {"path": str(photo), "kind": "photo_png",
                  "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
    }
    profile.write_text(json.dumps({"fields": {"identity.email": {
        "value": "synthetic@example.test"}}, "assets": assets}))
    target = f"http://127.0.0.1:{upload_site.server_port}/apply"
    manifest_path = tmp_path / "private" / "task-attachment.json"
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: TwoPageUploadAdapter(url, upload_site,
                                                         lose_photo=False))
    first = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                execution_id="synthetic-task")
    first.attachment_manifest_path = manifest_path
    assert first.run(max_pages=1).stage == ApplicationStage.BLOCKED
    assert set(upload_site.attachments) == {"resume", "photo"}
    raw_manifest = manifest_path.read_text()
    assert str(resume) not in raw_manifest and str(photo) not in raw_manifest
    assert "server-draft-one" not in raw_manifest
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest_path.parent.stat().st_mode) == 0o700
    assert load_attachment_manifest(manifest_path, target_url=target,
                                    execution_id="synthetic-task", assets=assets)
    with pytest.raises(AttachmentManifestError, match="scope"):
        load_attachment_manifest(manifest_path, target_url=target,
                                 execution_id="another-task", assets=assets)
    changed_assets = {**assets, "photo": {**assets["photo"], "sha256": digest("changed")}}
    with pytest.raises(AttachmentManifestError, match="canonical hash"):
        load_attachment_manifest(manifest_path, target_url=target,
                                 execution_id="synthetic-task", assets=changed_assets)

    def at_page_two(url):
        adapter = TwoPageUploadAdapter(url, upload_site, lose_photo=False)
        adapter.page_index = 1
        return adapter

    monkeypatch.setattr("executor.application.adapter_for_url", at_page_two)
    second = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                 execution_id="synthetic-task")
    second.attachment_manifest_path = manifest_path
    before = upload_site.revision
    assert second.run(max_pages=1).stage == ApplicationStage.READY_TO_SUBMIT
    assert upload_site.revision == before  # Read-only restart, no second upload.
    assert upload_site.submit_count == 0

    upload_site.attachments.pop("photo")
    upload_site.revision += 1
    missing = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                  execution_id="synthetic-task")
    missing.attachment_manifest_path = manifest_path
    assert missing.run(max_pages=1).metadata["block_reason"] == "attachment draft receipt unverified"

    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: TwoPageUploadAdapter(url, upload_site,
                                                         lose_photo=False))
    replay = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                 execution_id="synthetic-task")
    replay.attachment_manifest_path = manifest_path
    before = upload_site.revision
    assert replay.run(max_pages=1).stage == ApplicationStage.BLOCKED
    assert upload_site.revision == before


def test_browser_upload_and_rows_share_one_draft_across_pages_and_restart(
        tmp_path, monkeypatch, upload_site):
    """Actual isolated file controls and a non-idempotent row API share one draft."""
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 integrated fixture")
    photo.write_bytes(b"\x89PNG integrated fixture")
    assets = {slot: {"path": str(path), "kind": kind,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
              for slot, path, kind in (("resume", resume, "resume_pdf"),
                                       ("photo", photo, "photo_png"))}
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.email": {
        "value": "synthetic@example.test"}}, "assets": assets, "collections": {
        "education_records": [
            {"id": "school-a", "fields": {"school": {"value": "School A"}}},
            {"id": "school-b", "fields": {"school": {"value": "School B"}}},
        ]}}))
    target = f"http://127.0.0.1:{upload_site.server_port}/apply"
    upload_site.target_sha256 = digest(target)
    base = f"http://127.0.0.1:{upload_site.server_port}"

    class RowsOnDraft:
        capabilities = frozenset({"add"})

        def read_rows(self):
            data = json.load(urllib.request.urlopen(base + "/rows"))
            return RowInventory(data["revision"], True, tuple(SiteRow(
                row["record_id"], row["site_row_id"], hashlib.sha256(
                    json.dumps(row["values"], sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode()).hexdigest(), True)
                for row in data["rows"]), data["target_sha256"],
                data["draft_id_digest"])

        def add_row(self, row):
            request = urllib.request.Request(base + "/add-row", data=json.dumps({
                "record_id": row.record_id, "values": dict(row.values)}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request).read()

    class IntegratedAdapter(BrowserUploadAdapter):
        def __init__(self, url, *, start_page=0, lose_row=False):
            super().__init__(url)
            self.page_index = start_page
            self.lose_row = lose_row

        def __enter__(self):
            super().__enter__()
            if self.page_index == 1:
                self._show_review_page()
            return self

        def _show_review_page(self):
            self.page.locator("input[type=file]").evaluate_all(
                "items => items.forEach(item => item.closest('label').remove())")
            self.page.evaluate("""() => {
                const label = document.createElement('label');
                label.textContent = 'Review token';
                const input = document.createElement('input');
                input.id = 'review-token';
                input.value = 'synthetic-review';
                label.appendChild(input);
                document.body.appendChild(label);
            }""")

        def apply_resolutions(self, resolutions):
            if self.page_index == 1:
                return []
            return super().apply_resolutions(resolutions)

        def structured_row_contract(self, applicant_profile):
            records = applicant_profile["collections"]["education_records"]
            desired = tuple(DesiredRow(record["id"], {
                "school": record["fields"]["school"]["value"]})
                for record in records)
            driver = RowsOnDraft()
            if self.page_index == 1:
                driver.capabilities = frozenset()  # Returning page is read-only.
            return RowExecutionContract(driver, desired, digest("server-draft-one"),
                                        "education_records", "synthetic-browser-v1")

        def next_control(self):
            return self.page_index == 0

        def final_submit_control(self):
            return "Submit" if self.page_index == 1 else None

        def advance(self):
            if self.page_index != 0:
                return False
            self.page_index = 1
            self._show_review_page()
            if self.lose_row:
                upload_site.rows.pop()
                upload_site.revision += 1
            return True

        def observe_review_draft(self, plan):
            snapshot = super().observe_review_draft(plan)
            observed = json.load(urllib.request.urlopen(base + "/rows"))
            snapshot["rows"] = {"education_records": [
                {"record_id": row["record_id"],
                 "values_digest": hashlib.sha256(json.dumps(
                     row["values"], sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode()).hexdigest()}
                for row in observed["rows"]]}
            return snapshot

    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: IntegratedAdapter(url))
    manifest = tmp_path / "private" / "attachments.json"
    journal = tmp_path / "private" / "rows.sqlite3"

    def run(pages=2):
        runner = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}},
                                     execution_id="integrated-task")
        runner.attachment_manifest_path = manifest
        runner.row_journal_path = journal
        return runner.run(max_pages=pages)

    first = run()
    assert first.stage == ApplicationStage.READY_TO_SUBMIT, first.metadata.get("block_reason")
    assert [(row["record_id"], row["values"]) for row in upload_site.rows] == [
        ("school-a", {"school": "School A"}),
        ("school-b", {"school": "School B"})]
    assert set(upload_site.attachments) == {"resume", "photo"}
    assert upload_site.row_add_count == 2 and upload_site.submit_count == 0
    collection_journal = journal.with_name("rows.education_records.sqlite3")
    assert "School A" not in collection_journal.read_bytes().decode(errors="replace")

    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: IntegratedAdapter(url, start_page=1))
    before = upload_site.revision
    returned = run(pages=1)
    assert returned.stage == ApplicationStage.READY_TO_SUBMIT
    assert upload_site.revision == before
    assert upload_site.row_add_count == 2 and upload_site.submit_count == 0

    def missing_row_driver(url):
        adapter = IntegratedAdapter(url, start_page=1)
        adapter.structured_row_contract = None
        return adapter

    monkeypatch.setattr("executor.application.adapter_for_url", missing_row_driver)
    missing_contract = run(pages=1)
    assert missing_contract.stage == ApplicationStage.BLOCKED
    assert missing_contract.metadata["block_reason"] == "row reconciliation unverified"
    assert upload_site.row_add_count == 2 and upload_site.submit_count == 0
    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: IntegratedAdapter(url, start_page=1))

    project_journal = journal.with_name("rows.projects.sqlite3")
    RowActionJournal(project_journal, scope="prior-project-collection")
    unreconciled_collection = run(pages=1)
    assert unreconciled_collection.stage == ApplicationStage.BLOCKED
    assert unreconciled_collection.metadata["block_reason"] == "row reconciliation unverified"
    assert upload_site.row_add_count == 2 and upload_site.submit_count == 0
    project_journal.unlink()

    upload_site.rows.pop()
    upload_site.revision += 1
    lost = run(pages=1)
    assert lost.stage == ApplicationStage.BLOCKED
    assert lost.metadata["block_reason"] == "row reconciliation unverified"
    assert upload_site.row_add_count == 2 and upload_site.submit_count == 0
