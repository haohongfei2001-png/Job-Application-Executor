"""Private future-task editor: bounded transport, immutable versions and CAS."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import threading
from email.message import Message

import pytest

from executor import settings
from executor.autonomy import profile_editor as editor
from executor.autonomy.manager import ManagerController
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker


def canonical():
    return {"schema_version": "1.0", "private_extension": {"unchanged": [1, False, "PRIVATE_METADATA"]},
            "fields": {
                "identity.full_name": {"value": "PRIVATE_NAME", "extension": ["PRIVATE_EXTENSION"],
                    "sources": [{"kind": "resume_pdf", "path": "/PRIVATE_SOURCE", "extra": [7]}],
                    "normalization": {"constraint": {"min": 2}}, "confidence": 0.3,
                    "user_confirmed": False, "aliases": ["姓名"], "sensitive": True},
                "identity.phone": {"value": "PRIVATE_PHONE"},
                "identity.id_number": {"value": "PRIVATE_ID", "constraints": {"never_edit": True}},
                "language.cet6.score": {"value": 520},
                "preferences.preferred_cities": {"value": ["武汉", "上海"]},
            },
            "collections": {"projects": [{"name": "PRIVATE_PROJECT"}], "profile_write_version": 4},
            "source_documents": [{"kind": "resume_pdf", "path": "/PRIVATE_OLD_RESUME", "extra": True}],
            "assets": {"other": {"path": "/PRIVATE_OTHER", "kind": "other", "extra": [1]}}}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    authority = tmp_path / "private"
    authority.mkdir(mode=0o700)
    path = authority / "prior.json"
    raw = json.dumps(canonical(), ensure_ascii=False, separators=(",", ":")).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    resume = authority / "prior.pdf"
    resume.write_bytes(b"PRIVATE_PRIOR_RESUME\x00opaque")
    resume.chmod(0o400)
    obj = canonical()
    obj["assets"]["resume"] = {"path": str(resume), "kind": "resume_pdf",
                              "sha256": hashlib.sha256(resume.read_bytes()).hexdigest(),
                              "source": {"kind": "resume_pdf", "extra": "PRIVATE_OLD_EVIDENCE"},
                              "extension": {"keep": True}}
    path.write_bytes(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode())
    monkeypatch.setattr(settings, "PATH", authority / "settings.json")
    monkeypatch.setattr(settings, "PACKAGED", True)
    original = {"profile_path": str(path), "resume_path": "/PRIVATE_LEGACY_RESUME",
                "unknown_preference": {"keep": [True]}, "deepseek": {"enabled": False}}
    settings.save_settings(original)
    return authority, path, resume, original


def metadata(view=None, **changes):
    if view is None:
        _, view = editor.editor_state()
    obj = {"schema_version": 1, "expected_settings_version": view["settings_version"],
           "expected_profile_version": view["profile_version"], "edits": {},
           "resume_action": "keep", "resume_kind": None}
    obj.update(changes)
    return obj


def multipart(data, payload=None, *, filename="../../不可信.pdf", mime="application/octet-stream", boundary="boundary_test"):
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
            'Content-Type: application/json\r\n\r\n').encode() + json.dumps(data, ensure_ascii=False).encode()
    if payload is not None:
        body += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="resume"; filename="{filename}"\r\n'
                 f'Content-Type: {mime}\r\n\r\n').encode() + payload
    body += f'\r\n--{boundary}--\r\n'.encode()
    headers = Message()
    headers["Content-Length"] = str(len(body))
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    return headers, body


def snapshot(path):
    return path.read_bytes(), path.stat().st_mode, path.stat().st_ino


def supervisor_for(root, current):
    queue = TaskQueue(root.resolve() / "runtime")
    worker = Worker(queue, settings=copy.deepcopy(current))
    manager = ManagerController(queue, worker, settings=copy.deepcopy(current))
    return Supervisor(queue, worker=worker, manager=manager, token="s" * 40)


def test_editor_projection_hides_id_paths_extensions_and_uses_exact_bytes(configured):
    authority, path, resume, original = configured
    before = {x.name: snapshot(x) for x in authority.iterdir()}
    current, view = editor.editor_state()
    assert current == original
    assert view["profile_version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert view["settings_version"] == settings.settings_version(original)
    assert view["mode"] == "canonical" and view["future_tasks_only"] is True
    assert view["submit_capability"] is False
    assert len(view["fields"]) == 12
    projected = json.dumps(view)
    for canary in ("PRIVATE_ID", "PRIVATE_SOURCE", "PRIVATE_OTHER", "PRIVATE_OLD_EVIDENCE",
                   "PRIVATE_METADATA", "PRIVATE_EXTENSION", "PRIVATE_PROJECT", str(path), str(resume)):
        assert canary not in projected
    assert "PRIVATE_NAME" in projected and "PRIVATE_PHONE" in projected
    assert {x.name: snapshot(x) for x in authority.iterdir()} == before


def test_patch_and_opaque_resume_preserve_all_prior_bytes_and_metadata(configured):
    authority, path, resume, original = configured
    path.chmod(0o400)
    old_profile, old_resume = snapshot(path), snapshot(resume)
    old = json.loads(path.read_bytes())
    data = metadata(edits={"identity.full_name": "新名字", "identity.phone": None},
                    resume_action="replace", resume_kind="resume_docx")
    opaque = b"MZ\x00not a docx; never parse or execute\xffPRIVATE_NEW_RESUME"
    current, view = editor.save_profile(data, opaque)
    updated_path = Path(current["profile_path"])
    updated = json.loads(updated_path.read_bytes())
    assert snapshot(path) == old_profile and snapshot(resume) == old_resume
    assert current == {**original, "profile_path": str(updated_path)}
    assert view["profile_version"] == hashlib.sha256(updated_path.read_bytes()).hexdigest()
    assert updated_path != path and updated_path.parent == authority
    assert stat.S_IMODE(updated_path.stat().st_mode) == 0o600
    for key in ("private_extension", "collections", "source_documents"):
        assert updated[key] == old[key]
    for key in old["fields"].keys() - data["edits"].keys():
        assert updated["fields"][key] == old["fields"][key]
    changed = updated["fields"]["identity.full_name"]
    assert changed["value"] == "新名字" and changed["user_confirmed"] is True
    for key in old["fields"]["identity.full_name"].keys() - {"value", "sources", "user_confirmed", "last_verified"}:
        assert changed[key] == old["fields"]["identity.full_name"][key]
    assert changed["sources"][:-1] == old["fields"]["identity.full_name"]["sources"]
    assert changed["sources"][-1]["kind"] == "user_explicit"
    assert "path" not in changed["sources"][-1]
    assert updated["fields"]["identity.phone"]["value"] is None
    assert updated["assets"]["other"] == old["assets"]["other"]
    histories = [value for key, value in updated.items() if key.startswith("resume_previous_")]
    assert set(updated["assets"]) == set(old["assets"])
    assert histories == [old["assets"]["resume"]]
    selected_resume = Path(updated["assets"]["resume"]["path"])
    assert selected_resume.parent == authority and selected_resume.name.startswith("resume-")
    assert selected_resume.suffix == ".docx" and selected_resume.read_bytes() == opaque
    assert stat.S_IMODE(selected_resume.stat().st_mode) == 0o600
    assert updated["assets"]["resume"]["sha256"] == hashlib.sha256(opaque).hexdigest()


def test_new_empty_authority_and_explicit_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path.resolve() / "missing" / "settings.json")
    monkeypatch.setattr(settings, "PACKAGED", True)
    current, view = editor.editor_state()
    assert view["mode"] == "new" and view["profile_version"] is None
    selected, saved = editor.save_profile(metadata(view, edits={"identity.email": "a@example.test"}))
    new = json.loads(Path(selected["profile_path"]).read_bytes())
    assert new["fields"]["identity.email"]["value"] == "a@example.test"
    assert "identity.phone" not in new["fields"]
    assert saved["mode"] == "canonical"


def test_legacy_remains_import_only_without_overlay(configured):
    _, path, _, original = configured
    legacy = {"identity": {"full_name": "PRIVATE_LEGACY"}, "education": {"school": "OLD"}}
    path.write_text(json.dumps(legacy))
    before = snapshot(path)
    _, view = editor.editor_state()
    assert view["mode"] == "legacy" and view["fields"] == []
    assert "PRIVATE_LEGACY" not in json.dumps(view)
    with pytest.raises(editor.EditorInvalid):
        editor.save_profile(metadata(view, edits={"identity.full_name": "changed"}))
    assert snapshot(path) == before and settings.load_settings() == original


@pytest.mark.parametrize("key,value", [("identity.gender", 7), ("identity.phone", False),
    ("language.cet6.score", {"PRIVATE_UNSUPPORTED": 1}), ("preferences.preferred_cities", "Wuhan"),
    ("identity.full_name", ["PRIVATE_UNSUPPORTED"]), ("identity.phone", []), ("identity.email", "x" * 2049),
    ("language.cet6.score", 10 ** 1000)])
def test_unsupported_values_hidden_and_preserved_on_unrelated_edits(configured, key, value):
    _, path, _, _ = configured
    profile = json.loads(path.read_bytes())
    profile["fields"][key] = {"value": value, "extension": [False]}
    path.write_text(json.dumps(profile))
    _, view = editor.editor_state()
    item = next(x for x in view["fields"] if x["key"] == key)
    assert item["status"] == "unsupported" and item["editable"] is False and item["value"] is None
    with pytest.raises(editor.EditorInvalid):
        editor.save_profile(metadata(view, edits={key: None}))
    selected, _ = editor.save_profile(metadata(view))
    assert json.loads(Path(selected["profile_path"]).read_bytes())["fields"][key] == profile["fields"][key]


def test_future_task_binding_changes_without_touching_old_task(configured, tmp_path):
    _, path, _, original = configured
    supervisor = supervisor_for(tmp_path, original)
    queue = supervisor.queue
    old = queue.enqueue(TaskSpec(company="Synthetic", role="Old", target_url="https://old.example.test",
                                 profile_ref=str(path), live_authorized=False))
    before = queue.get(old["task_id"])
    old_bytes = snapshot(path)
    result = supervisor.configure_profile_editor(metadata(edits={"identity.full_name": "NEXT"}), None)
    new = queue.enqueue(TaskSpec(company="Synthetic", role="New", target_url="https://new.example.test",
                                 profile_ref=supervisor.manager._profile_ref(), live_authorized=False))
    assert new["spec"]["profile_ref"] != old["spec"]["profile_ref"]
    assert queue.get(old["task_id"]) == before and snapshot(path) == old_bytes
    assert supervisor.worker.settings == supervisor.manager.settings == settings.load_settings()
    assert result["save_status"] == "saved" and result["submit_capability"] is False


@pytest.mark.parametrize("change", ["settings", "profile", "format"])
def test_stale_settings_profile_or_exact_source_bytes_conflict(configured, change):
    _, path, _, original = configured
    request = metadata(edits={"identity.full_name": "SHOULD_NOT_PUBLISH"})
    if change == "settings":
        settings.save_settings({**original, "new_preference": True})
    elif change == "profile":
        obj = json.loads(path.read_bytes()); obj["fields"]["identity.phone"]["value"] = "PROMOTED"
        path.write_text(json.dumps(obj))
    else:
        path.write_bytes(path.read_bytes() + b"\n")
    expected = settings.load_settings()
    with pytest.raises(editor.EditorConflict):
        editor.save_profile(request)
    assert settings.load_settings() == expected


def test_two_simultaneous_saves_have_one_winner(configured):
    request = metadata(edits={"identity.full_name": "ONLY_ONCE"})
    barrier = threading.Barrier(2)
    outcomes = []
    def run():
        barrier.wait()
        try:
            editor.save_profile(request)
            outcomes.append("saved")
        except editor.EditorConflict:
            outcomes.append("conflict")
    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(5)
    assert sorted(outcomes) == ["conflict", "saved"]


@pytest.mark.parametrize("kind", ["profile", "settings"])
def test_busy_writer_locks_fail_without_waiting(configured, kind):
    authority, path, _, _ = configured
    request = metadata()
    lock = path.with_name(path.name + ".lock")
    lock.touch(mode=0o600)
    descriptor = os.open(lock if kind == "profile" else authority, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(editor.EditorConflict):
            editor.save_profile(request)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "mode", "directory", "fifo"])
@pytest.mark.parametrize("target", ["profile", "lock"])
def test_unsafe_source_or_writer_lock_is_not_repaired(configured, fault, target):
    authority, path, _, original = configured
    request = metadata()
    subject = path if target == "profile" else path.with_name(path.name + ".lock")
    other = authority / "other"
    other.write_bytes(b"PRIVATE_TARGET"); other.chmod(0o600)
    if subject.exists(): subject.unlink()
    if fault == "symlink": subject.symlink_to(other)
    elif fault == "hardlink": os.link(other, subject)
    elif fault == "mode": subject.write_bytes(b"PRIVATE_PUBLIC"); subject.chmod(0o644)
    elif fault == "directory": subject.mkdir(mode=0o700)
    else: os.mkfifo(subject, 0o600)
    before = subject.lstat()
    untouched = snapshot(other)
    with pytest.raises((editor.EditorInvalid, editor.EditorConflict)):
        editor.save_profile(request)
    assert subject.lstat().st_ino == before.st_ino and subject.lstat().st_mode == before.st_mode
    assert snapshot(other) == untouched and settings.load_settings() == original


@pytest.mark.parametrize("part", ["profile_parent", "settings_parent"])
def test_ancestor_symlink_and_public_authority_rejected(configured, part, tmp_path, monkeypatch):
    authority, path, _, original = configured
    request = metadata()
    alias = tmp_path.resolve() / "alias"; alias.symlink_to(authority, target_is_directory=True)
    if part == "settings_parent":
        monkeypatch.setattr(settings, "PATH", alias / "settings.json")
    else:
        settings.save_settings({**original, "profile_path": str(alias / path.name)})
        request["expected_settings_version"] = settings.settings_version(settings.load_settings())
    with pytest.raises((editor.EditorInvalid, editor.EditorConflict)):
        editor.save_profile(request)


@pytest.mark.parametrize("key,value", [("schema_version", True), ("schema_version", 2),
    ("expected_settings_version", "f" * 63), ("expected_profile_version", "bad"),
    ("edits", {"identity.id_number": "FORBIDDEN"}), ("edits", {"unknown": None}),
    ("edits", {"identity.phone": 1}), ("edits", {"language.cet6.score": True}),
    ("edits", {"language.cet6.score": float("nan")}), ("edits", {"identity.full_name": "x" * 2049}),
    ("edits", {"preferences.preferred_cities": [3]}), ("edits", {"identity.email": "bad\x00"}),
    ("resume_action", "delete"), ("resume_kind", "resume_pdf"), ("extra", "PRIVATE_EXTRA")])
def test_strict_metadata_rejects_unsupported_or_ambiguous_edits(configured, key, value):
    request = metadata(); request[key] = value
    with pytest.raises(editor.EditorInvalid): editor.save_profile(request)


def test_multipart_opaque_file_and_advisory_filename(configured):
    request = metadata(resume_action="replace", resume_kind="resume_doc")
    payload = b"\x00PK\x03\x04not parsed\xffPRIVATE_OPAQUE"
    headers, body = multipart(request, payload, mime="text/plain")
    parsed, actual = editor.read_save_request(headers, io.BytesIO(body))
    assert parsed == request and actual == payload
    selected, _ = editor.save_profile(parsed, actual)
    profile = json.loads(Path(selected["profile_path"]).read_bytes())
    asset_path = Path(profile["assets"]["resume"]["path"])
    assert asset_path.suffix == ".doc" and asset_path.read_bytes() == payload


@pytest.mark.parametrize("header,value", [("Content-Length", "1"), ("Content-Type", "application/json"),
    ("Transfer-Encoding", "chunked"), ("Content-Encoding", "identity"),
    ("Content-Transfer-Encoding", "binary")])
def test_transport_ambiguities_reject_before_read(configured, header, value):
    headers, _ = multipart(metadata())
    headers[header] = value
    class NeverRead:
        def read(self, _): raise AssertionError("body must not be read")
    with pytest.raises(editor.EditorInvalid): editor.read_save_request(headers, NeverRead())


@pytest.mark.parametrize("length", [str(editor.BODY_LIMIT + 1), "-1", "+1", "01", "1,1", "", " " * 100])
def test_declared_length_rejected_before_read(configured, length):
    headers, _ = multipart(metadata())
    del headers["Content-Length"]; headers["Content-Length"] = length
    class NeverRead:
        def read(self, _): raise AssertionError("body must not be read")
    with pytest.raises(editor.EditorInvalid): editor.read_save_request(headers, NeverRead())


def test_total_body_exact_ceiling_and_chunk_budget(configured):
    request = metadata(resume_action="replace", resume_kind="resume_pdf")
    _, empty = multipart(request, b"")
    headers, body = multipart(request, b"x" * (editor.BODY_LIMIT - len(empty)))
    assert len(body) == editor.BODY_LIMIT
    class Counted(io.BytesIO):
        requested = []
        def read(self, amount):
            self.requested.append(amount)
            assert amount <= editor.CHUNK_SIZE
            assert self.tell() + amount <= editor.BODY_LIMIT
            return super().read(amount)
    stream = Counted(body)
    _, payload = editor.read_save_request(headers, stream)
    assert len(payload) == editor.BODY_LIMIT - len(empty)
    assert sum(stream.requested) == editor.BODY_LIMIT
    headers.replace_header("Content-Length", str(editor.BODY_LIMIT + 1))
    with pytest.raises(editor.EditorInvalid): editor.read_save_request(headers, Counted(body + b"x"))


@pytest.mark.parametrize("mode", ["short", "overread", "wrong_type"])
def test_stream_actual_bytes_and_incomplete_reads_rejected(configured, mode):
    headers, body = multipart(metadata())
    class BadStream:
        def read(self, size):
            if mode == "short": return b""
            if mode == "overread": return b"x" * (size + 1)
            return "not bytes"
    with pytest.raises(editor.EditorInvalid): editor.read_save_request(headers, BadStream())


@pytest.mark.parametrize("fault", ["unknown_part", "duplicate_metadata", "duplicate_header", "unknown_header",
    "nested_multipart", "no_final", "epilogue", "preamble", "duplicate_json", "duplicate_json_nested",
    "metadata_too_large", "header_too_large", "no_metadata", "unexpected_resume", "missing_resume", "zero_resume"])
def test_multipart_structural_ambiguities_fail(configured, fault):
    request = metadata()
    headers, body = multipart(request)
    if fault == "unknown_part": body = body.replace(b'name="metadata"', b'name="other"')
    elif fault == "duplicate_metadata":
        part = body[:-len(b'\r\n--boundary_test--\r\n')]
        body = part + b'\r\n' + part + b'\r\n--boundary_test--\r\n'
    elif fault == "duplicate_header": body = body.replace(b'Content-Type: application/json', b'Content-Type: application/json\r\nContent-Type: application/json')
    elif fault == "unknown_header": body = body.replace(b'Content-Type: application/json', b'Content-Type: application/json\r\nX-Ignored: no')
    elif fault == "nested_multipart": headers, body = multipart(metadata(resume_action="replace", resume_kind="resume_pdf"), b"x", mime="multipart/mixed")
    elif fault == "no_final": body = body[:-2]
    elif fault == "epilogue": body += b"extra"
    elif fault == "preamble": body = b"extra" + body
    elif fault == "duplicate_json": body = body.replace(b'{"schema_version": 1,', b'{"schema_version": 1, "schema_version": 1,')
    elif fault == "duplicate_json_nested": body = body.replace(b'"edits": {}', b'"edits": {"identity.phone":null,"identity.phone":null}')
    elif fault == "metadata_too_large": body = body.replace(b'"edits": {}', b'"edits": {"identity.phone":"' + b'x' * editor.METADATA_LIMIT + b'"}')
    elif fault == "header_too_large": body = body.replace(b'Content-Type: application/json', b'Content-Type: ' + b'x' * editor.HEADER_LIMIT)
    elif fault == "no_metadata": headers, body = multipart(metadata(), b"x"); body = body[body.find(b'\r\n--boundary_test\r\n') + 2:]
    elif fault == "unexpected_resume": headers, body = multipart(metadata(), b"x")
    elif fault == "missing_resume": headers, body = multipart(metadata(resume_action="replace", resume_kind="resume_pdf"))
    else: headers, body = multipart(metadata(resume_action="replace", resume_kind="resume_pdf"), b"")
    headers.replace_header("Content-Length", str(len(body)))
    with pytest.raises(editor.EditorInvalid): editor.read_save_request(headers, io.BytesIO(body))


@pytest.mark.parametrize("point", range(1, 7))
def test_every_fsync_boundary_has_honest_publication_semantics(configured, monkeypatch, point):
    _, path, resume, original = configured
    request = metadata(edits={"identity.full_name": "UPDATED"}, resume_action="replace", resume_kind="resume_pdf")
    prior = snapshot(path), snapshot(resume)
    real_fsync, calls = os.fsync, 0
    def fail(descriptor):
        nonlocal calls
        calls += 1
        if calls == point:
            raise OSError("PRIVATE_FSYNC_DETAILS")
        return real_fsync(descriptor)
    monkeypatch.setattr(editor.os, "fsync", fail)
    error = editor.PublicationUncertain if point == 6 else editor.EditorConflict
    with pytest.raises(error) as failure:
        editor.save_profile(request, b"OPAQUE")
    assert "PRIVATE" not in str(failure.value)
    assert (snapshot(path), snapshot(resume)) == prior
    if point == 6:
        assert failure.value.current == settings.load_settings()
        assert failure.value.current["profile_path"] != original["profile_path"]
        assert json.loads(Path(failure.value.current["profile_path"]).read_bytes())["fields"]["identity.full_name"]["value"] == "UPDATED"
    else:
        assert settings.load_settings() == original
    assert not list(path.parent.glob(".settings-*.tmp"))


def test_settings_replace_failure_keeps_prior_selection(configured, monkeypatch):
    _, path, resume, original = configured
    request = metadata(edits={"identity.full_name": "NEW"})
    def fail(*_args, **_kwargs): raise OSError("PRIVATE_REPLACE_DETAILS")
    monkeypatch.setattr(editor.os, "replace", fail)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request)
    assert settings.load_settings() == original


@pytest.mark.parametrize("point", [1, 2, 3])
def test_write_failure_at_resume_profile_or_settings_keeps_old_selection(configured, monkeypatch, point):
    _, path, resume, original = configured
    request = metadata(edits={"identity.full_name": "NEW"}, resume_action="replace", resume_kind="resume_pdf")
    real_fdopen, writes = os.fdopen, 0
    class BrokenWrite:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): self.handle.__enter__(); return self
        def __exit__(self, *args): return self.handle.__exit__(*args)
        def __getattr__(self, name): return getattr(self.handle, name)
        def write(self, data):
            nonlocal writes
            writes += 1
            if writes == point: raise OSError("PRIVATE_WRITE_DETAILS")
            return self.handle.write(data)
    def wrapped(fd, mode, *args, **kwargs):
        handle = real_fdopen(fd, mode, *args, **kwargs)
        return BrokenWrite(handle) if mode == "wb" else handle
    monkeypatch.setattr(editor.os, "fdopen", wrapped)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request, b"OPAQUE")
    assert settings.load_settings() == original
    assert not list(path.parent.glob(".settings-*.tmp"))


def test_source_promotion_mid_save_conflicts_before_settings_switch(configured, monkeypatch):
    _, path, _, original = configured
    request = metadata(edits={"identity.full_name": "NEW"})
    real_publish = editor._publish_file
    def promote(*args):
        result = real_publish(*args)
        obj = json.loads(path.read_bytes()); obj["fields"]["identity.phone"]["value"] = "PROMOTED"
        replacement = path.with_suffix(".next")
        replacement.write_text(json.dumps(obj)); replacement.chmod(0o600)
        os.replace(replacement, path)
        return result
    monkeypatch.setattr(editor, "_publish_file", promote)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request)
    assert settings.load_settings() == original
    assert json.loads(path.read_bytes())["fields"]["identity.phone"]["value"] == "PROMOTED"


def test_same_writer_lock_inode_replacement_conflicts(configured, monkeypatch):
    _, path, _, original = configured
    request = metadata()
    real_publish = editor._publish_file
    def replace_lock(*args):
        result = real_publish(*args)
        lock = path.with_name(path.name + ".lock")
        lock.unlink(); lock.touch(mode=0o600)
        return result
    monkeypatch.setattr(editor, "_publish_file", replace_lock)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request)
    assert settings.load_settings() == original


def test_new_version_alias_rejected_before_settings_publication(configured, monkeypatch):
    _, path, resume, original = configured
    request = metadata()
    real_publish = editor._publish_file
    def alias(*args):
        name, signature = real_publish(*args)
        target = path.parent / name
        target.unlink(); target.symlink_to(resume)
        return name, signature
    monkeypatch.setattr(editor, "_publish_file", alias)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request)
    assert settings.load_settings() == original


def test_authority_swap_before_switch_does_not_redirect_publication(configured, monkeypatch):
    authority, _, _, original = configured
    request = metadata()
    real_publish = editor._publish_file
    moved = authority.with_name("moved")
    def swap(*args):
        result = real_publish(*args)
        authority.rename(moved); authority.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(editor, "_publish_file", swap)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request)
    assert json.loads((moved / "settings.json").read_bytes()) == original
    assert not list(authority.iterdir())


@pytest.mark.parametrize("target", ["profile", "lock", "authority"])
def test_foreign_owner_rejected_without_chmod(configured, monkeypatch, target):
    authority, path, _, _ = configured
    request = metadata()
    lock = path.with_name(path.name + ".lock"); lock.touch(mode=0o600)
    wanted = {"profile": path, "lock": lock, "authority": authority}[target].stat().st_ino
    real_fstat, real_stat = os.fstat, os.stat
    def foreign(info):
        if info.st_ino != wanted: return info
        class Info:
            def __getattr__(self, key): return getattr(info, key)
            st_uid = os.getuid() + 1
        return Info()
    monkeypatch.setattr(editor.os, "stat", lambda *a, **k: foreign(real_stat(*a, **k)))
    monkeypatch.setattr(editor.os, "fstat", lambda *a, **k: foreign(real_fstat(*a, **k)))
    with pytest.raises((editor.EditorInvalid, editor.EditorConflict)): editor.save_profile(request)


def test_uncertain_readback_reconciles_memory_and_never_retries(configured, tmp_path, monkeypatch):
    _, _, _, original = configured
    supervisor = supervisor_for(tmp_path, original)
    request = metadata(edits={"identity.full_name": "UPDATED"})
    real_write, writes = settings._write_at, 0
    def after_replace(parent, encoded):
        nonlocal writes
        writes += 1
        real_write(parent, encoded)
        raise OSError("PRIVATE_POST_REPLACE")
    monkeypatch.setattr(settings, "_write_at", after_replace)
    with pytest.raises(editor.PublicationUncertain): supervisor.configure_profile_editor(request, None)
    assert writes == 1
    assert supervisor.worker.settings == supervisor.manager.settings == settings.load_settings()
    assert supervisor.manager._profile_ref() != original["profile_path"]
    assert not supervisor.manager._profile_selection_uncertain


def test_unresolved_publication_fences_only_future_admission_until_explicit_reconcile(configured, tmp_path, monkeypatch):
    from executor.autonomy.commands import CommandEnvelope
    _, path, _, original = configured
    supervisor = supervisor_for(tmp_path, original)
    task = supervisor.queue.enqueue(TaskSpec(company="Synthetic", role="Existing", target_url="https://existing.test",
                                             profile_ref=str(path), live_authorized=False))
    request = metadata(edits={"identity.full_name": "UPDATED"})
    real_write, real_observe = settings._write_at, editor._observe_at
    def after_replace(parent, encoded):
        real_write(parent, encoded)
        raise OSError("PRIVATE_POST_REPLACE")
    monkeypatch.setattr(settings, "_write_at", after_replace)
    monkeypatch.setattr(editor, "_observe_at", lambda *_: (_ for _ in ()).throw(OSError("PRIVATE_READBACK")))
    with pytest.raises(editor.PublicationUncertain) as failure:
        supervisor.configure_profile_editor(request, None)
    assert failure.value.current is None and supervisor.manager._profile_selection_uncertain
    with pytest.raises(RuntimeError, match="reconciliation_required"): supervisor.manager._profile_ref()
    with pytest.raises(editor.EditorConflict): supervisor.reconcile_profile_editor()
    assert supervisor.manager._profile_selection_uncertain
    # Existing-task controls do not share a broad profile/configuration fence.
    receipt = supervisor.run_local_command(CommandEnvelope(command_id="cancel-existing-001", task_id=task["task_id"],
                                           action="CANCEL", expected_revision=task["revision"]))
    assert receipt and supervisor.queue.get(task["task_id"])["stage"] == "CANCELLED"
    monkeypatch.setattr(editor, "_observe_at", real_observe)
    observed = supervisor.reconcile_profile_editor()
    assert observed["reconciliation_status"] == "reconciled"
    assert supervisor.manager._profile_ref() == settings.load_settings()["profile_path"]
    assert supervisor.worker.settings == supervisor.manager.settings


@pytest.fixture
def api(configured, tmp_path):
    _, _, _, original = configured
    supervisor = supervisor_for(tmp_path, original)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    session = supervisor.consume_ui_ticket(supervisor.issue_ui_ticket())
    def request(method="GET", path="/ui/api/profile-editor", body=None, headers=None, *, authenticated=True, local_origin=True):
        values = dict(headers or {})
        if authenticated: values["Cookie"] = "application_executor_session=" + session
        if local_origin: values["Origin"] = origin
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request(method, path, body, values)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally: connection.close()
    try: yield supervisor, request
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


def test_http_editor_auth_origin_no_store_and_no_bearer_value_surface(api):
    supervisor, request = api
    assert request(authenticated=False)[0] == 401
    assert request(authenticated=False, headers={"Authorization": "Bearer " + supervisor.token})[0] == 401
    assert request(headers={"Host": "remote.invalid"})[0] == 403
    assert request(local_origin=False, headers={"Origin": "https://remote.invalid"})[0] == 403
    status, view, headers = request()
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer" and headers["X-Content-Type-Options"] == "nosniff"
    assert view["admission_status"] == "ready"
    assert "PRIVATE_NAME" in json.dumps(view) and "PRIVATE_ID" not in json.dumps(view)
    assert request(path="/ui/api/profile-editor?unknown=1")[0] == 400
    assert request(path="/v1/profile-editor", local_origin=False, headers={"Authorization": "Bearer " + supervisor.token})[0] in {400, 404}
    data_headers, body = multipart(metadata(view))
    assert request("POST", body=body, headers=dict(data_headers), local_origin=False)[0] == 403
    status, saved, _ = request("POST", body=body, headers=dict(data_headers))
    assert status == 200 and saved["save_status"] == "saved"
    status, readback, _ = request()
    assert (readback["settings_version"], readback["profile_version"]) == (saved["settings_version"], saved["profile_version"])
    assert request("POST", body=body, headers=dict(data_headers))[0] == 409


def test_http_uncertainty_and_reconcile_are_sanitized(api, monkeypatch):
    supervisor, request = api
    _, view, _ = request()
    headers, body = multipart(metadata(view))
    real_write = settings._write_at
    def fail_after_write(parent, encoded):
        real_write(parent, encoded)
        raise OSError("PRIVATE_EXCEPTION_BODY")
    monkeypatch.setattr(settings, "_write_at", fail_after_write)
    status, result, _ = request("POST", body=body, headers=dict(headers))
    assert (status, result) == (409, {"error": "publication_uncertain", "reconciliation_status": "reconciled", "submit_capability": False})
    status, result, _ = request("POST", "/ui/api/profile-editor/reconcile", b"{}", {"Content-Type": "application/json"})
    assert status == 200 and result["reconciliation_status"] == "reconciled"
    assert request("POST", "/ui/api/profile-editor/reconcile", b'{"ignored":1}', {"Content-Type": "application/json"})[0] == 400


def test_editor_does_not_initialize_provider_model_browser_or_write_task_state(configured, tmp_path, monkeypatch, capsys):
    from executor import evidence
    from executor.autonomy import supervisor as supervisor_module
    _, path, _, original = configured
    supervisor = supervisor_for(tmp_path, original)
    task = supervisor.queue.enqueue(TaskSpec(company="Synthetic", role="Existing", target_url="https://existing.test", profile_ref=str(path)))
    before_task = supervisor.queue.get(task["task_id"])
    before_events = supervisor.queue.recent_events(100)
    def forbidden(*_args, **_kwargs): pytest.fail("no provider, model, browser or old profile writer")
    monkeypatch.setattr(ManagerController, "provider", property(forbidden))
    monkeypatch.setattr(evidence, "write_profile", forbidden)
    monkeypatch.setattr(evidence, "set_user_confirmed_field", forbidden)
    monkeypatch.setattr(supervisor_module.browser, "ensure_browser", forbidden, raising=False)
    monkeypatch.setattr(supervisor_module.browser, "_alive", lambda: False)
    view = supervisor.profile_editor_state()
    supervisor.configure_profile_editor(metadata(view, edits={"identity.full_name": "PRIVATE_UPDATED"}), None)
    assert supervisor.queue.get(task["task_id"]) == before_task
    assert supervisor.queue.recent_events(100) == before_events
    public = json.dumps([supervisor.ui_state(), supervisor.diagnostics()])
    for secret in ("PRIVATE_UPDATED", "PRIVATE_NAME", "PRIVATE_ID", "PRIVATE_PHONE", "PRIVATE_SOURCE"):
        assert secret not in public
    assert "PRIVATE" not in capsys.readouterr().out


def test_browser_formdata_metadata_blob_filename_is_advisory(configured):
    headers, body = multipart(metadata())
    body = body.replace(b'name="metadata"', b'name="metadata"; filename="metadata.json"')
    headers.replace_header("Content-Length", str(len(body)))
    parsed, resume = editor.read_save_request(headers, io.BytesIO(body))
    assert parsed == metadata() and resume is None


def test_small_total_request_header_limit_is_enforced_before_stream_read(configured):
    headers, _ = multipart(metadata())
    headers["X-Too-Large"] = "x" * editor.REQUEST_HEADER_LIMIT
    class NeverRead:
        def read(self, _): raise AssertionError("no stream read")
    with pytest.raises(editor.EditorInvalid): editor.read_save_request(headers, NeverRead())


def test_stream_timeout_is_sanitized_as_invalid_request(configured):
    headers, _ = multipart(metadata())
    class TimedOut:
        def read(self, _): raise TimeoutError("PRIVATE_TIMEOUT_DETAIL")
    with pytest.raises(editor.EditorInvalid, match="^invalid_profile_editor_request$"):
        editor.read_save_request(headers, TimedOut())


def test_compact_profile_under_exact_limit_keeps_extensions_without_pretty_size_loss(configured):
    _, path, _, _ = configured
    profile = canonical()
    profile["wide_extension"] = [0] * 42000
    raw = json.dumps(profile, separators=(",", ":")).encode()
    assert len(raw) < editor.PROFILE_LIMIT < len(json.dumps(profile, indent=2).encode())
    path.write_bytes(raw)
    _, view = editor.editor_state()
    assert view["profile_version"] == hashlib.sha256(raw).hexdigest()
    selected, _ = editor.save_profile(metadata(view, edits={"identity.email": "changed@example.test"}))
    assert json.loads(Path(selected["profile_path"]).read_bytes())["wide_extension"] == profile["wide_extension"]
    assert path.read_bytes() == raw


def test_profile_read_and_output_limits_preserve_selection(configured, monkeypatch):
    _, path, _, original = configured
    request = metadata()
    path.write_bytes(b"x" * (editor.PROFILE_LIMIT + 1))
    with pytest.raises(editor.EditorConflict): editor.editor_state()
    with pytest.raises(editor.EditorConflict): editor.save_profile(request)
    assert settings.load_settings() == original


def test_output_profile_growth_past_limit_fails_without_switch(configured):
    _, path, _, original = configured
    profile = canonical(); profile["long_extension"] = ""
    raw = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode()
    profile["long_extension"] = "x" * (editor.PROFILE_LIMIT - len(raw))
    raw = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(raw) == editor.PROFILE_LIMIT
    path.write_bytes(raw)
    request = metadata(edits={"identity.phone": "NEW"})
    with pytest.raises(editor.EditorInvalid): editor.save_profile(request)
    assert path.read_bytes() == raw and settings.load_settings() == original


def test_structured_education_is_read_only_to_prevent_flat_row_disagreement(configured):
    _, path, _, original = configured
    profile = json.loads(path.read_bytes())
    profile["fields"]["education.highest.school"] = {"value": "OLD_SCHOOL"}
    profile["collections"]["education_records"] = [{"school": "OLD_SCHOOL", "extension": [1]}]
    path.write_text(json.dumps(profile))
    before = snapshot(path)
    _, view = editor.editor_state()
    fields = [field for field in view["fields"] if field["key"].startswith("education.highest.")]
    assert fields and all(field["status"] == "unsupported" and field["editable"] is False
                          and field["value"] is None for field in fields)
    with pytest.raises(editor.EditorInvalid):
        editor.save_profile(metadata(view, edits={"education.highest.school": "DIVERGENT"}))
    selected, _ = editor.save_profile(metadata(view, edits={"identity.email": "other@example.test"}))
    after = json.loads(Path(selected["profile_path"]).read_bytes())
    assert after["collections"] == profile["collections"]
    assert after["fields"]["education.highest.school"] == profile["fields"]["education.highest.school"]
    assert snapshot(path) == before


def test_changed_uploaded_resume_keeps_uncertain_admission_fenced_until_proven(configured, tmp_path, monkeypatch):
    _, _, _, original = configured
    supervisor = supervisor_for(tmp_path, original)
    request = metadata(resume_action="replace", resume_kind="resume_pdf")
    real_write = settings._write_at
    original_bytes = b"ORIGINAL_OPAQUE"
    def alter_published_resume(parent, encoded):
        real_write(parent, encoded)
        selected = json.loads(encoded)
        profile = json.loads(Path(selected["profile_path"]).read_bytes())
        Path(profile["assets"]["resume"]["path"]).write_bytes(b"CHANGED_OPAQUE")
    monkeypatch.setattr(settings, "_write_at", alter_published_resume)
    with pytest.raises(editor.PublicationUncertain) as failure:
        supervisor.configure_profile_editor(request, original_bytes)
    assert failure.value.current is None and supervisor.manager._profile_selection_uncertain
    with pytest.raises(editor.EditorConflict): supervisor.reconcile_profile_editor()
    assert supervisor.manager._profile_selection_uncertain
    selected = settings.load_settings()
    profile = json.loads(Path(selected["profile_path"]).read_bytes())
    Path(profile["assets"]["resume"]["path"]).write_bytes(original_bytes)
    assert supervisor.reconcile_profile_editor()["reconciliation_status"] == "reconciled"
    assert supervisor.manager._profile_ref() == selected["profile_path"]


def test_resume_history_key_collision_preserves_existing_extension(configured, monkeypatch):
    _, path, _, original = configured
    profile = json.loads(path.read_bytes())
    profile["resume_previous_" + "0" * 32] = {"PRIVATE_EXISTING": [1]}
    path.write_text(json.dumps(profile))
    request = metadata(resume_action="replace", resume_kind="resume_pdf")
    monkeypatch.setattr(editor.secrets, "token_hex", lambda _: "0" * 32)
    with pytest.raises(editor.EditorConflict): editor.save_profile(request, b"opaque")
    assert json.loads(path.read_bytes()) == profile and settings.load_settings() == original


def test_resume_history_does_not_become_an_active_application_attachment(configured):
    from types import SimpleNamespace
    from executor.application import ApplicationExecutor
    selected, _ = editor.save_profile(metadata(resume_action="replace", resume_kind="resume_doc"), b"OPAQUE_DOC")
    profile = json.loads(Path(selected["profile_path"]).read_bytes())
    runner = ApplicationExecutor("https://synthetic.example.test", selected["profile_path"],
                                 settings={"deepseek": {"enabled": False}},
                                 audit_store=SimpleNamespace(load_user_answers=lambda: {}))
    assert set(runner.plan.attachments) == {"resume", "other"}
    assert runner.plan.attachments["resume"] == profile["assets"]["resume"]["path"]
    assert not any(key.startswith("resume_previous_") for key in runner.plan.attachments)


def test_existing_asset_with_null_source_remains_supported(configured):
    _, path, _, _ = configured
    profile = json.loads(path.read_bytes()); profile["assets"]["resume"]["source"] = None
    path.write_text(json.dumps(profile))
    _, view = editor.editor_state()
    assert view["resume"]["status"] == "recorded_locally"
    selected, _ = editor.save_profile(metadata(view))
    assert json.loads(Path(selected["profile_path"]).read_bytes())["assets"]["resume"]["source"] is None


def test_metadata_wire_compact_limit_is_not_reduced_by_pretty_spacing(configured):
    request = metadata(edits={key: "x" * 2048 for key in editor.FIELD_KEYS if editor._type(key) == "text"})
    request["edits"]["preferences.preferred_cities"] = ["x" * 200] * 32
    # Non-ASCII uses actual UTF-8 size, matching JSON.stringify + TextEncoder.
    request["edits"]["language.cet6.score"] = "x" * 2048
    compact = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    delta = editor.METADATA_LIMIT - len(compact)
    emoji, remainder = divmod(delta, 3)
    chinese = 0
    if remainder == 1:
        emoji -= 1; chinese = 2
    elif remainder == 2:
        chinese = 1
    assert 0 <= emoji + chinese <= 2048
    request["edits"]["identity.phone"] = "😀" * emoji + "中" * chinese + "x" * (2048 - emoji - chinese)
    compact = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(compact) == editor.METADATA_LIMIT
    assert len(json.dumps(request, ensure_ascii=False).encode()) > editor.METADATA_LIMIT
    editor.validate_metadata(request, None)
    headers, body = multipart(request)
    body = body.replace(json.dumps(request, ensure_ascii=False).encode(), compact)
    headers.replace_header("Content-Length", str(len(body)))
    assert editor.read_save_request(headers, io.BytesIO(body))[0] == request


def test_near_limit_editor_save_is_readable_by_task_preparation(configured, tmp_path):
    from executor.autonomy import profile_setup, task_preparation
    _, path, _, _ = configured
    profile = json.loads(path.read_bytes())
    profile["wide_extension"] = [0] * 30000
    profile["large_extension"] = ""
    raw = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode()
    profile["large_extension"] = "x" * (editor.PROFILE_LIMIT - len(raw) - 1024)
    raw = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(raw) == editor.PROFILE_LIMIT - 1024
    assert len(json.dumps(profile, ensure_ascii=False, indent=2).encode()) > editor.PROFILE_LIMIT
    path.write_bytes(raw)
    assert profile_setup.validate_profile_text(raw.decode()) == profile
    # Import's established formatting and generated-byte limit are unchanged.
    with pytest.raises(ValueError, match="^invalid_profile$"):
        profile_setup._profile_bytes(raw.decode())
    selected, saved = editor.save_profile(metadata(edits={"identity.email": "future@example.test"}))
    selected_path = Path(selected["profile_path"])
    assert editor.PROFILE_LIMIT - 1024 < len(selected_path.read_bytes()) <= editor.PROFILE_LIMIT
    queue = TaskQueue(tmp_path.resolve() / "near-limit-runtime")
    task = queue.enqueue(TaskSpec(company=task_preparation.CONTRACT_COMPANY,
                                  role=task_preparation.CONTRACT_ROLE,
                                  target_url=task_preparation.CONTRACT_URL,
                                  profile_ref=str(selected_path), live_authorized=False))
    observed = task_preparation.prepare_task(queue, task["task_id"], task["revision"])
    assert observed["profile"] == {"status": "available", "version": saved["profile_version"]}
    assert observed["resume"]["status"] == "local_version_matches"
    assert next(item for item in observed["items"] if item["key"] == "identity.email")["status"] == "recorded_locally"
    assert "future@example.test" not in json.dumps(observed) and path.read_bytes() == raw


@pytest.mark.parametrize("text", [
    '{"fields":{},"fields":{}}',
    '{"fields":{"identity.phone":{"value":NaN}}}',
    '{"fields":{"identity.phone":{"value":1e999}}}',
    '{"fields":{"identity.phone":{"value":"\\ud800"}}}',
    '{"fields":{},"extension":{"access_token":"PRIVATE_SECRET"}}',
    '{"fields":{"identity.phone":"PRIVATE_NOT_A_FIELD"}}',
    '{"fields":{},"extension":"' + "😀" * (editor.PROFILE_LIMIT // 4) + '"}',
])
def test_shared_raw_reader_rejects_ambiguities_secrets_schema_unicode_and_utf8_size(text):
    from executor.autonomy.profile_setup import validate_profile_text
    with pytest.raises(ValueError, match="^invalid_profile$"):
        validate_profile_text(text)
