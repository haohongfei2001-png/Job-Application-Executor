"""Two attachment slots require independent server and same-draft readback."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from executor.application import ApplicationExecutor
from executor.adapters.generic_web import GenericWebAdapter
from executor.browser import BrowserOwnershipError
from executor.autonomy.worker import outcome
from executor.models import ApplicationStage, ValidationResult, WebField


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class UploadATS(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
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
    profile.write_text(json.dumps({"assets": {
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
    assert ready.stage == ApplicationStage.READY_TO_SUBMIT
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


def test_next_page_rechecks_earlier_attachments_before_ready(tmp_path, monkeypatch, upload_site):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 earlier page")
    photo.write_bytes(b"\x89PNG earlier page")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"assets": {
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
    assert retained.stage == ApplicationStage.READY_TO_SUBMIT
    assert retained.metadata["attachment_persistence"]["slot_count"] == 2
    assert upload_site.submit_count == 0


def test_real_browser_file_inputs_require_independent_upload_oracle(tmp_path, monkeypatch, upload_site):
    resume, photo = tmp_path / "resume.pdf", tmp_path / "photo.png"
    resume.write_bytes(b"%PDF-1.4 browser upload")
    photo.write_bytes(b"\x89PNG browser upload")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"assets": {
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
