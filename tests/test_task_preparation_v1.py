"""Read-only task-bound preparation never becomes website or account authority."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import threading
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from executor import settings
from executor.autonomy import task_preparation as preparation
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.supervisor import Supervisor, create_server

PRIVATE = "PRIVATE_PREPARATION_CANARY"


@pytest.fixture
def tmp_path(tmp_path):
    # macOS pytest temporary roots may begin with the /var -> /private/var
    # system alias. Fixtures establish their real authority path explicitly;
    # production admission continues to reject every symlink component.
    return tmp_path.resolve()


def _write(path, value, *, mode=0o600):
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())
    path.chmod(mode)
    return path


def _profile(**extra):
    return {"fields": {"identity.full_name": {"value": PRIVATE},
                       "identity.email": {"value": PRIVATE + "@example.test"},
                       "identity.id_number": {"value": PRIVATE + "_ID"}}, **extra}


def _task(q, profile, **changes):
    values = {"company": preparation.CONTRACT_COMPANY,
              "role": preparation.CONTRACT_ROLE,
              "target_url": preparation.CONTRACT_URL,
              "profile_ref": str(profile), "live_authorized": False}
    values.update(changes)
    return q.enqueue(TaskSpec(**values))


def _snapshot(q):
    with q.tx() as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: [tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')] for name in names}


def _prepare(q, task):
    return preparation.prepare_task(q, task["task_id"], task["revision"])


@pytest.fixture
def local_task(tmp_path):
    profile = _write(tmp_path / (PRIVATE + "_profile.json"), _profile())
    q = TaskQueue(tmp_path / "state")
    return q, _task(q, profile), profile


def test_matching_contract_is_value_free_local_only_with_exact_byte_versions(local_task, tmp_path):
    q, task, profile = local_task
    resume = _write(tmp_path / (PRIVATE + "_resume.pdf"), b"not parsed as PDF\0" + PRIVATE.encode())
    digest = hashlib.sha256(resume.read_bytes()).hexdigest()
    contents = _profile(assets={"resume": {"kind": "resume_pdf", "path": str(resume), "sha256": digest}})
    contents["fields"].update({
        "identity.phone": {"value": ""},
        "identity.gender": {"value": {"unexpected": PRIVATE}},
        "language.cet6.level": {"value": "CET6"},
        "language.cet6.score": {"value": 500},
        "preferences.preferred_cities": {"value": [PRIVATE]},
    })
    _write(profile, json.dumps(contents, ensure_ascii=False, indent=3).encode() + b"\n")
    before = _snapshot(q)
    profile_before, resume_before = profile.read_bytes(), resume.read_bytes()
    observed = _prepare(q, task)
    assert _snapshot(q) == before
    assert profile.read_bytes() == profile_before and resume.read_bytes() == resume_before
    assert observed["task_id"] == task["task_id"] and observed["task_revision"] == task["revision"]
    assert observed["mode"] == "LOCAL_PREPARATION_ONLY"
    assert observed["profile"] == {"status": "available", "version": hashlib.sha256(profile_before).hexdigest()}
    assert observed["resume"] == {"status": "local_version_matches", "version": digest}
    assert observed["contract"] == {"matched": True, "id": preparation.CONTRACT_ID,
        "source_url": preparation.CONTRACT_URL, "observed_at": "2026-09-30",
        "coverage": "observed_fields_only", "requiredness": "UNVERIFIED", "freshness": "cached_observation"}
    assert observed["capabilities"] == {"live_write": False, "submit": False,
        "account_verified": False, "server_draft_verified": False}
    items = {item["key"]: item for item in observed["items"]}
    assert items["identity.full_name"]["status"] == "recorded_locally"
    assert items["identity.phone"]["status"] == "missing"
    assert items["identity.gender"]["status"] == "needs_review"
    assert items["education.highest.school"]["status"] == "missing"
    assert items["language.cet6.score"]["status"] == "recorded_locally"
    assert items["preferences.preferred_cities"]["status"] == "recorded_locally"
    assert "identity.id_number" not in items
    assert {item["status"] for item in observed["items"]} <= {"recorded_locally", "missing", "needs_review"}
    serialized = json.dumps(observed, ensure_ascii=False)
    assert PRIVATE not in serialized and str(tmp_path) not in serialized and "CET6" not in json.dumps(observed["items"])
    for marker in ("身份证", "验证码", "隐私", "岗位2", "上传", "最终提交", "必填"):
        assert marker in " ".join(observed["manual_steps"])


@pytest.mark.parametrize("changes", [
    {"target_url": "http://www.qiyunfang.com/h-col-124.html"},
    {"target_url": "https://qiyunfang.com/h-col-124.html"},
    {"target_url": "https://www.qiyunfang.com:443/h-col-124.html"},
    {"target_url": "https://WWW.qiyunfang.com/h-col-124.html"},
    {"target_url": "https://www.qiyunfang.com/h-col-124.html/"},
    {"target_url": "https://www.qiyunfang.com/h-col-125.html"},
    {"target_url": "https://www.qiyunfang.com/h-col-124.html?"},
    {"target_url": "https://www.qiyunfang.com/h-col-124.html?id=1"},
    {"target_url": "https://www.qiyunfang.com/h-col-124.html#/job/abc"},
    {"target_url": "https://www.qiyunfang.com/h-col-124.html#"},
    {"target_url": "https://www.qiyunfang.com/%68-col-124.html"},
    {"company": "启云方科技"}, {"company": preparation.CONTRACT_COMPANY + " "},
    {"company": PRIVATE}, {"role": "应用实施工程师"}, {"role": "应用实施工程师(武汉)"},
    {"role": preparation.CONTRACT_ROLE + " "},
])
def test_contract_requires_exact_persisted_target_company_and_role(tmp_path, changes):
    profile = _write(tmp_path / "profile.json", _profile())
    q = TaskQueue(tmp_path / "state")
    task = _task(q, profile, **changes)
    before = _snapshot(q)
    observed = _prepare(q, task)
    assert observed["contract"] == {"matched": False, "id": None, "source_url": None,
        "observed_at": None, "coverage": "unavailable", "requiredness": "UNVERIFIED", "freshness": "unavailable"}
    assert observed["items"] == []
    assert observed["profile"]["status"] == "available" and observed["resume"]["status"] == "missing"
    assert _snapshot(q) == before
    assert PRIVATE not in json.dumps(observed)


def test_deduplication_uses_original_persisted_spec_not_new_matching_display(tmp_path):
    old = _write(tmp_path / "old.json", {"fields": {}})
    new = _write(tmp_path / "new.json", _profile())
    q = TaskQueue(tmp_path / "state")
    original = _task(q, old, company=PRIVATE, role=PRIVATE)
    deduped = _task(q, new)
    assert deduped["task_id"] == original["task_id"]
    observed = _prepare(q, deduped)
    assert observed["contract"]["matched"] is False
    assert observed["profile"]["version"] == hashlib.sha256(old.read_bytes()).hexdigest()


def test_new_global_selection_does_not_rebind_old_task_profile(local_task, tmp_path, monkeypatch):
    q, task, profile = local_task
    changed = _write(tmp_path / "new-selection.json", {"fields": {}})
    supervisor = Supervisor(q, token="x" * 40)
    supervisor.manager.settings = {"profile_path": str(changed)}
    supervisor.worker.settings = {"profile_path": str(changed)}
    monkeypatch.setattr(settings, "load_settings", lambda: pytest.fail("must not consult global settings"))
    result = preparation.prepare_task(supervisor.queue, task["task_id"], task["revision"])
    assert result["profile"]["version"] == hashlib.sha256(profile.read_bytes()).hexdigest()
    assert result["items"][0]["status"] == "recorded_locally"


@pytest.mark.parametrize("content", [
    b'{"arbitrary":"PRIVATE_PREPARATION_CANARY"}', b'{"fields":[]}',
    b'{"fields":{"identity.full_name":"PRIVATE_PREPARATION_CANARY"}}',
    b'{"fields":{},"fields":{}}', b'{"fields":{"x":{"value":NaN}}}',
    b'{"fields":{"x":{"value":1e999}}}', b'{"fields":{"x":{"value":"\\ud800"}}}',
    b'{"fields":{},"unrecognized":{"password":"PRIVATE_PREPARATION_CANARY"}}',
    b'{"fields":{},"schema_version":"2.0"}', b'{"fields":{},"assets":[]}',
    b'{"fields":{},"source_documents":["PRIVATE_PREPARATION_CANARY"]}',
    b'{"fields":{},"assets":{"resume":{"path":3,"kind":"resume_pdf"}}}',
    b'\xff', b'', b'[' * 5000,
])
def test_invalid_profiles_are_sanitized_without_repairs(local_task, content):
    q, task, profile = local_task
    _write(profile, content, mode=0o400)
    before = _snapshot(q), profile.read_bytes(), profile.stat().st_mode
    result = _prepare(q, task)
    assert result["profile"] == {"status": "unavailable", "version": None}
    assert result["resume"] == {"status": "unavailable", "version": None}
    assert all(item["status"] == "needs_review" for item in result["items"])
    assert (_snapshot(q), profile.read_bytes(), profile.stat().st_mode) == before
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("value", [None, "", "  ", [], False, {}, [PRIVATE], 0, 10 ** 500])
def test_field_presence_does_not_certify_values_or_crash_on_unusual_types(local_task, value):
    q, task, profile = local_task
    _write(profile, {"fields": {"language.cet6.score": {"value": value}}})
    result = _prepare(q, task)
    assert result["profile"]["status"] == "available"
    assert PRIVATE not in json.dumps(result)


def test_legacy_known_fields_are_presence_only(local_task):
    q, task, profile = local_task
    _write(profile, {"identity": {"full_name": PRIVATE},
                     "education": {"highest": {"degree": PRIVATE}},
                     "preferences": {"preferred_cities": [PRIVATE]}})
    result = _prepare(q, task)
    items = {item["key"]: item["status"] for item in result["items"]}
    assert items["identity.full_name"] == items["education.highest.degree"] == "recorded_locally"
    assert items["preferences.preferred_cities"] == "recorded_locally"
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("kind", ["symlink", "ancestor_symlink", "hardlink", "fifo", "directory", "public", "foreign", "large", "missing", "relative", "dot", "dotdot", "double_slash"])
def test_profile_admission_rejects_aliases_nonprivate_and_nonordinary_without_reading(tmp_path, monkeypatch, kind):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    profile = _write(real_dir / "profile.json", _profile())
    path = str(profile)
    if kind == "symlink":
        link = tmp_path / "alias.json"
        link.symlink_to(profile)
        path = str(link)
    elif kind == "ancestor_symlink":
        link = tmp_path / "alias"
        link.symlink_to(real_dir, target_is_directory=True)
        path = str(link / profile.name)
    elif kind == "hardlink":
        os.link(profile, tmp_path / "hardlink.json")
    elif kind == "fifo":
        profile.unlink()
        os.mkfifo(profile, mode=0o600)
    elif kind == "directory":
        profile.unlink()
        profile.mkdir(mode=0o700)
    elif kind == "public":
        profile.chmod(0o644)
    elif kind == "foreign":
        monkeypatch.setattr(preparation.os, "getuid", lambda: profile.stat().st_uid + 1)
    elif kind == "large":
        with profile.open("wb") as handle:
            handle.truncate(preparation.PROFILE_LIMIT + 1)
    elif kind == "missing":
        profile.unlink()
    elif kind == "relative":
        path = "real/profile.json"
    elif kind == "dot":
        path = str(real_dir) + "/./profile.json"
    elif kind == "dotdot":
        path = str(real_dir) + "/../real/profile.json"
    elif kind == "double_slash":
        path = str(real_dir) + "//profile.json"
    q = TaskQueue(tmp_path / "state")
    task = _task(q, path)
    before = _snapshot(q)
    monkeypatch.setattr(preparation.os, "read", lambda *_: pytest.fail("unadmitted content read"))
    result = _prepare(q, task)
    assert result["profile"] == {"status": "unavailable", "version": None}
    assert _snapshot(q) == before
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("stored,status", [(None, "unverified"), ("", "unverified"),
    ("bad", "unverified"), ("a" * 64, "local_version_differs"), ("match", "local_version_matches")])
def test_resume_only_hashes_bytes_and_distinguishes_local_versions(local_task, tmp_path, stored, status):
    q, task, profile = local_task
    resume = _write(tmp_path / "resume.pdf", b"not a parsed resume\xff" + PRIVATE.encode(), mode=0o644)
    digest = hashlib.sha256(resume.read_bytes()).hexdigest()
    asset = {"path": str(resume), "kind": "resume_pdf", "sha256": digest.upper() if stored == "match" else stored}
    _write(profile, _profile(assets={"resume": asset}))
    result = _prepare(q, task)
    assert result["resume"] == {"status": status, "version": digest}
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("kind", ["symlink", "ancestor_symlink", "hardlink", "fifo", "foreign", "large", "directory", "missing", "relative", "dotdot"])
def test_resume_file_admission_does_not_follow_or_read_rejected_asset(local_task, tmp_path, monkeypatch, kind):
    q, task, profile = local_task
    directory = tmp_path / "resume-dir"
    directory.mkdir()
    resume = _write(directory / "resume.pdf", PRIVATE.encode())
    path = str(resume)
    if kind == "symlink":
        alias = tmp_path / "resume-alias.pdf"
        alias.symlink_to(resume)
        path = str(alias)
    elif kind == "ancestor_symlink":
        alias = tmp_path / "resume-alias.pdf"
        alias.symlink_to(directory, target_is_directory=True)
        path = str(alias / resume.name)
    elif kind == "hardlink":
        os.link(resume, tmp_path / "resume-hardlink")
    elif kind == "fifo":
        resume.unlink()
        os.mkfifo(resume, mode=0o600)
    elif kind == "foreign":
        original_entry = preparation._entry
        def foreign_entry(parent, name):
            result = original_entry(parent, name)
            if name == resume.name and result is not None:
                attrs = {key: getattr(result, key) for key in dir(result) if key.startswith("st_")}
                attrs["st_uid"] += 1
                return SimpleNamespace(**attrs)
            return result
        monkeypatch.setattr(preparation, "_entry", foreign_entry)
    elif kind == "large":
        with resume.open("wb") as handle:
            handle.truncate(preparation.RESUME_LIMIT + 1)
    elif kind == "directory":
        resume.unlink()
        resume.mkdir()
    elif kind == "missing":
        resume.unlink()
    elif kind == "relative":
        path = "resume.pdf"
    elif kind == "dotdot":
        path = str(directory) + "/../resume-dir/resume.pdf"
    _write(profile, _profile(assets={"resume": {"path": path, "kind": "resume_pdf"}}))
    original_read = preparation.os.read
    def checked_read(fd, count):
        assert os.fstat(fd).st_ino == profile.stat().st_ino, "unadmitted resume content read"
        return original_read(fd, count)
    monkeypatch.setattr(preparation.os, "read", checked_read)
    result = _prepare(q, task)
    assert result["profile"]["status"] == "available"
    assert result["resume"] == {"status": "unavailable", "version": None}


def test_only_designated_resume_asset_is_inspected(local_task, tmp_path, monkeypatch):
    q, task, profile = local_task
    forbidden = tmp_path / "private-other.bin"
    forbidden.write_bytes(PRIVATE.encode())
    _write(profile, _profile(
        assets={"photo": {"path": str(forbidden), "kind": "photo"},
                "other": {"path": str(forbidden), "kind": "other"}},
        source_documents=[{"kind": "resume_pdf", "path": str(forbidden)}]))
    original_open, original_entry = preparation.os.open, preparation._entry
    def checked_open(path, *args, **kwargs):
        assert str(path) != forbidden.name and str(path) != str(forbidden)
        return original_open(path, *args, **kwargs)
    def checked_entry(parent, name):
        assert name != forbidden.name
        return original_entry(parent, name)
    monkeypatch.setattr(preparation.os, "open", checked_open)
    monkeypatch.setattr(preparation, "_entry", checked_entry)
    assert _prepare(q, task)["resume"] == {"status": "missing", "version": None}


@pytest.mark.parametrize("change", ["contents", "replace", "unlink", "hardlink", "mode", "parent_replace", "task_cancel", "task_spec", "task_revision", "task_delete"])
def test_observation_fences_profile_paths_fds_and_entire_persisted_task(local_task, tmp_path, monkeypatch, change):
    q, task, profile = local_task
    original_bytes = preparation._profile_bytes
    before = _snapshot(q)
    def change_during_validation(text):
        result = original_bytes(text)
        if change == "contents":
            _write(profile, {"fields": {}})
        elif change == "replace":
            replacement = _write(tmp_path / "replacement", profile.read_bytes())
            replacement.replace(profile)
        elif change == "unlink":
            profile.unlink()
        elif change == "hardlink":
            os.link(profile, tmp_path / "alias")
        elif change == "mode":
            profile.chmod(0o644)
        elif change == "parent_replace":
            # Keep the admitted original leaf reachable through held FDs while
            # the lexical path is redirected to a different directory.
            new_parent = tmp_path.with_name(tmp_path.name + "-moved")
            tmp_path.rename(new_parent)
            tmp_path.mkdir()
            _write(profile, _profile())
            # Queue lives under the moved tree; move it back to keep this test
            # focused on the profile's ancestor identity fence.
            (new_parent / "state").rename(tmp_path / "state")
        elif change == "task_cancel":
            q.cancel(task["task_id"])
        elif change == "task_spec":
            with q.tx() as db:
                spec = {**task["spec"], "profile_ref": str(tmp_path / "another")}
                db.execute("UPDATE tasks SET spec=? WHERE task_id=?", (json.dumps(spec), task["task_id"]))
        elif change == "task_revision":
            with q.tx() as db:
                db.execute("UPDATE tasks SET revision=revision+1 WHERE task_id=?", (task["task_id"],))
        elif change == "task_delete":
            with q.tx() as db:
                db.execute("DELETE FROM tasks WHERE task_id=?", (task["task_id"],))
        return result
    monkeypatch.setattr(preparation, "_profile_bytes", change_during_validation)
    with pytest.raises(RuntimeError, match="^preparation changed$"):
        _prepare(q, task)
    if not change.startswith("task_"):
        assert _snapshot(q) == before


@pytest.mark.parametrize("change", ["profile", "resume", "size", "resume_replaced", "task_cancel"])
def test_mid_read_changes_fail_closed(local_task, tmp_path, monkeypatch, change):
    q, task, profile = local_task
    resume = _write(tmp_path / "resume.pdf", b"x" * 100000)
    _write(profile, _profile(assets={"resume": {"path": str(resume), "kind": "resume_pdf"}}))
    target = profile if change == "profile" else resume
    target_inode = target.stat().st_ino
    original_read, changed = preparation.os.read, False
    def changing_read(fd, count):
        nonlocal changed
        data = original_read(fd, count)
        if not changed and os.fstat(fd).st_ino == target_inode:
            changed = True
            if change == "task_cancel":
                q.cancel(task["task_id"])
            elif change == "resume_replaced":
                replacement = _write(tmp_path / "replacement", b"x" * 100000)
                replacement.replace(resume)
            else:
                with target.open("ab" if change == "size" else "wb") as handle:
                    handle.write(b"y" * 100000)
        return data
    monkeypatch.setattr(preparation.os, "read", changing_read)
    with pytest.raises(RuntimeError, match="^preparation changed$"):
        _prepare(q, task)
    assert changed


def test_absent_profile_created_during_observation_is_conflict(local_task, monkeypatch):
    q, task, profile = local_task
    profile.unlink()
    original_get, calls = q.get, 0
    def changing_get(tid):
        nonlocal calls
        calls += 1
        if calls == 2:
            _write(profile, _profile())
        return original_get(tid)
    monkeypatch.setattr(q, "get", changing_get)
    with pytest.raises(RuntimeError):
        _prepare(q, task)


def test_stale_and_cancelled_tasks_reject_before_any_profile_io(local_task, monkeypatch):
    q, task, _ = local_task
    monkeypatch.setattr(preparation._LocalFile, "read", lambda *_: pytest.fail("must reject before profile read"))
    with pytest.raises(RuntimeError):
        preparation.prepare_task(q, task["task_id"], task["revision"] + 1)
    q.cancel(task["task_id"])
    with pytest.raises(RuntimeError):
        _prepare(q, task)
    cancelled = q.get(task["task_id"])
    with pytest.raises(RuntimeError):
        _prepare(q, cancelled)


@pytest.mark.parametrize("task_id,revision", [(True, 0), (None, 0), ("", 0), ("x" * 121, 0),
    ("../private/path", 0), ("private\nvalue", 0), ("id", True), ("id", 1.0),
    ("id", -1), ("id", preparation.MAX_REVISION + 1), ("id", "0")])
def test_invalid_request_rejected_before_task_lookup(task_id, revision):
    queue = SimpleNamespace(get=lambda *_: pytest.fail("invalid task lookup"))
    with pytest.raises(ValueError):
        preparation.prepare_task(queue, task_id, revision)


@pytest.fixture
def api(local_task):
    q, task, profile = local_task
    supervisor = Supervisor(q, token="x" * 40)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    session = supervisor.consume_ui_ticket(supervisor.issue_ui_ticket())
    def request(query=None, *, authenticated=True, origin=None, host=None, path="/ui/api/task-preparation", method="GET", bearer=False):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            headers = {}
            if authenticated:
                headers["Cookie"] = "application_executor_session=" + session
            if bearer:
                headers["Authorization"] = "Bearer " + supervisor.token
            if origin is not None:
                headers["Origin"] = origin
            if host is not None:
                headers["Host"] = host
            if query is None:
                query = urlencode({"task_id": task["task_id"], "expected_revision": task["revision"]})
            connection.request(method, path + "?" + query, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()
    try:
        yield q, task, profile, supervisor, request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_auth_host_origin_cache_and_no_v1_surface(api):
    q, task, profile, supervisor, request = api
    before = _snapshot(q)
    assert request(authenticated=False)[:2] == (401, {"error": "ui_session_required"})
    assert request(authenticated=False, bearer=True)[:2] == (401, {"error": "ui_session_required"})
    assert request(origin="https://untrusted.example.test")[:2] == (403, {"error": "local_origin_required"})
    assert request(host="untrusted.example.test")[:2] == (403, {"error": "local_client_required"})
    status, result, headers = request()
    assert status == 200 and result["task_id"] == task["task_id"]
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    assert request(path="/v1/task-preparation", bearer=True)[:2] == (404, {"error": "not_found"})
    assert request(method="POST")[:2] == (404, {"error": "not_found"})
    supervisor._ui_sessions = {key: 0 for key in supervisor._ui_sessions}
    assert request()[:2] == (401, {"error": "ui_session_required"})
    assert _snapshot(q) == before


@pytest.mark.parametrize("query", ["", "task_id=x", "expected_revision=0", "task_id=x&expected_revision=",
    "task_id=x&expected_revision=0&unknown=PRIVATE", "task_id=x&task_id=y&expected_revision=0",
    "task_id=x&expected_revision=0&expected_revision=1", "task_id=x&expected_revision=00",
    "task_id=x&expected_revision=-1", "task_id=x&expected_revision=true", "task_id=x&expected_revision=1.0",
    "task_id=x&expected_revision=9007199254740992", "task_id=x&expected_revision=+0",
    "task_id=x&expected_revision=0&", "task_id=%FF&expected_revision=0", "task_id=%00&expected_revision=0",
    "task_id=../private&expected_revision=0", "task_id=x;expected_revision=0", "task_id&expected_revision=0",
    "task_id=missing&expected_revision=0", "task_id=" + "x" * 2100 + "&expected_revision=0"])
def test_http_malformed_and_unknown_queries_are_sanitized(api, query):
    q, _, _, _, request = api
    before = _snapshot(q)
    assert request(query)[:2] == (400, {"error": "invalid_request"})
    assert _snapshot(q) == before


def test_http_stale_revision_cancel_race_and_errors_are_sanitized(api, monkeypatch):
    q, task, profile, _, request = api
    assert request(urlencode({"task_id": task["task_id"], "expected_revision": task["revision"] + 1}))[:2] == (409, {"error": "state_conflict"})
    def conflict(_):
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(preparation, "_profile_bytes", conflict)
    assert request()[:2] == (409, {"error": "state_conflict"})
    def unexpected(_):
        raise Exception(PRIVATE)
    monkeypatch.setattr(preparation, "_profile_bytes", unexpected)
    assert request()[:2] == (500, {"error": "internal_error"})


def test_preparation_never_calls_browser_discovery_provider_worker_or_mutation(api, monkeypatch):
    from executor.autonomy import supervisor as supervisor_module
    q, _, _, supervisor, request = api
    def forbidden(*_, **__):
        pytest.fail("preparation attempted side effect or remote observation")
    for name in ("run_mutation", "observe_task", "review_values", "observe_submission",
                 "load_configured_provider", "readiness", "dispatch"):
        monkeypatch.setattr(supervisor, name, forbidden)
    for name in ("enqueue", "cancel", "pause", "resume", "remember_task_view"):
        monkeypatch.setattr(q, name, forbidden)
    monkeypatch.setattr(settings, "load_settings", forbidden)
    monkeypatch.setattr(supervisor_module, "browser", SimpleNamespace(__getattr__=forbidden))
    monkeypatch.setattr(supervisor, "manager", SimpleNamespace(__getattr__=forbidden))
    monkeypatch.setattr(supervisor, "worker", SimpleNamespace(__getattr__=forbidden))
    before = _snapshot(q)
    assert request()[0] == 200
    assert _snapshot(q) == before


@pytest.mark.parametrize("name,kind", [("credentials.json", "resume_pdf"),
    ("resume.pdf", "photo"), ("resume.pdf", "resume_file"),
    ("resume.exe", "resume_pdf"), ("resume.docx", "resume_pdf"),
    ("private.key", "resume_key"), ("resume.pdf", "RESUME_PDF")])
def test_unsupported_resume_kind_or_extension_is_not_even_probed(local_task, tmp_path, monkeypatch, name, kind):
    q, task, profile = local_task
    candidate = _write(tmp_path / name, PRIVATE.encode())
    _write(profile, _profile(assets={"resume": {"path": str(candidate), "kind": kind}}))
    original_entry = preparation._entry
    def checked_entry(parent, child):
        assert child != candidate.name, "unsupported path was probed"
        return original_entry(parent, child)
    monkeypatch.setattr(preparation, "_entry", checked_entry)
    result = _prepare(q, task)
    assert result["resume"] == {"status": "unavailable", "version": None}


@pytest.mark.parametrize("extension,kind", [(".pdf", "resume_pdf"), (".PDF", "resume_pdf"),
    (".docx", "resume_docx"), (".doc", "resume_doc")])
def test_supported_resume_kinds_hash_without_document_parsing(local_task, tmp_path, extension, kind):
    q, task, profile = local_task
    candidate = _write(tmp_path / ("resume" + extension), b"not a valid document")
    _write(profile, _profile(assets={"resume": {"path": str(candidate), "kind": kind}}))
    result = _prepare(q, task)
    assert result["resume"] == {"status": "unverified", "version": hashlib.sha256(candidate.read_bytes()).hexdigest()}


def test_reads_are_bounded_and_exact_limits_are_admitted(local_task, tmp_path, monkeypatch):
    q, task, profile = local_task
    resume = _write(tmp_path / "resume.pdf", b"r" * preparation.RESUME_LIMIT)
    profile_data = json.dumps(_profile(assets={"resume": {"path": str(resume), "kind": "resume_pdf"}})).encode()
    _write(profile, profile_data + b" " * (preparation.PROFILE_LIMIT - len(profile_data)))
    original_read, counts = preparation.os.read, {}
    def bounded_read(fd, count):
        info = os.fstat(fd)
        assert 0 < count <= 65536
        result = original_read(fd, count)
        counts[info.st_ino] = counts.get(info.st_ino, 0) + len(result)
        return result
    monkeypatch.setattr(preparation.os, "read", bounded_read)
    result = _prepare(q, task)
    assert result["profile"]["status"] == "available" and result["resume"]["status"] == "unverified"
    assert counts == {profile.stat().st_ino: preparation.PROFILE_LIMIT, resume.stat().st_ino: preparation.RESUME_LIMIT}


def test_swap_to_fifo_between_lstat_and_open_never_blocks_or_reads(local_task, monkeypatch):
    q, task, profile = local_task
    original_open, swapped = preparation.os.open, False
    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == profile.name and not swapped:
            swapped = True
            profile.unlink()
            os.mkfifo(profile, mode=0o600)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(preparation.os, "open", swapping_open)
    monkeypatch.setattr(preparation.os, "read", lambda *_: pytest.fail("swapped FIFO read"))
    with pytest.raises(RuntimeError, match="^preparation changed$"):
        _prepare(q, task)
    assert swapped


def test_swap_to_other_owned_file_before_open_fails_identity_fence(local_task, tmp_path, monkeypatch):
    q, task, profile = local_task
    alternate = _write(tmp_path / "alternate", _profile())
    original_open, swapped = preparation.os.open, False
    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == profile.name and not swapped:
            swapped = True
            alternate.replace(profile)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(preparation.os, "open", swapping_open)
    monkeypatch.setattr(preparation.os, "read", lambda *_: pytest.fail("replacement content read"))
    with pytest.raises(RuntimeError, match="^preparation changed$"):
        _prepare(q, task)
    assert swapped


def test_read_error_is_unavailable_without_details_or_repairs(local_task, monkeypatch):
    q, task, profile = local_task
    def failed_read(*_):
        raise OSError(PRIVATE)
    monkeypatch.setattr(preparation.os, "read", failed_read)
    result = _prepare(q, task)
    assert result["profile"] == {"status": "unavailable", "version": None}
    assert PRIVATE not in json.dumps(result)


def test_pure_local_observation_opens_no_network_or_write_descriptors(local_task, monkeypatch):
    import socket
    q, task, _ = local_task
    def forbidden(*_, **__):
        pytest.fail("local preparation attempted network or file repair")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(os, "chmod", forbidden)
    monkeypatch.setattr(os, "fchmod", forbidden)
    original_open = os.open
    def readonly_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", readonly_open)
    before = _snapshot(q)
    assert _prepare(q, task)["profile"]["status"] == "available"
    assert _snapshot(q) == before


@pytest.mark.parametrize("company", ["武汉启云方科技有限公司", "启云方"])
def test_only_two_observed_company_names_match_exact_contract(tmp_path, company):
    profile = _write(tmp_path / "profile.json", _profile())
    q = TaskQueue(tmp_path / "state")
    result = _prepare(q, _task(q, profile, company=company))
    assert result["contract"]["matched"] is True
    labels = {item["key"]: item["label"] for item in result["items"]}
    assert labels["language.cet6.level"] == "英语证书情况（本地六级记录）"
    assert labels["language.cet6.score"] == "英语考级分数（本地六级记录）"
