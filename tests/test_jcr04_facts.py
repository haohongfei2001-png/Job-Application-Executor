import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import Worker
from executor.evidence import (projects_for_task, set_project_exclusion,
                               set_user_confirmed_field, write_profile)
from executor.models import (ApplicantProfile, ApplicationPlan, ApplicationStage,
                             ProfileField, ResolutionStatus, WebField)


def _task(queue, tmp_path, suffix="1"):
    profile = tmp_path / "synthetic-profile.json"
    if not profile.exists():
        profile.write_text("{}")
    return queue.enqueue(TaskSpec(
        company="Synthetic", role="Tester", job_id=suffix,
        target_url=f"https://example.test/jobs/{suffix}", profile_ref=str(profile),
    ))["task_id"]


def _wait_for_fact(queue, task_id, key="family.primary.role"):
    claim = queue.claim("synthetic-worker")
    assert claim["task_id"] == task_id
    queue.checkpoint(task_id, claim["owner"], "NEEDS_USER_INPUT",
                     blocker="unknown_facts", details={"unresolved_keys": [key]},
                     release=True)


def test_task_answer_survives_worker_restart_without_cross_task_reuse(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    first = _task(queue, tmp_path)
    _wait_for_fact(queue, first)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    canary = "PRIVATE_TASK_ONLY_CANARY"
    with pytest.raises(ValueError):
        worker.user_input(first, {"otp_code": "123456"})
    with pytest.raises(ValueError):
        worker.user_input(first, {"family.primary.role": "123456"})
    worker.user_input(first, {"family.primary.role": canary})
    assert worker.answer_store.metadata(first)[0]["answer_version"] == 1

    second = _task(queue, tmp_path, "2")
    restarted = Worker(TaskQueue(queue.root), settings={"deepseek": {"enabled": False}})
    assert restarted.answer_store.load(first) == {"family.primary.role": canary}
    assert restarted.answer_store.load(second) == {}
    assert canary.encode() not in queue.path.read_bytes()
    assert all(canary.encode() not in path.read_bytes()
               for path in queue.root.rglob("*") if path.is_file())

    class FakeRunner:
        def __init__(self, url, profile, settings, **kwargs):
            self.plan = ApplicationPlan(execution_id=kwargs["execution_id"],
                                        target_url=url, site_id="synthetic")

        def run(self):
            assert self.user_answers == [{"canonical_key": "family.primary.role",
                                          "field_id": "family.primary.role", "value": canary}]
            self.plan.stage = ApplicationStage.READY_TO_SUBMIT
            return self.plan

    restarted.runner_factory = FakeRunner
    assert restarted.run_once()
    assert queue.get(first)["stage"] == "READY_TO_SUBMIT"


def test_profile_write_crash_preserves_old_version_and_explicit_history(tmp_path, monkeypatch):
    path = tmp_path / "canonical.json"
    profile = ApplicantProfile()
    write_profile(profile, path)
    old = path.read_bytes()
    import executor.evidence as evidence
    real_replace = evidence.os.replace

    def failed_replace(*args):
        raise OSError("synthetic crash before rename")

    monkeypatch.setattr(evidence.os, "replace", failed_replace)
    with pytest.raises(OSError):
        write_profile(profile, path)
    assert path.read_bytes() == old
    assert profile.collections["profile_write_version"] == 1
    assert path.with_name(path.name + ".pre-jcr04").read_bytes() == old
    monkeypatch.setattr(evidence.os, "replace", real_replace)
    set_user_confirmed_field(path, "identity.current_city", "Synthetic City")
    data = json.loads(path.read_text())
    assert data["collections"]["profile_write_version"] == 2
    assert data["collections"]["profile_revisions"][-1]["source"] == "user_explicit"
    assert data["fields"]["identity.current_city"]["value"] == "Synthetic City"
    assert path.with_name(path.name + ".pre-jcr04").read_bytes() == old


def test_parallel_explicit_updates_preserve_both_and_reject_stale_import(tmp_path):
    path = tmp_path / "canonical.json"
    write_profile(ApplicantProfile(), path)
    stale = ApplicantProfile.model_validate_json(path.read_text())
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda pair: set_user_confirmed_field(path, *pair),
                      [("identity.current_city", "Synthetic City"),
                       ("preferences.accept_travel", False)]))
    updated = ApplicantProfile.model_validate_json(path.read_text())
    assert updated.collections["profile_write_version"] == 3
    assert len(updated.collections["profile_revisions"]) == 2
    assert set(updated.fields) == {"identity.current_city", "preferences.accept_travel"}
    with pytest.raises(RuntimeError, match="stale"):
        write_profile(stale, path)
    assert ApplicantProfile.model_validate_json(path.read_text()).collections["profile_write_version"] == 3


def test_explicit_reuse_updates_canonical_and_revokes_prior_ready_review(tmp_path):
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    ready = _task(queue, tmp_path, "ready")
    claimed = queue.claim("synthetic-worker")
    queue.checkpoint(ready, claimed["owner"], "READY_TO_SUBMIT", release=True)
    pending = _task(queue, tmp_path, "pending")
    _wait_for_fact(queue, pending, "identity.current_city")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    worker.user_input(pending, {"identity.current_city": "Synthetic City"}, remember=True)
    saved = json.loads(profile.read_text())
    assert saved["fields"]["identity.current_city"]["user_confirmed"] is True
    assert saved["fields"]["identity.current_city"]["value"] == "Synthetic City"
    assert saved["collections"]["profile_revisions"][-1]["source"] == "user_explicit"
    from executor.resolver import FieldResolver
    resolved = FieldResolver(saved, {"deepseek": {"enabled": False}}).resolve(
        WebField(field_id="new_site_city", selector="#city", label="现居城市", required=True))
    assert resolved.status == ResolutionStatus.RESOLVED
    assert resolved.value == "Synthetic City"
    assert queue.get(ready)["stage"] == "BLOCKED"
    assert queue.get(ready)["blocker"] == "profile_changed"
    assert queue.get(pending)["stage"] == "DISCOVERED"
    assert worker.answer_store.load(ready) == {}


def test_project_same_title_different_context_keeps_stable_distinct_ids():
    from executor.evidence import _merge_projects, _parse_project_lines

    records = _parse_project_lines([
        "研究平台 | 大学A | 2024.01-2024.06", "• 第一项",
        "研究平台｜大学B｜2025.01-2025.06", "• 第二项",
    ], "synthetic")
    merged = _merge_projects([], records)
    assert len(merged) == 2
    assert len({item["id"] for item in merged}) == 2
    assert _merge_projects([], records) == merged


def test_project_exclusion_is_explicit_and_single_task_only(tmp_path):
    from executor.evidence import _merge_projects
    profile = ApplicantProfile()
    profile.collections["projects"] = _merge_projects([], [{
        "title": "Synthetic research", "metadata": ["2024.01-2024.06"],
        "bullets": ["Investigated a synthetic system"],
        "source_kind": "resume_docx", "category": "research",
    }])
    path = tmp_path / "canonical.json"
    write_profile(profile, path)
    item = profile.collections["projects"][0]
    task_a = "a" * 32
    task_b = "b" * 32
    with pytest.raises(ValueError, match="reason"):
        set_project_exclusion(path, task_a, item["id"], "")
    set_project_exclusion(path, task_a, item["id"], "Form does not permit research")
    saved = ApplicantProfile.model_validate_json(path.read_text())
    assert len(saved.collections["projects"]) == 1
    assert projects_for_task(saved, task_a) == []
    assert projects_for_task(saved, task_b) == saved.collections["projects"]
    assert saved.collections["fact_exclusions"][task_a][item["id"]]["source"] == "user_explicit_task"


def test_resume_sections_distinguish_research_from_unparsed_or_absent_projects():
    from executor.evidence import _resume_projects
    records, status = _resume_projects(
        "教育背景\nSynthetic University\n科研经历\n研究平台 | 2025.01-2025.06\n"
        "• Used a synthetic fixture\n技能与语言\nPython", "resume_docx")
    assert status == "PARSED"
    assert len(records) == 1 and records[0]["category"] == "research"
    assert records[0]["title"] == "研究平台"
    assert _resume_projects("科研经历\n研究平台\n• 无分隔符项目", "resume_docx")[1] == "UNPARSED_SECTION"
    assert _resume_projects("教育背景\nSynthetic University", "resume_docx")[1] == "NO_MATCHING_SECTION"


def test_legacy_cli_answers_migrate_to_encrypted_versioned_store(tmp_path, monkeypatch):
    import executor.audit as audit
    monkeypatch.setattr(audit, "ROOT", tmp_path / "applications")
    store = audit.AuditStore("synthetic-execution")
    canary = "PRIVATE_LEGACY_ANSWER_CANARY"
    old = store.root / "user-answers.json"
    old.write_text(json.dumps({"answers": [{
        "field_id": "unknown_fact", "selector": "#unknown_fact",
        "canonical_key": "unknown_fact", "value": canary,
    }]}))
    assert store.load_user_answers()[0]["value"] == canary
    assert not old.exists()
    store.add_user_answer({"field_id": "unknown_fact", "selector": "#unknown_fact",
                           "canonical_key": "unknown_fact", "value": "updated"})
    restarted = audit.AuditStore("synthetic-execution")
    assert restarted.load_user_answers()[0]["value"] == "updated"
    assert canary.encode() not in restarted.answer_store.path.read_bytes()
    assert audit.AuditStore("another-execution").load_user_answers() == []
    with pytest.raises(ValueError):
        restarted.add_user_answer({"field_id": "security_code", "selector": "#code",
                                   "canonical_key": "unknown_fact", "value": "123456"})


def test_site_representation_requires_observed_unique_reversible_option():
    from executor.facts.representation import represent_choice
    from executor.resolver import FieldResolver
    assert represent_choice("北京市", ["北京", "上海"]) is None
    mapped = represent_choice("北京市", ["北京", "上海"], {"北京市": "北京"})
    assert mapped.site_value == "北京"
    assert mapped.canonical_value == "北京市"
    assert represent_choice("北京市", ["北京", "北京"], {"北京市": "北京"}) is None
    assert represent_choice("北京市", ["北京", "上海"],
                            {"北京市": "北京", "上海市": "北京"}) is None

    profile = ApplicantProfile(fields={"identity.current_city": ProfileField(
        value="北京市", user_confirmed=True)})
    field = WebField(field_id="city", selector="#city", label="现居城市",
                     input_type="select", options=["北京", "上海"], required=True)
    unresolved = FieldResolver(profile.model_dump(), {"deepseek": {"enabled": False}}).resolve(field)
    assert unresolved.status == ResolutionStatus.UNRESOLVED
    assert profile.fields["identity.current_city"].value == "北京市"
    field.options = ["北京市", "上海市"]
    resolved = FieldResolver(profile.model_dump(), {"deepseek": {"enabled": False}}).resolve(field)
    assert resolved.status == ResolutionStatus.RESOLVED
    assert resolved.value == "北京市"


def test_local_browser_requires_explicit_checkbox_for_reusable_fact(tmp_path):
    import threading
    from playwright.sync_api import sync_playwright
    from executor.autonomy.supervisor import Supervisor, create_server

    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, "identity.current_city")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    supervisor = Supervisor(queue, worker=worker)
    server = create_server(supervisor, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/ui-login?ticket="
                          + supervisor.issue_ui_ticket())
                page.get_by_label("identity.current_city").fill("Synthetic City")
                checkbox = page.get_by_role("checkbox", name="保存为可复用事实")
                assert not checkbox.is_checked()
                checkbox.check()
                page.get_by_role("button", name="本地填写").click()
                page.locator("button[data-answer]").wait_for(state="detached")
            finally:
                browser.close()
        saved = ApplicantProfile.model_validate_json(profile.read_text())
        assert saved.fields["identity.current_city"].value == "Synthetic City"
        assert saved.fields["identity.current_city"].user_confirmed
        assert queue.get(tid)["stage"] == "DISCOVERED"
    finally:
        server.shutdown()
        server.server_close()
