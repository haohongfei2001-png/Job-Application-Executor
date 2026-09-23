from __future__ import annotations

from executor.discovery.core import (
    DiscoveryCandidate, DiscoveryRequest, DiscoverySnapshot, evaluate_snapshot,
)
from executor.discovery import service
from executor.target_resolver import Candidate, OPPO_CAMPUS_ROOT
from executor import target_resolver
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.manager import ManagerController, _local_discovery_intent
from executor.protected_targets import protected_target
from executor.discovery.ats_routes import parse_ats_route, verified_official_ats_target
import pytest
import threading
from playwright.sync_api import sync_playwright
from executor.autonomy.supervisor import Supervisor, create_server
from executor.autonomy.worker import Worker


def candidate(job_id, *, location="北京", tenant="oppo-campus", campaign="Campus",
              title="AI 产品经理", host="careers.oppo.com"):
    return DiscoveryCandidate(
        employer="OPPO", tenant=tenant, job_id=job_id, title=title,
        location=location, campaign=campaign, employment_type="campus",
        detail_url=f"https://{host}/university/oppo/campus/post/{job_id}",
        platform="oppo",
    )


def snapshot(*items, complete=True, source_verified=True):
    return DiscoverySnapshot(
        candidates=tuple(items), complete=complete,
        source_chain=(OPPO_CAMPUS_ROOT,), allowed_hosts=("careers.oppo.com",),
        observed_at="2026-09-24T00:00:00Z", coverage="fixture:all_pages",
        source_verified=source_verified,
    )


def test_unique_target_needs_complete_listing_and_exact_constraints():
    request = DiscoveryRequest("OPPO", "AI产品经理", location="北京", campaign="Campus")
    one = candidate("101")
    incomplete = evaluate_snapshot(request, snapshot(one, complete=False))
    assert incomplete.status == "INCOMPLETE" and incomplete.target is None
    verified = evaluate_snapshot(request, snapshot(one))
    assert verified.status == "VERIFIED"
    assert verified.target.job_id == "101"
    assert verified.target.tenant == "oppo-campus"
    assert len(verified.target.evidence_digest) == 64


def test_same_title_requires_choice_and_rechecks_stable_candidate_id():
    request = DiscoveryRequest("OPPO", "AI产品经理")
    beijing, shenzhen = candidate("101"), candidate("102", location="深圳")
    ambiguous = evaluate_snapshot(request, snapshot(beijing, shenzhen))
    assert ambiguous.status == "AMBIGUOUS" and ambiguous.target is None
    chosen = evaluate_snapshot(request, snapshot(beijing, shenzhen),
                               selected_candidate_id=shenzhen.candidate_id)
    assert chosen.status == "VERIFIED" and chosen.target.job_id == "102"
    changed = evaluate_snapshot(request, snapshot(beijing),
                                selected_candidate_id=shenzhen.candidate_id)
    assert changed.status == "UNAVAILABLE" and changed.target is None


def test_location_campaign_employment_and_exact_url_cannot_be_ignored():
    beijing = candidate("101")
    assert evaluate_snapshot(DiscoveryRequest("OPPO", "AI产品经理", location="深圳"),
                             snapshot(beijing)).status == "UNAVAILABLE"
    assert evaluate_snapshot(DiscoveryRequest("OPPO", "AI产品经理", campaign="Social"),
                             snapshot(beijing)).status == "UNAVAILABLE"
    assert evaluate_snapshot(DiscoveryRequest("OPPO", "AI产品经理", employment_type="social"),
                             snapshot(beijing)).status == "UNAVAILABLE"
    wrong_url = OPPO_CAMPUS_ROOT + "/post/999"
    assert evaluate_snapshot(DiscoveryRequest("OPPO", "AI产品经理", source_url=wrong_url),
                             snapshot(beijing)).status == "UNAVAILABLE"


def test_untrusted_source_and_cross_origin_candidate_cannot_verify():
    request = DiscoveryRequest("OPPO", "AI产品经理")
    assert evaluate_snapshot(request, snapshot(candidate("101"), source_verified=False)).status == "UNSUPPORTED"
    assert evaluate_snapshot(request, snapshot(candidate("101", host="evil.example"))).status == "UNAVAILABLE"


def test_same_job_id_across_tenants_has_distinct_identity():
    first, second = candidate("101", tenant="tenant-a"), candidate("101", tenant="tenant-b")
    assert first.target_key != second.target_key
    assert first.candidate_id != second.candidate_id
    result = evaluate_snapshot(DiscoveryRequest("OPPO", "AI产品经理"), snapshot(first, second))
    assert result.status == "AMBIGUOUS"


def test_known_company_without_url_uses_readonly_provider_and_keeps_ambiguity(monkeypatch):
    items = [
        Candidate("AI 产品经理", "101", "北京", "产品", OPPO_CAMPUS_ROOT + "/post/101?recruitType=Campus"),
        Candidate("AI 产品经理", "102", "深圳", "产品", OPPO_CAMPUS_ROOT + "/post/102?recruitType=Campus"),
    ]
    seen = []
    def fake_resolve(title, location, *, return_coverage):
        seen.append((title, location, return_coverage))
        return items, True
    monkeypatch.setattr(service, "resolve_oppo", fake_resolve)
    result = service.discover(DiscoveryRequest("OPPO", "AI产品经理"))
    assert result.status == "AMBIGUOUS"
    assert len(result.candidates) == 2
    assert seen == [("AI产品经理", "", True)]
    selected = service.discover(DiscoveryRequest("OPPO", "AI产品经理"),
                                selected_candidate_id=result.candidates[1].candidate_id)
    assert selected.status == "VERIFIED"
    assert selected.target.job_id == result.candidates[1].job_id


def test_unknown_or_unofficial_source_does_not_invoke_public_provider(monkeypatch):
    monkeypatch.setattr(service, "resolve_oppo", lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    assert service.discover(DiscoveryRequest("Unknown Co", "Engineer")).status == "UNSUPPORTED"
    result = service.discover(DiscoveryRequest(
        "OPPO", "AI产品经理", source_url="https://evil.example/university/oppo/campus"))
    assert result.status == "UNSUPPORTED"
    assert service.discover(DiscoveryRequest(
        "OPPO", "AI产品经理", source_url=OPPO_CAMPUS_ROOT + "/post/not-a-job")).status == "UNSUPPORTED"
    with pytest.raises(ValueError):
        DiscoveryRequest("OPPO", "AI产品经理",
                         source_url=OPPO_CAMPUS_ROOT + "?access_token=secret")
    with pytest.raises(ValueError):
        DiscoveryRequest("OPPO", "AI产品经理",
                         source_url=OPPO_CAMPUS_ROOT + "#access_token=secret")


def test_shared_ats_job_id_is_scoped_by_verified_tenant_and_protected_alias(tmp_path):
    queue = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "synthetic-profile.json"
    profile.write_text("{}")
    common = dict(company="Synthetic Co", role="Engineer", job_id="123",
                  target_url="https://boards.example.test/jobs/123",
                  profile_ref=str(profile))
    tenant_a = queue.enqueue(TaskSpec(**common, tenant="tenant-a"))
    tenant_b = queue.enqueue(TaskSpec(**common, tenant="tenant-b"))
    assert tenant_a["task_id"] != tenant_b["task_id"]
    assert queue.enqueue(TaskSpec(**common, tenant="tenant-a"))["task_id"] == tenant_a["task_id"]
    rules = [{"target_identity": {"tenant": "tenant-a", "job_id": "123", "campaign": ""},
              "status": "SUBMITTED"}]
    assert protected_target("https://alternate.example.test/apply/123", rules,
                            tenant="tenant-a", job_id="123")
    assert protected_target("https://alternate.example.test/apply/123", rules,
                            tenant="tenant-b", job_id="123") is None
    with_campaign = {**common, "job_id": "456", "target_url": "https://boards.example.test/jobs/456"}
    first_campaign = queue.enqueue(TaskSpec(**with_campaign, tenant="tenant-a", campaign="2027 届 校园招聘"))
    same_campaign = queue.enqueue(TaskSpec(**with_campaign, tenant="tenant-a", campaign="2027届校园招聘"))
    assert first_campaign["task_id"] == same_campaign["task_id"]
    campaign_rule = [{"target_identity": {"tenant": "tenant-a", "job_id": "456",
                                           "campaign": "2027届校园招聘"}}]
    assert protected_target("https://alternate.example.test/jobs/456", campaign_rule,
                            tenant="tenant-a", job_id="456", campaign="2027 届 校园招聘")


def test_local_company_role_entry_creates_only_after_explicit_candidate_choice(tmp_path, monkeypatch):
    from executor.autonomy import manager as manager_module

    queue = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "synthetic-profile.json"
    profile.write_text("{}")
    controller = ManagerController(queue, object(), settings={"profile_path": str(profile)})
    first, second = candidate("101"), candidate("102", location="深圳")
    observed = snapshot(first, second)
    monkeypatch.setattr(manager_module, "discover", lambda request, selected_candidate_id="":
                        evaluate_snapshot(request, observed, selected_candidate_id=selected_candidate_id))

    ambiguous = controller.create_from_local_form("OPPO", "AI产品经理")
    assert ambiguous["discovery"]["status"] == "AMBIGUOUS"
    assert queue.tasks() == []
    created = controller.create_from_local_form(
        "OPPO", "AI产品经理", selected_candidate_id=second.candidate_id)
    task = queue.get(created["task_id"])
    assert task["spec"]["job_id"] == "102"
    assert task["spec"]["tenant"] == "oppo-campus"
    assert task["spec"]["target_verified"] is True
    assert task["spec"]["live_authorized"] is False
    assert len(task["spec"]["target_evidence_digest"]) == 64


def test_unofficial_url_for_known_company_is_not_enqueued(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "synthetic-profile.json"
    profile.write_text("{}")
    controller = ManagerController(queue, object(), settings={"profile_path": str(profile)})
    result = controller.create_from_local_form(
        "OPPO", "AI产品经理", "https://evil.example/university/oppo/campus")
    assert result["discovery"]["status"] == "UNSUPPORTED"
    assert queue.tasks() == []


def test_explicit_company_role_chat_discovers_locally_without_model_or_url(tmp_path, monkeypatch):
    from executor.autonomy import manager as manager_module

    class ForbiddenProvider:
        available = True
        def decide(self, *_):
            raise AssertionError("raw discovery intent must stay local")

    queue = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "synthetic-profile.json"
    profile.write_text("{}")
    controller = ManagerController(queue, object(), provider=ForbiddenProvider(),
                                   settings={"profile_path": str(profile)})
    monkeypatch.setattr(manager_module, "discover", lambda request, selected_candidate_id="":
                        evaluate_snapshot(request, snapshot(candidate("101")),
                                          selected_candidate_id=selected_candidate_id))
    result = controller.handle("请投递 OPPO 的 AI产品经理（北京）")
    assert result["actions"][0]["status"] == "accepted"
    task = queue.get(result["actions"][0]["task_id"])
    assert task["spec"]["job_id"] == "101"
    assert task["spec"]["location"] == "北京"
    assert _local_discovery_intent("请不要投递 OPPO 的 AI产品经理") is None
    assert _local_discovery_intent("看看 OPPO 的 AI产品经理") is None


def test_official_to_shared_ats_route_binds_tenant_and_job_without_trusting_host_alone():
    official = "https://careers.example.test/jobs"
    lever_board = "https://jobs.lever.co/exampleco"
    lever_job = lever_board + "/12345678-aaaa-bbbb-cccc-123456789abc?tracking=discarded"
    route = verified_official_ats_target(
        official_source_url=official, official_host="careers.example.test",
        observed_link=lever_board, candidate_url=lever_job)
    assert route is not None
    assert route.platform == "lever" and route.tenant == "exampleco"
    assert route.canonical_url == lever_job.split("?")[0]
    assert verified_official_ats_target(
        official_source_url=official, official_host="careers.example.test",
        observed_link=lever_board,
        candidate_url=lever_job.replace("exampleco", "otherco")) is None
    assert verified_official_ats_target(
        official_source_url="https://evil.example/jobs",
        official_host="careers.example.test", observed_link=lever_board,
        candidate_url=lever_job) is None


def test_greenhouse_route_and_wrong_job_redirect_are_distinct():
    listed = "https://boards.greenhouse.io/exampleco/jobs/1234567"
    correct = "https://job-boards.greenhouse.io/exampleco/jobs/1234567"
    wrong = "https://job-boards.greenhouse.io/exampleco/jobs/7654321"
    assert parse_ats_route(correct).job_id == "1234567"
    assert verified_official_ats_target(
        official_source_url="https://example.test/careers", official_host="example.test",
        observed_link=listed, candidate_url=correct) is not None
    assert verified_official_ats_target(
        official_source_url="https://example.test/careers", official_host="example.test",
        observed_link=listed, candidate_url=wrong) is None
    assert parse_ats_route(correct + "#access_token=secret") is None


def test_oppo_public_listing_requires_all_pages_and_matching_detail(monkeypatch):
    def record(index):
        return {"idRecruitPosition": index, "positionName": "AI 产品经理" if index == 11 else "其他岗位",
                "workCityName": "北京市", "projectName": "2027届应届生校园招聘",
                "recruitmentTypeName": "应届生", "positionTypeName": "产品类"}

    class Response:
        status = 200
        def __init__(self, body):
            self.body = body
        def json(self):
            return self.body

    class Request:
        def __init__(self):
            self.page_numbers = []
            self.bad_page = False
            self.bad_detail = False
        def post(self, url, *, data, headers, timeout):
            assert url.endswith("/openapi/position/pageNew")
            number = data["pageNum"]
            self.page_numbers.append(number)
            records = [record(i) for i in range(1, 11)] if number == 1 else [record(11)]
            return Response({"code": 0, "data": {"records": records, "total": 11,
                "pages": 2, "current": 1 if self.bad_page else number}})
        def get(self, url, *, timeout):
            assert url.endswith("/openapi/position/detail?id=11")
            return Response({"code": 0, "data": {
                **record(11), "idRecruitPosition": 99 if self.bad_detail else 11,
                "workCityVOList": [{"workCityName": "北京市"}], "positionStatus": 0}})

    class Page:
        def goto(self, url, **kwargs):
            assert url == target_resolver.OPPO_CAMPUS_POST_LIST
        def close(self):
            pass

    class Browser:
        def close(self):
            pass

    class Playwright:
        def stop(self):
            pass

    request = Request()
    ctx = type("Context", (), {"request": request})()
    monkeypatch.setattr(target_resolver, "_public_connect",
                        lambda: (Playwright(), Browser(), ctx, Page()))
    items, complete = target_resolver.resolve_oppo("AI产品经理", return_coverage=True)
    assert complete is True
    assert len(items) == 11
    assert request.page_numbers == [1, 2]
    assert [item.job_id for item in items if item.exact_title] == ["11"]
    request.bad_page = True
    assert target_resolver.resolve_oppo("AI产品经理", return_coverage=True)[1] is False
    request.bad_page = False
    request.bad_detail = True
    assert target_resolver.resolve_oppo("AI产品经理", return_coverage=True)[1] is False


def test_plain_hash_job_route_is_preserved_but_secret_fragment_is_rejected(tmp_path):
    profile = tmp_path / "synthetic-profile.json"
    profile.write_text("{}")
    url = "https://careers.example.test/#/jobs/role-123"
    spec = TaskSpec(company="Synthetic", role="Engineer", target_url=url,
                    job_id="role-123", profile_ref=str(profile))
    task = TaskQueue(tmp_path / "runtime").enqueue(spec)
    assert task["spec"]["target_url"] == url
    with pytest.raises(ValueError):
        TaskSpec(company="Synthetic", role="Engineer",
                 target_url="https://careers.example.test/#/jobs/role-123?access_token=secret",
                 profile_ref=str(profile))
    with pytest.raises(ValueError):
        TaskSpec(company="Synthetic", role="Engineer", target_url=url,
                 job_id="other-role", profile_ref=str(profile))


def test_isolated_consumer_ui_requires_choice_before_creating_task(tmp_path, monkeypatch):
    from executor.autonomy import manager as manager_module

    queue = TaskQueue(tmp_path / "runtime")
    profile = tmp_path / "synthetic-profile.json"
    profile.write_text("{}")
    worker = Worker(queue, settings={"deepseek": {"enabled": False}})
    manager = ManagerController(queue, worker, settings={"profile_path": str(profile)})
    observed = snapshot(candidate("101"), candidate("102", location="深圳"))
    monkeypatch.setattr(manager_module, "discover", lambda request, selected_candidate_id="":
                        evaluate_snapshot(request, observed, selected_candidate_id=selected_candidate_id))
    supervisor = Supervisor(queue, worker=worker, token="x" * 40, manager=manager)
    server = create_server(supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        ticket = supervisor.issue_ui_ticket()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(base + "/ui-login?ticket=" + ticket)
                page.locator('input[name="company"]').fill("OPPO")
                page.locator('input[name="role"]').fill("AI产品经理")
                page.locator('#newtask button').click()
                page.locator('#candidates button').first.wait_for()
                assert page.locator('#candidates button').count() == 2
                assert queue.tasks() == []
                page.locator('#candidates button').nth(1).click()
                page.locator('#tasks .task').first.wait_for()
                tasks = queue.tasks()
                assert len(tasks) == 1
                assert tasks[0]["spec"]["job_id"] == "102"
                assert tasks[0]["spec"]["live_authorized"] is False
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
