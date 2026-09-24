"""Structured form observations use synthetic pages and isolated headless browser."""

import hashlib
import json

import pytest

from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.forms import FillPlan, FormObservation, FormObservationError
from executor.models import (ApplicationStage, FieldResolution, ResolutionStatus,
                             WebField)
from executor.autonomy.worker import outcome
from executor.autonomy.queue import TaskQueue, TaskSpec


def _runner(tmp_path, monkeypatch, body, extra_fields=None):
    html = tmp_path / "form.html"
    html.write_text("<!doctype html><meta charset='utf-8'><body>" + body,
                    encoding="utf-8")
    profile = tmp_path / "profile.json"
    fields = {"identity.full_name": {
        "value": "Synthetic Person", "confidence": 1.0,
        "user_confirmed": True}}
    fields.update(extra_fields or {})
    profile.write_text(json.dumps({"fields": fields}), encoding="utf-8")
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    return ApplicationExecutor(html.as_uri(), profile,
                               {"deepseek": {"enabled": False}}), html


@pytest.mark.parametrize(("body", "signal"), [
    ("<label>姓名<input name='full_name'></label>"
     "<label>姓名<input name='full_name'></label>", "ambiguous_selector_count"),
    ("<label>姓名<input id='name'></label>"
     "<label style='display:none'>Required later<input required></label>",
     "hidden_required_count"),
    ("<label>姓名<input id='name'></label>"
     "<div role='combobox' tabindex='0'>请选择城市</div>",
     "unsupported_component_count"),
])
def test_unproven_form_structure_blocks_before_fill(tmp_path, monkeypatch, body, signal):
    runner, html = _runner(
        tmp_path, monkeypatch,
        body + "<button type='button'>Submit application</button>")
    with GenericWebAdapter(html.as_uri()) as adapter:
        observed = adapter.observe_form()
        assert observed.safe_summary()[signal] > 0
        assert observed.unsafe_structure
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    if signal == "hidden_required_count":
        assert plan.metadata["block_reason"] == "validation failed"
    else:
        assert plan.metadata["block_reason"] == "form structure unsupported or incomplete"
        assert plan.fields == []
        expected = ("component_driver_unavailable" if signal == "unsupported_component_count"
                    else "ambiguous_field_or_row_identity")
        assert expected in plan.metadata["capability_limitations"]


def test_no_observed_fields_cannot_become_ready(tmp_path, monkeypatch):
    runner, _html = _runner(
        tmp_path, monkeypatch,
        "<button type='button'>Submit application</button>")
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["form_observation"]["field_count"] == 0
    assert plan.metadata["capability_limitations"] == ["empty_final_form_unverified"]


@pytest.mark.parametrize("embedded", [
    '<iframe srcdoc="<input required aria-label=\'Hidden ATS field\'>"></iframe>',
    '<application-form id="shadow-host"></application-form>'
    '<script>document.getElementById("shadow-host").attachShadow({mode:"open"})'
    '.innerHTML="<input required aria-label=\\"Shadow ATS field\\">"</script>',
])
def test_embedded_form_controls_are_explicitly_unsupported(
        tmp_path, monkeypatch, embedded):
    runner, html = _runner(tmp_path, monkeypatch,
        "<label>姓名<input id='name' name='full_name' required></label>"
        + embedded + "<button type='button'>Submit application</button>")
    with GenericWebAdapter(html.as_uri()) as adapter:
        if "shadow-host" in embedded:
            assert adapter.page.locator("#shadow-host").evaluate(
                "e => Boolean(e.shadowRoot?.querySelector('input[required]'))")
        observed = adapter.observe_form()
        assert observed.unsupported_component_count > 0
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert "component_driver_unavailable" in plan.metadata["capability_limitations"]
    assert plan.fields == []


def test_form_observation_error_blocks_before_any_fill(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch,
        "<label>姓名<input id='name' name='full_name' required></label>"
        "<button type='button'>Submit application</button>")

    class BrokenObservation(GenericWebAdapter):
        def observe_form(self):
            raise FormObservationError("synthetic page evaluate failed")

        def apply_resolutions(self, _resolutions):
            pytest.fail("field write after failed observation")

    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: BrokenObservation(url))
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["form_observation"] == "ERROR"
    assert plan.fields == []
    assert outcome(plan) == ("BLOCKED", "form_observation_unavailable")


def test_form_observation_error_after_write_never_reaches_ready(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch,
        "<label>姓名<input id='name' name='full_name' required></label>"
        "<button type='button'>Submit application</button>")

    class FailsAfterWrite(GenericWebAdapter):
        observations = 0

        def observe_form(self):
            self.observations += 1
            if self.observations > 1:
                raise FormObservationError("synthetic redraw evaluate failed")
            return super().observe_form()

    monkeypatch.setattr("executor.application.adapter_for_url",
                        lambda url: FailsAfterWrite(url))
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["form_observation"] == "ERROR"
    assert outcome(plan) == ("BLOCKED", "form_observation_unavailable")


@pytest.mark.parametrize("blocker", [
    "form_observation_unavailable",
    "draft_persistence_unverified",
    "attachment_persistence_unverified",
    "auth_return_unverified",
    "account_identity_unverified",
    "row_reconciliation_unverified",
])
def test_unverified_effect_cannot_be_blindly_resumed_after_pause(tmp_path, blocker):
    queue = TaskQueue(tmp_path / "runtime")
    task = queue.enqueue(TaskSpec(
        company="Synthetic", role="Engineer",
        target_url="https://jobs.example.test/apply?postId=observe-1",
        profile_ref=str(tmp_path / "synthetic-profile.json")))
    claimed = queue.claim("synthetic-worker")
    queue.checkpoint(task["task_id"], claimed["owner"], "BLOCKED",
                     blocker=blocker, release=True)
    assert queue.get(task["task_id"])["blocker"] == blocker
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.resume(task["task_id"])
    queue.pause(task["task_id"])
    assert queue.get(task["task_id"])["blocker"] == "user_paused_from_" + blocker
    with pytest.raises(ValueError, match="read-only reconciliation"):
        queue.resume(task["task_id"])


def test_duplicate_locator_never_writes_first_repeated_row(tmp_path):
    html = tmp_path / "rows.html"
    html.write_text("<!doctype html><body><label>学校<input name='school'></label>"
                    "<label>学校<input name='school'></label>", encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        fields = adapter.discover_fields()
        assert fields[0].selector == fields[1].selector
        actions = adapter.apply_resolutions([FieldResolution(
            field_id=fields[1].field_id, selector=fields[1].selector,
            label=fields[1].label, status=ResolutionStatus.RESOLVED,
            value="Synthetic University")])
        assert actions[0]["reason"] == "ambiguous_or_missing_locator"
        assert adapter.page.locator("input[name=school]").evaluate_all(
            "elements => elements.map(e => e.value)") == ["", ""]


def test_repeated_rows_are_observed_without_exposing_site_record_tokens(tmp_path):
    html = tmp_path / "rows.html"
    html.write_text("""<!doctype html><body>
      <div data-record-id='PRIVATE_ROW_ALPHA'><input name='school'></div>
      <div data-record-id='PRIVATE_ROW_BETA'><input name='school'></div>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        observed = adapter.observe_form()
        assert len(observed.rows) == 2
        assert all(row.identity_proven for row in observed.rows)
        assert observed.ambiguous_selector_count == 1
        assert "PRIVATE_ROW" not in json.dumps(observed.safe_summary())
        assert "PRIVATE_ROW" not in repr(observed)
    html.write_text("""<!doctype html><body>
      <div data-record-id='REUSED'><input name='school'></div>
      <div data-record-id='REUSED'><input name='school'></div>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        observed = adapter.observe_form()
        assert observed.ambiguous_row_count == 1
        assert observed.unsafe_structure


def test_unique_dom_row_ids_do_not_prove_canonical_record_binding(tmp_path, monkeypatch):
    runner, html = _runner(tmp_path, monkeypatch, """
      <div data-record-id='SITE_ROW_A'><label>学校<input id='school-a' required></label></div>
      <div data-record-id='SITE_ROW_B'><label>学校<input id='school-b' required></label></div>
      <button type='button'>Submit application</button>
    """)
    with GenericWebAdapter(html.as_uri()) as adapter:
        observed = adapter.observe_form()
        assert len(observed.rows) == 2
        assert observed.ambiguous_row_count == 0
        assert observed.ambiguous_selector_count == 0
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "form structure unsupported or incomplete"
    assert plan.fields == []


def test_duplicate_exact_select_labels_block_before_choice(tmp_path):
    html = tmp_path / "duplicate-options.html"
    html.write_text("""<!doctype html><body>
      <label>城市<select id='city'>
        <option value=''>Choose</option>
        <option value='city-one'>Synthetic City</option>
        <option value='city-two'>Synthetic City</option>
      </select></label>
    """)
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([FieldResolution(
            field_id="city", selector="#city", label="City",
            status=ResolutionStatus.RESOLVED, value="Synthetic City")])
        assert actions == [{"field_id": "city", "ok": False,
                            "reason": "no_matching_select_option"}]
        assert adapter.page.locator("#city").input_value() == ""


def test_generic_multiple_select_requires_a_component_driver(tmp_path):
    html = tmp_path / "multi-select.html"
    html.write_text("""<!doctype html><body><label>城市
      <select id='cities' multiple>
        <option value='a'>City A</option><option value='b'>City B</option>
      </select></label>""")
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([FieldResolution(
            field_id="cities", selector="#cities", label="Cities",
            status=ResolutionStatus.RESOLVED, value=["City A", "City B"])])
        assert actions == [{"field_id": "cities", "ok": False,
                            "reason": "no_matching_select_option"}]
        assert adapter.page.locator("#cities").evaluate(
            "e => [...e.selectedOptions].length") == 0


def test_explicit_dependent_select_is_filled_after_parent_redraw(tmp_path):
    html = tmp_path / "dependent.html"
    html.write_text("""<!doctype html><body>
      <label>本科专业<select id='major' data-depends-on='#school' required>
        <option value=''>Choose major</option></select></label>
      <label>本科院校<select id='school' required>
        <option value=''>Choose school</option>
        <option value='school-a'>Synthetic University</option></select></label>
      <script>
      document.querySelector('#school').addEventListener('change', () => {
        document.querySelector('#major').innerHTML =
          '<option value="">Choose major</option>' +
          '<option value="cs">Computer Science</option>';
      });
      </script>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        observation = adapter.observe_form()
        assert observation.dependencies == (("#school", "#major"),)
        before = observation.structure_digest
        actions = adapter.apply_resolutions([
            FieldResolution(field_id="major", selector="#major", label="本科专业",
                status=ResolutionStatus.RESOLVED, value="Computer Science"),
            FieldResolution(field_id="school", selector="#school", label="本科院校",
                status=ResolutionStatus.RESOLVED, value="Synthetic University"),
        ])
        assert [action["field_id"] for action in actions] == ["school", "major"]
        assert all(action["ok"] for action in actions)
        assert adapter.page.locator("#major").input_value() == "cs"
        assert adapter.observe_form().structure_digest != before


def test_cyclic_field_dependency_blocks_without_writing(tmp_path):
    html = tmp_path / "cycle.html"
    html.write_text("""<!doctype html><body>
      <input id='a' data-depends-on='#b'>
      <input id='b' data-depends-on='#a'>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([
            FieldResolution(field_id="a", selector="#a", label="A",
                status=ResolutionStatus.RESOLVED, value="one"),
            FieldResolution(field_id="b", selector="#b", label="B",
                status=ResolutionStatus.RESOLVED, value="two"),
        ])
        assert all(not action["ok"] and action["reason"] == "cyclic_field_dependency"
                   for action in actions)
        assert adapter.page.locator("#a").input_value() == ""
        assert adapter.page.locator("#b").input_value() == ""


def test_unresolved_parent_blocks_dependent_write(tmp_path):
    html = tmp_path / "unresolved-parent.html"
    html.write_text("""<!doctype html><body>
      <input id='school'>
      <input id='major' data-depends-on='#school'>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([
            FieldResolution(field_id="major", selector="#major", label="Major",
                status=ResolutionStatus.RESOLVED, value="Computer Science"),
            FieldResolution(field_id="school", selector="#school", label="School",
                status=ResolutionStatus.UNRESOLVED),
        ])
        assert actions == [{"field_id": "major", "ok": False,
                            "reason": "unresolved_field_dependency"}]
        assert adapter.page.locator("#major").input_value() == ""


def test_failed_parent_choice_never_writes_dependent_field(tmp_path):
    html = tmp_path / "failed-parent.html"
    html.write_text("""<!doctype html><body>
      <select id='school'><option value=''>Choose school</option></select>
      <input id='major' data-depends-on='#school'>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([
            FieldResolution(field_id="major", selector="#major", label="Major",
                status=ResolutionStatus.RESOLVED, value="Computer Science"),
            FieldResolution(field_id="school", selector="#school", label="School",
                status=ResolutionStatus.RESOLVED, value="Synthetic University"),
        ])
        assert actions == [
            {"field_id": "school", "ok": False, "reason": "no_matching_select_option"},
            {"field_id": "major", "ok": False, "reason": "parent_field_write_failed"},
        ]
        assert adapter.page.locator("#major").input_value() == ""


def test_dynamic_required_field_is_reobserved_after_fill(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch, """
      <label>姓名<input id='name' name='full_name' required></label>
      <div id='extra'></div>
      <script>
        document.querySelector('#name').addEventListener('input', () => {
          document.querySelector('#extra').innerHTML =
            '<label>新必填项<input id="new-required" required></label>';
        });
      </script>
      <button type='button'>Submit application</button>
    """)
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "unresolved fields require user input"
    assert plan.metadata["form_observation"]["field_count"] == 2


def test_dependent_city_is_filled_from_fresh_observation(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch, """
      <label>姓名<input id='name' name='full_name' required></label>
      <div id='extra'></div>
      <script>
        document.querySelector('#name').addEventListener('input', () => {
          document.querySelector('#extra').innerHTML =
            '<label>现居城市<input id="city" name="current_city" required></label>';
        });
      </script>
      <button type='button'>Submit application</button>
    """, {"identity.current_city": {"value": "Synthetic City",
                                  "confidence": 1.0, "user_confirmed": True}})
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "draft persistence unverified"
    assert {item.canonical_key for item in plan.fields} >= {
        "identity.full_name", "identity.current_city"}


def test_dom_value_alone_cannot_prove_persisted_draft(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch, """
      <label>姓名<input id='name' name='full_name' required></label>
      <button type='button'>Submit application</button>
    """)
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["validation"]["ok"] is True
    assert plan.metadata["block_reason"] == "draft persistence unverified"


def test_generic_next_cannot_leave_unverified_draft(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch, """
      <label>姓名<input id='name' name='full_name' required></label>
      <button type='button' onclick="location.href='missing-next.html'">Next</button>
    """)
    plan = runner.run(max_pages=2)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "draft persistence unverified"
    assert plan.metadata["draft_persistence_phase"] == "before_navigation"
    assert plan.metadata["page_index"] == 0
    assert not plan.metadata.get("page_draft_receipts")


def test_generic_upload_needs_independent_completion_receipt(tmp_path):
    html = tmp_path / "upload.html"
    html.write_text("<!doctype html><body><input id='resume' type='file'>",
                    encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([FieldResolution(
            field_id="resume", selector="#resume", label="Resume",
            status=ResolutionStatus.RESOLVED, value=str(tmp_path / "resume.pdf"))])
        assert actions == [{"field_id": "resume", "ok": False,
                            "reason": "upload_receipt_unsupported"}]
        assert adapter.page.locator("#resume").input_value() == ""


def test_month_precision_never_invents_day_for_date_control(tmp_path):
    html = tmp_path / "date.html"
    html.write_text("<!doctype html><body><input id='graduation' type='date'>",
                    encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        actions = adapter.apply_resolutions([FieldResolution(
            field_id="graduation", selector="#graduation", label="Graduation date",
            status=ResolutionStatus.RESOLVED, value="2026-09")])
        assert actions == [{"field_id": "graduation", "ok": False,
                            "reason": "date_precision_unsupported"}]
        assert adapter.page.locator("#graduation").input_value() == ""


def test_scoped_aria_combobox_requires_exact_choice_and_readback(tmp_path):
    html = tmp_path / "combo.html"
    html.write_text("""<!doctype html><body>
      <label>现居城市</label>
      <div id='city' role='combobox' aria-controls='cities' aria-required='true'
           tabindex='0' onclick="document.querySelector('#cities').hidden=false">请选择</div>
      <div id='cities' role='listbox' hidden>
        <div role='option' onclick="document.querySelector('#city').innerText=this.innerText;this.parentElement.hidden=true">Synthetic City</div>
        <div role='option' onclick="document.querySelector('#city').innerText=this.innerText;this.parentElement.hidden=true">Synthetic City East</div>
      </div>
    """, encoding="utf-8")
    with GenericWebAdapter(html.as_uri()) as adapter:
        observation = adapter.observe_form()
        assert observation.unsupported_component_count == 0
        assert len(observation.fields) == 1
        failed = adapter.apply_resolutions([FieldResolution(
            field_id="city", selector="#city", label="City",
            status=ResolutionStatus.RESOLVED, value="Synthetic")])
        assert failed[0]["reason"] == "combobox_exact_choice_unavailable"
        assert adapter.page.locator("#city").inner_text() == "请选择"
        chosen = adapter.apply_resolutions([FieldResolution(
            field_id="city", selector="#city", label="City",
            status=ResolutionStatus.RESOLVED, value="Synthetic City")])
        assert chosen[0]["ok"] is True
        assert adapter.observe_form().fields[0].current_value == "Synthetic City"


def test_site_error_prevents_false_ready_after_fill(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch, """
      <label>姓名<input id='name' name='full_name' required></label>
      <div role='alert'>Synthetic server rejected value</div>
      <button type='button'>Submit application</button>
    """)
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "validation failed"
    assert "site validation errors remain visible" in plan.metadata["validation"]["errors"]


def test_attachment_slots_require_distinct_type_and_canonical_hash(tmp_path, monkeypatch):
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    resume = tmp_path / "resume.pdf"
    photo = tmp_path / "portrait.png"
    resume.write_bytes(b"%PDF-1.4 synthetic resume")
    photo.write_bytes(b"synthetic image")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"assets": {
        "resume": {"path": str(resume), "kind": "resume_pdf",
                   "sha256": hashlib.sha256(resume.read_bytes()).hexdigest()},
        "photo": {"path": str(photo), "kind": "photo_png",
                  "sha256": hashlib.sha256(photo.read_bytes()).hexdigest()},
    }}), encoding="utf-8")
    runner = ApplicationExecutor("file:///synthetic/form.html", profile,
                                 {"deepseek": {"enabled": False}})
    resume_field = WebField(field_id="resume", selector="#resume", label="简历",
                            input_type="file", required=True,
                            metadata={"accept": ".pdf"})
    photo_field = WebField(field_id="photo", selector="#photo", label="证件照",
                           input_type="file", required=True,
                           metadata={"accept": "image/png"})
    assert runner._attachment_resolution(resume_field).value == str(resume)
    assert runner._attachment_resolution(photo_field).value == str(photo)
    ambiguous = photo_field.model_copy(update={"label": "简历与证件照"})
    assert runner._attachment_resolution(ambiguous).status == ResolutionStatus.UNRESOLVED
    wrong_type = photo_field.model_copy(update={"metadata": {"accept": ".pdf"}})
    assert runner._attachment_resolution(wrong_type).status == ResolutionStatus.UNRESOLVED
    too_large = photo_field.model_copy(update={"metadata": {"accept": "image/png",
                                                           "max_size": "1"}})
    assert runner._attachment_resolution(too_large).status == ResolutionStatus.UNRESOLVED
    photo.write_bytes(b"changed image after canonical capture")
    assert runner._attachment_resolution(photo_field).status == ResolutionStatus.UNRESOLVED


def test_existing_checked_declaration_is_not_new_scoped_consent(tmp_path, monkeypatch):
    runner, _html = _runner(tmp_path, monkeypatch, """
      <label>姓名<input id='name' name='full_name' required></label>
      <label>本人承诺以上信息真实有效<input id='declaration' type='checkbox' checked required></label>
      <button type='button'>Submit application</button>
    """, {"policy.auto_accept_truth_submission_declarations": {
        "value": True, "confidence": 1.0, "user_confirmed": True}})
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "unresolved fields require user input"
    declaration = next(item for item in plan.fields if item.field_id == "declaration")
    assert declaration.status == ResolutionStatus.USER_CONFIRMATION


def test_scoped_salary_representation_preserves_canonical_amount(tmp_path, monkeypatch):
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    target = "https://synthetic.example.test/apply/role-1"
    profile = tmp_path / "salary-profile.json"
    salary_profile = {"fields": {"preferences.expected_salary": {
        "value": "200000", "user_confirmed": True,
        "normalization": {"salary": {
            "target_sha256": hashlib.sha256(target.encode()).hexdigest(),
            "currency": "CNY", "period": "annual", "tax_basis": "gross", "unit": "yuan",
        }}}}}
    profile.write_text(json.dumps(salary_profile), encoding="utf-8")
    runner = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}})
    runner.plan.fields.append(FieldResolution(
        field_id="salary", selector="#salary", label="期望年薪",
        canonical_key="preferences.expected_salary", status=ResolutionStatus.RESOLVED,
        value="20", source="user_confirmed_scoped_salary"))
    assert runner._profile_consistency()["ok"] is True


def test_scoped_salary_on_later_page_uses_same_scope_for_resolution_and_audit(tmp_path, monkeypatch):
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "audit")
    target = "https://synthetic.example.test/apply/role-1"
    salary_page = target + "/salary"
    profile = tmp_path / "salary-page-profile.json"
    profile.write_text(json.dumps({"fields": {"preferences.expected_salary": {
        "value": "200000", "user_confirmed": True,
        "normalization": {"salary": {
            "target_sha256": hashlib.sha256(salary_page.encode()).hexdigest(),
            "currency": "CNY", "period": "annual", "tax_basis": "gross", "unit": "yuan",
        }}}}}))
    runner = ApplicationExecutor(target, profile, {"deepseek": {"enabled": False}})
    field = WebField(field_id="salary", selector="#salary", label="期望年薪",
                     required=True, page_url=salary_page,
                     metadata={"salary": {"currency": "CNY", "period": "annual",
                                          "tax_basis": "gross", "unit": "ten_thousand_yuan"}})
    resolved = runner.resolver.resolve(field)
    assert resolved.value == "20"
    assert resolved.scope_sha256 == hashlib.sha256(salary_page.encode()).hexdigest()
    runner.plan.fields.append(resolved)
    assert runner._profile_consistency()["ok"] is True


def test_form_observation_and_fill_plan_do_not_expose_values_in_receipts():
    field = WebField(field_id="name", selector="#name", label="Private Label",
                     current_value="CANARY_PRIVATE_PERSON", required=True)
    observation = FormObservation.from_fields("https://example.test/form", "1234.5", [field])
    plan = FillPlan.bind(observation, [FieldResolution(
        field_id="name", selector="#name", label="Private Label",
        status=ResolutionStatus.RESOLVED, value="CANARY_PRIVATE_PERSON")])
    assert plan.observation_digest == observation.structure_digest
    assert "CANARY_PRIVATE_PERSON" not in repr(observation)
    assert "CANARY_PRIVATE_PERSON" not in repr(plan)
    assert "CANARY_PRIVATE_PERSON" not in json.dumps(observation.safe_summary())
    with pytest.raises(ValueError, match="unobserved"):
        FillPlan.bind(observation, [FieldResolution(
            field_id="other", selector="#other", label="Other",
            status=ResolutionStatus.RESOLVED, value="x")])
