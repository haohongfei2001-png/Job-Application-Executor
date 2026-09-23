"""Structured form observations use synthetic pages and isolated headless browser."""

import json

import pytest

from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.forms import FillPlan, FormObservation
from executor.models import (ApplicationStage, FieldResolution, ResolutionStatus,
                             WebField)


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


def test_no_observed_fields_cannot_become_ready(tmp_path, monkeypatch):
    runner, _html = _runner(
        tmp_path, monkeypatch,
        "<button type='button'>Submit application</button>")
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["form_observation"]["field_count"] == 0


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
