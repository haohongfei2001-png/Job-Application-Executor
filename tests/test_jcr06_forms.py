"""Structured form observations use synthetic pages and isolated headless browser."""

import json

import pytest

from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.forms import FillPlan, FormObservation
from executor.models import (ApplicationStage, FieldResolution, ResolutionStatus,
                             WebField)


def _runner(tmp_path, monkeypatch, body):
    html = tmp_path / "form.html"
    html.write_text("<!doctype html><meta charset='utf-8'><body>" + body,
                    encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.full_name": {
        "value": "Synthetic Person", "confidence": 1.0,
        "user_confirmed": True}}}), encoding="utf-8")
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
