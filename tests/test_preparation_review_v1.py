"""Private exact-task proposals remain local and never confer browser authority."""
import hashlib
import json
from urllib.parse import urlencode

import pytest

from executor.autonomy import preparation_review as review
from executor.preparation import qiyunfang
from test_task_preparation_v1 import api, local_task, _write, _profile, _snapshot, _task


def test_private_projection_binds_original_task_bytes_and_omits_protected_values(local_task, tmp_path):
    queue, task, path = local_task
    source = _profile(); source["fields"].update({"identity.gender": {"value": "男"},
        "identity.full_name": {"value": "PRIVATE_REVIEW_CANARY"},
        "identity.id_number": {"value": "ID_MUST_NEVER_BE_PROJECTED"}})
    _write(path, source)
    before = _snapshot(queue)
    result = review.review_preparation(queue, task["task_id"], task["revision"])
    assert result["mode"] == "PRIVATE_MAPPING_REVIEW"
    assert result["state"] == "AWAITING_LIVE_PREFLIGHT"
    assert result["profile_version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    encoded = json.dumps(result)
    assert "PRIVATE_REVIEW_CANARY" in encoded and "ID_MUST_NEVER_BE_PROJECTED" not in encoded
    assert str(tmp_path) not in encoded
    assert all(value is False for value in result["capabilities"].values())
    assert _snapshot(queue) == before
    assert {item["field_id"] for item in result["proposals"]} == {field.field_id for field in qiyunfang.ROUTINE_FIELDS}


def test_private_route_needs_cookie_session_and_is_no_store(api):
    queue, task, path, supervisor, request = api
    kwargs = {"path": "/ui/api/preparation-review"}
    before = _snapshot(queue)
    assert request(authenticated=False, **kwargs)[:2] == (401, {"error": "ui_session_required"})
    assert request(authenticated=False, bearer=True, **kwargs)[0] == 401
    assert request(origin="https://untrusted.example.test", **kwargs)[0] == 403
    assert request(host="evil.test", **kwargs)[0] == 403
    status, result, headers = request(**kwargs)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "PRIVATE_PREPARATION_CANARY" in json.dumps(result)
    assert "PRIVATE_PREPARATION_CANARY_ID" not in json.dumps(result)
    assert request(method="POST", **kwargs)[0] == 404
    assert request(path="/v1/preparation-review", bearer=True)[0] == 404
    supervisor._ui_sessions = {key: 0 for key in supervisor._ui_sessions}
    assert request(**kwargs)[0] == 401
    assert _snapshot(queue) == before


@pytest.mark.parametrize("query", ["", "task_id=x", "task_id=x&expected_revision=00", "task_id=x&expected_revision=0&unknown=secret",
    "task_id=x&task_id=y&expected_revision=0", "task_id=missing&expected_revision=0"])
def test_private_route_rejects_ambiguous_queries_without_leaking_values(api, query):
    *_, request = api
    assert request(query, path="/ui/api/preparation-review")[:2] == (400, {"error": "invalid_request"})


@pytest.mark.parametrize("change", ["profile_bytes", "profile_path", "task_revision", "resume_bytes", "cancel"])
def test_changes_during_review_fail_closed_before_private_response(local_task, tmp_path, monkeypatch, change):
    queue, task, profile = local_task
    resume = _write(tmp_path / "resume.pdf", b"synthetic")
    _write(profile, _profile(assets={"resume": {"path": str(resume), "kind": "resume_pdf"}}))
    original = review.map_routine_fields
    def mutate(source):
        result = original(source)
        if change == "profile_bytes": _write(profile, {"fields": {}})
        elif change == "profile_path":
            replacement = _write(tmp_path / "replacement", profile.read_bytes()); replacement.replace(profile)
        elif change == "resume_bytes": _write(resume, b"different")
        elif change == "cancel": queue.cancel(task["task_id"])
        else:
            with queue.tx() as db: db.execute("UPDATE tasks SET revision=revision+1 WHERE task_id=?", (task["task_id"],))
        return result
    monkeypatch.setattr(review, "map_routine_fields", mutate)
    with pytest.raises(RuntimeError): review.review_preparation(queue, task["task_id"], task["revision"])


def test_unmatched_target_or_unreadable_profile_has_no_private_projection(local_task, tmp_path):
    queue, task, profile = local_task
    other = _task(queue, profile, target_url="https://example.test/jobs/1")
    with pytest.raises(ValueError): review.review_preparation(queue, other["task_id"], other["revision"])
    profile.chmod(0o644)
    with pytest.raises(ValueError): review.review_preparation(queue, task["task_id"], task["revision"])


def test_private_review_never_connects_browser_or_consults_current_profile(api, monkeypatch):
    from executor.autonomy import supervisor as module
    from executor import settings
    queue, task, _, supervisor, request = api
    def denied(*_, **__): pytest.fail("private review attempted external or mutable operation")
    for method in ("run_mutation", "observe_task", "review_values", "observe_submission", "dispatch"):
        monkeypatch.setattr(supervisor, method, denied)
    monkeypatch.setattr(settings, "load_settings", denied)
    monkeypatch.setattr(module.browser, "connect", denied)
    assert request(path="/ui/api/preparation-review")[0] == 200
