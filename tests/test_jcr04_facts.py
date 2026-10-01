import json
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import Worker
from executor.evidence import (ProfileBuilder, projects_for_task, set_project_exclusion,
                               set_user_confirmed_field, write_profile)
from executor.models import (ApplicantProfile, ApplicationPlan, ApplicationStage,
                             EvidenceRef, FieldResolution, ProfileField, ResolutionStatus, WebField)
from executor.review_certificate import certify_review


def _synthetic_review_certificate(plan):
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    snapshot = {
        "source": "server_readback",
        "target_sha256": digest(plan.target_url),
        "draft_id_digest": digest("synthetic-draft"),
        "revision": 1,
        "account_verified": True,
        "account_identity_digest": digest("synthetic-account"),
        "complete_pages": True,
        "complete_required": True,
        "save_status": "VERIFIED",
        "validation_error_count": 0,
        "hidden_required_count": 0,
        "unverified_default_count": 0,
        "document_epoch": "synthetic-session",
        "driver_version": "synthetic-v1",
        "fields": [],
        "attachments": {},
        "rows": {},
    }
    return certify_review(
        {"generated_at": "synthetic-v1"}, plan, snapshot,
        expected_account_identity_digest=digest("synthetic-account"),
    ).safe_summary()


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
            self.plan.metadata["review_certificate"] = _synthetic_review_certificate(self.plan)
            self.plan.stage = ApplicationStage.READY_TO_SUBMIT
            return self.plan

    restarted.runner_factory = FakeRunner
    assert restarted.run_once()
    assert queue.get(first)["stage"] == "READY_TO_SUBMIT"


@pytest.mark.parametrize("key,value", [
    ("education.highest.graduation_date", "2026"),
    ("identity.postal_code", "123456"),
    ("preferences.salary_policy", "10000"),
])
def test_numeric_applicant_fact_is_not_mistaken_for_otp(tmp_path, key, value):
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, key)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    worker.user_input(tid, {key: value})
    assert worker.answer_store.load(tid) == {key: value}
    assert queue.get(tid)["stage"] == "DISCOVERED"


def test_rejected_resume_rolls_back_answer_and_racing_input(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid)
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    revision = queue.get(tid)["revision"]
    original = queue._resume_in_tx

    def fail_after_answer(*args, **kwargs):
        raise RuntimeError("synthetic resume failure")

    monkeypatch.setattr(queue, "_resume_in_tx", fail_after_answer)
    with pytest.raises(RuntimeError, match="synthetic resume failure"):
        worker.user_input(tid, {"family.primary.role": "A"},
                          expected_revision=revision)
    assert worker.answer_store.load(tid) == {}
    assert queue.get(tid)["stage"] == "NEEDS_USER_INPUT"
    monkeypatch.setattr(queue, "_resume_in_tx", original)

    def submit(value):
        try:
            worker.user_input(tid, {"family.primary.role": value},
                              expected_revision=revision)
            return "accepted", value
        except RuntimeError:
            return "stale", value

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(submit, ("A", "B")))
    assert {result for result, _ in outcomes} == {"accepted", "stale"}
    accepted = next(value for result, value in outcomes if result == "accepted")
    assert worker.answer_store.load(tid) == {"family.primary.role": accepted}
    assert len(worker.answer_store.metadata(tid)) == 1


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
    ready_plan = ApplicationPlan(
        execution_id=ready,
        target_url=queue.get(ready)["spec"]["target_url"],
        site_id="synthetic",
    )
    queue.checkpoint(
        ready, claimed["owner"], "READY_TO_SUBMIT",
        details={"unresolved_keys": [],
                 "review_certificate": _synthetic_review_certificate(ready_plan)},
        release=True,
    )
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


def test_boolean_fact_is_typed_before_task_save_and_reuse(tmp_path):
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, "preferences.accept_travel")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    with pytest.raises(ValueError, match="yes or no"):
        worker.user_input(tid, {"preferences.accept_travel": "maybe"}, remember=True)
    worker.user_input(tid, {"preferences.accept_travel": "false"}, remember=True)
    assert worker.answer_store.load(tid) == {"preferences.accept_travel": False}
    saved = ApplicantProfile.model_validate_json(profile.read_text())
    assert saved.fields["preferences.accept_travel"].value is False


def test_reusable_fact_promotion_retries_without_duplicate_revision(tmp_path, monkeypatch):
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, "identity.current_city")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    real_mark = worker.answer_store.mark_reuse_applied

    def crash_after_profile_write(*args, **kwargs):
        raise RuntimeError("synthetic crash after canonical replace")

    monkeypatch.setattr(worker.answer_store, "mark_reuse_applied", crash_after_profile_write)
    result = worker.user_input(tid, {"identity.current_city": "Synthetic City"}, remember=True)
    assert result["fact_reuse_status"] == "PENDING"
    assert queue.get(tid)["stage"] == "BLOCKED"
    assert queue.get(tid)["blocker"] == "profile_promotion_pending"
    assert queue.claim("other-worker") is None
    version = ApplicantProfile.model_validate_json(profile.read_text()).collections["profile_write_version"]
    monkeypatch.setattr(worker.answer_store, "mark_reuse_applied", real_mark)
    restarted = Worker(TaskQueue(queue.root), settings={"deepseek": {"enabled": False}})
    assert restarted._promote_pending_facts(tid, str(profile))
    assert restarted.answer_store.pending_reuse(tid) == []
    assert queue.get(tid)["stage"] == "DISCOVERED"
    assert ApplicantProfile.model_validate_json(profile.read_text()).collections["profile_write_version"] == version


def test_final_promotion_marker_and_resume_roll_back_together(tmp_path, monkeypatch):
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, "identity.current_city")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    original = queue._resume_in_tx
    monkeypatch.setattr(queue, "_resume_in_tx",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("synthetic crash before atomic resume")))
    result = worker.user_input(tid, {"identity.current_city": "Synthetic City"}, remember=True)
    assert result["fact_reuse_status"] == "PENDING"
    assert queue.get(tid)["blocker"] == "profile_promotion_pending"
    assert len(worker.answer_store.pending_reuse(tid)) == 1
    version = ApplicantProfile.model_validate_json(profile.read_text()).collections["profile_write_version"]
    monkeypatch.setattr(queue, "_resume_in_tx", original)
    restarted = Worker(TaskQueue(queue.root), settings={"deepseek": {"enabled": False}})
    assert restarted._promote_pending_facts(tid, str(profile))
    assert restarted.answer_store.pending_reuse(tid) == []
    assert queue.get(tid)["stage"] == "DISCOVERED"
    assert ApplicantProfile.model_validate_json(profile.read_text()).collections["profile_write_version"] == version


def test_profile_write_barrier_rejects_new_claim_before_file_replace(tmp_path, monkeypatch):
    import executor.autonomy.worker as worker_module
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    source = _task(queue, tmp_path, "source")
    _wait_for_fact(queue, source, "identity.current_city")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    original = worker_module.set_user_confirmed_field
    observed = []

    def race_between_fence_and_write(*args, **kwargs):
        newcomer = _task(queue, tmp_path, "newcomer")
        observed.append((newcomer, TaskQueue(queue.root).claim("new-worker")))
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, "set_user_confirmed_field", race_between_fence_and_write)
    worker.user_input(source, {"identity.current_city": "Synthetic City"}, remember=True)
    assert observed and observed[0][1] is None
    assert queue.get(observed[0][0])["stage"] == "DISCOVERED"
    with queue.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM profile_write_barriers").fetchone()[0] == 0


@pytest.mark.parametrize("control", ["pause", "cancel"])
def test_profile_promotion_never_resumes_a_user_stopped_task(tmp_path, monkeypatch, control):
    import executor.autonomy.worker as worker_module
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, "identity.current_city")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    original = worker_module.set_user_confirmed_field

    def stop_during_promotion(*args, **kwargs):
        getattr(queue, control)(tid)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker_module, "set_user_confirmed_field", stop_during_promotion)
    result = worker.user_input(tid, {"identity.current_city": "Synthetic City"}, remember=True)
    assert result["fact_reuse_status"] == "SAVED"
    task = queue.get(tid)
    assert task["stage"] == ("CANCELLED" if control == "cancel" else "BLOCKED")
    if control == "pause":
        assert task["blocker"].startswith("user_paused")
    with queue.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM profile_write_barriers").fetchone()[0] == 0


def test_project_same_title_different_context_keeps_stable_distinct_ids():
    from executor.evidence import _merge_projects, _parse_project_lines

    records = _parse_project_lines([
        "研究平台 | 大学A | 2024.01-2024.06", "• 第一项",
        "研究平台｜大学B｜2025.01-2025.06", "• 第二项",
    ], "synthetic")
    merged = _merge_projects([], records)
    assert len(merged) == 2
    assert len({item["id"] for item in merged}) == 2
    assert merged[0]["source_refs"] == [{"kind": "synthetic", "locator": "line:0"}]
    assert _merge_projects([], records) == merged


def test_same_project_merges_across_max_and_resume_categories():
    from executor.evidence import _merge_projects
    max_item = {"title": "Research platform", "metadata": ["University A", "2024.01-2024.06"],
                "bullets": ["Synthetic work"], "source_kind": "max_docx",
                "category": "project_or_research", "source_refs": [{"kind": "max_docx", "locator": "line:1"}]}
    resume_item = {"title": "Research platform", "metadata": ["University A", "2024.01-2024.06"],
                   "bullets": ["Synthetic work"], "source_kind": "resume_docx",
                   "category": "research", "source_refs": [{"kind": "resume_docx", "locator": "line:2"}]}
    merged = _merge_projects([max_item], [resume_item])
    assert len(merged) == 1
    assert merged[0]["category"] == "research"
    assert merged[0]["sources"] == ["max_docx", "resume_docx"]
    assert len(merged[0]["source_refs"]) == 2
    assert _merge_projects(merged, [resume_item]) == merged
    distinct = {**resume_item, "metadata": ["University B", "2025.01-2025.06"]}
    assert len(_merge_projects(merged, [distinct])) == 2


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
    from executor.review import project_coverage_review
    for task_id, expected in ((task_a, []), (task_b, ["Synthetic research"])):
        plan = ApplicationPlan(execution_id=task_id,
                               target_url="https://example.test/jobs/1", site_id="synthetic")
        assert project_coverage_review(saved.model_dump(), plan)["uncovered_projects"] == expected


def test_one_same_title_exclusion_cannot_hide_another_project():
    from executor.review import project_coverage_review
    profile = ApplicantProfile()
    profile.collections["projects"] = [
        {"id": "project-a", "title": "Shared title", "metadata": ["2024"]},
        {"id": "project-b", "title": "Shared title", "metadata": ["2025"]},
    ]
    task_id = "a" * 32
    profile.collections["fact_exclusions"] = {task_id: {
        "project-a": {"reason": "Not relevant to this role", "source": "user_explicit_task"}}}
    plan = ApplicationPlan(execution_id=task_id,
                           target_url="https://example.test/jobs/1", site_id="synthetic")
    assert project_coverage_review(profile.model_dump(), plan)["uncovered_projects"] == ["Shared title"]
    profile.collections["fact_exclusions"][task_id]["project-b"] = {
        "reason": "No project section on form", "source": "user_explicit_task"}
    assert project_coverage_review(profile.model_dump(), plan)["uncovered_projects"] == []


def test_two_same_title_projects_need_two_distinct_form_rows():
    from executor.review import project_coverage_review
    from executor.models import FieldResolution

    profile = ApplicantProfile()
    profile.collections["projects"] = [
        {"id": "project-a", "title": "Shared title", "metadata": ["2024"]},
        {"id": "project-b", "title": "Shared title", "metadata": ["2025"]},
    ]
    plan = ApplicationPlan(execution_id="synthetic-review",
                           target_url="https://example.test/jobs/1", site_id="synthetic")
    one = FieldResolution(field_id="project_name_1", selector="#project-1",
                          label="项目名称", status=ResolutionStatus.RESOLVED,
                          value="Shared title", record_id="project-a")
    two = FieldResolution(field_id="project_name_2", selector="#project-2",
                          label="项目名称", status=ResolutionStatus.RESOLVED,
                          value="Shared title", record_id="project-b")
    plan.fields = [one.model_copy(update={"record_id": None}),
                   two.model_copy(update={"record_id": None})]
    assert project_coverage_review(profile.model_dump(), plan)["uncovered_projects"] == [
        "Shared title", "Shared title"]
    plan.fields = [one]
    assert project_coverage_review(profile.model_dump(), plan)["uncovered_projects"] == ["Shared title"]
    plan.fields = [one, two]
    assert project_coverage_review(profile.model_dump(), plan)["uncovered_projects"] == []


def test_profile_promotion_fences_claimed_peer_before_writing(tmp_path):
    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    pending = _task(queue, tmp_path, "pending")
    _wait_for_fact(queue, pending, "identity.current_city")
    active = _task(queue, tmp_path, "active")
    claimed = queue.claim("active-worker")
    assert claimed["task_id"] == active
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    worker.user_input(pending, {"identity.current_city": "Synthetic City"}, remember=True)
    assert queue.get(active)["stage"] == "BLOCKED"
    assert queue.get(active)["blocker"] == "profile_changed"
    assert queue.get(active)["owner"] is None
    with pytest.raises(RuntimeError, match="lease"):
        queue.checkpoint(active, claimed["owner"], "READY_TO_SUBMIT", release=True)


def test_resume_sections_distinguish_research_from_unparsed_or_absent_projects():
    from executor.evidence import _resume_projects
    records, status = _resume_projects(
        "教育背景\nSynthetic University\n科研经历\n研究平台 | 2025.01-2025.06\n"
        "• Used a synthetic fixture\n技能与语言\nPython", "resume_docx")
    assert status == "PARSED"
    assert len(records) == 1 and records[0]["category"] == "research"
    assert records[0]["title"] == "研究平台"
    assert _resume_projects("科研经历\n研究平台\n• 无分隔符项目", "resume_docx")[1] == "UNPARSED_SECTION"
    english, english_status = _resume_projects(
        "Education\nSynthetic University\nResearch Projects\nSynthetic Study | 2025.01-2025.06\n"
        "• Controlled synthetic evidence\nWork Experience\nSynthetic Company", "resume_docx")
    assert english_status == "PARSED"
    assert len(english) == 1 and english[0]["category"] == "research"
    assert _resume_projects("Research Projects\nUnstructured Study", "resume_docx")[1] == "UNPARSED_SECTION"
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


def test_ai_mapped_select_also_requires_observed_reversible_option(monkeypatch):
    from executor.resolver import FieldResolver
    profile = ApplicantProfile(fields={"identity.current_city": ProfileField(
        value="北京市", user_confirmed=True)})
    resolver = FieldResolver(profile.model_dump(), {"deepseek": {"enabled": False}})
    monkeypatch.setattr(resolver.ai, "map_field",
                        lambda *_: ("identity.current_city", 0.95, "synthetic mapping"))
    field = WebField(field_id="opaque", selector="#opaque", label="Opaque location token",
                     input_type="select", options=["北京", "上海"], required=True)
    item = resolver.resolve(field)
    assert item.status == ResolutionStatus.UNRESOLVED
    assert "representation" in item.reason
    field.options = ["北京市", "上海市"]
    assert resolver.resolve(field).value == "北京市"


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
    observed = queue.get(tid)
    worker._remember_question_context(
        tid, ApplicationPlan(execution_id=tid, target_url=observed["spec"]["target_url"],
                             site_id="synthetic", unresolved_fields=[
            FieldResolution(field_id="site-question", selector="#site-question",
                            label="当前居住城市", canonical_key="identity.current_city",
                            status=ResolutionStatus.UNRESOLVED, required=True)]),
        observed["revision"])
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
                page.get_by_label("当前居住城市").fill("Synthetic City")
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


def test_local_boolean_control_persists_false_as_boolean(tmp_path):
    import threading
    from playwright.sync_api import sync_playwright
    from executor.autonomy.supervisor import Supervisor, create_server

    profile = tmp_path / "synthetic-profile.json"
    write_profile(ApplicantProfile(), profile)
    queue = TaskQueue(tmp_path / "runtime")
    tid = _task(queue, tmp_path)
    _wait_for_fact(queue, tid, "preferences.accept_travel")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    observed = queue.get(tid)
    worker._remember_question_context(
        tid, ApplicationPlan(execution_id=tid, target_url=observed["spec"]["target_url"],
                             site_id="synthetic", unresolved_fields=[
            FieldResolution(field_id="site-question", selector="#site-question",
                            label="是否接受出差", canonical_key="preferences.accept_travel",
                            status=ResolutionStatus.UNRESOLVED, required=True)]),
        observed["revision"])
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
                page.get_by_label("是否接受出差").select_option(label="否")
                page.get_by_role("checkbox", name="保存为可复用事实").check()
                page.get_by_role("button", name="本地填写").click()
                page.locator("button[data-answer]").wait_for(state="detached")
            finally:
                browser.close()
        assert worker.answer_store.load(tid)["preferences.accept_travel"] is False
        assert ApplicantProfile.model_validate_json(profile.read_text()).fields[
            "preferences.accept_travel"].value is False
    finally:
        server.shutdown()
        server.server_close()


def test_structured_education_record_keeps_id_and_field_evidence_on_correction(tmp_path):
    builder = ProfileBuilder()
    source = EvidenceRef(kind="synthetic_resume", locator="education:1")
    builder.add_field("education.highest.school", "Synthetic University", source)
    builder.add_field("education.highest.degree", "Master", source)
    builder.add_field("education.bachelor.school", "Synthetic College", source)
    profile = builder.build()
    records = profile.collections["education_records"]
    assert {item["scope"] for item in records} == {"highest", "bachelor"}
    assert all(item["status"] == "PARTIAL" for item in records)
    highest = next(item for item in records if item["scope"] == "highest")
    assert highest["fields"]["school"]["sources"][0]["locator"] == "education:1"
    path = tmp_path / "canonical.json"
    write_profile(profile, path)
    set_user_confirmed_field(path, "education.highest.school", "Corrected University")
    updated = ApplicantProfile.model_validate_json(path.read_text())
    corrected = next(item for item in updated.collections["education_records"]
                     if item["scope"] == "highest")
    assert corrected["id"] == highest["id"]
    assert corrected["fields"]["school"]["value"] == "Corrected University"


def test_conflicting_source_values_stay_unknown_until_explicit_confirmation(tmp_path):
    from executor.resolver import FieldResolver
    builder = ProfileBuilder()
    builder.add_field("identity.current_city", "City A",
                      EvidenceRef(kind="resume_docx", locator="line:1"))
    builder.add_field("identity.current_city", "City B",
                      EvidenceRef(kind="legacy_profile", locator="identity.city"))
    profile = builder.build()
    selected_before_confirmation = profile.fields["identity.current_city"].value
    field = WebField(field_id="city", selector="#city", label="现居城市", required=True)
    before = FieldResolver(profile.model_dump(), {"deepseek": {"enabled": False}}).resolve(field)
    assert before.status == ResolutionStatus.UNRESOLVED
    assert "conflicting sources" in before.reason
    path = tmp_path / "canonical.json"
    write_profile(profile, path)
    set_user_confirmed_field(path, "identity.current_city", "City B")
    confirmed = ApplicantProfile.model_validate_json(path.read_text())
    after = FieldResolver(confirmed.model_dump(), {"deepseek": {"enabled": False}}).resolve(field)
    assert after.status == ResolutionStatus.RESOLVED
    assert after.value == "City B"
    assert confirmed.collections["profile_revisions"][-1]["previous_value"] == selected_before_confirmation
    assert {profile.collections["conflicts"][0]["existing_value"],
            profile.collections["conflicts"][0]["incoming_value"]} == {"City A", "City B"}


def test_expired_fact_is_not_reused_or_sent_to_site():
    from executor.resolver import FieldResolver
    profile = ApplicantProfile(fields={"identity.current_city": ProfileField(
        value="Old City", user_confirmed=True,
        normalization={"valid_until": "2000-01-01"})})
    field = WebField(field_id="city", selector="#city", label="现居城市", required=True)
    result = FieldResolver(profile.model_dump(), {"deepseek": {"enabled": False}}).resolve(field)
    assert result.status == ResolutionStatus.UNRESOLVED
    assert result.value is None
    assert "validity" in result.reason
