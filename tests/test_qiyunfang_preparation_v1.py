"""Synthetic exact-contract and mapping tests; no external applicant data."""
import copy
import json

import pytest

from executor.preparation import qiyunfang as q


def profile(**fields):
    return {"fields": {key: {"value": value} for key, value in fields.items()}}


def observation():
    rows = []
    for field in q.FIELDS:
        controls = []
        count = len(field.options) if field.options else 2 if field.control == "upload" else 0 if field.control == "submit" else 1
        for index in range(count):
            kind = ("button" if index == 0 else "file") if field.control == "upload" else "checkbox" if field.control == "protocol" else field.control
            controls.append({"tag": "input", "type": kind,
                "id": f"M1567R{field.field_id}I{index}" if field.options else "",
                "name": f"M1567R{field.field_id}" if field.options else "fileselect[]" if kind == "file" else "",
                "required": False, "disabled": False, "readonly": False,
                "maxlength": str(field.maxlength) if field.maxlength else None,
                "placeholder": field.placeholder, "value": None if field.protected else field.options[index] if field.options else "",
                "option_value": field.options[index] if field.options else None,
                "occupied": False, "checked": None if field.protected else False,
                "label": field.options[index] if field.options else "",
                "visible": kind != "file", "formaction": None, "has_form_owner": False})
        rows.append({"field_id": field.field_id, "data_type": field.data_type, "label": field.label,
            "required_marker": field.required_marker, "visible": True, "controls": controls,
            "submit_text": "提交" if field.control == "submit" else "",
            "protocol_text": "我已经阅读并同意" if field.control == "protocol" else "",
            "protocol_title": "《隐私保护协议》" if field.control == "protocol" else ""})
    return {"connected": True, "visible": True, "fields": rows, "native_forms": 0, "ancestor_form": False, "control_count": 90}


def test_complete_observed_contract_accepts_empty_popup_without_claiming_requiredness():
    assert q.validate_observation(observation()) is True
    assert len(q.ROLE_OPTIONS) == 31
    assert q.ROLE_OPTIONS[19] == q.CONTRACT_ROLE
    assert q.ROLE_OPTIONS[5].endswith(" （武汉/深圳）")
    assert q.ROLE_OPTIONS[17].endswith("/深圳)")
    assert sum(len(row["controls"]) for row in observation()["fields"]) == 90


@pytest.mark.parametrize("key,value", [
    ("identity.full_name", "Synthetic Applicant"), ("identity.phone", "13000000000"),
    ("identity.email", "synthetic@example.test"), ("identity.gender", "男"),
    ("education.highest.degree", "硕士"), ("education.highest.school", "Synthetic University"),
    ("education.highest.college", "Synthetic College"), ("education.highest.major", "Synthetic Major"),
    ("education.highest.graduation_date", "2027年"), ("language.cet6.level", "CET-6"),
    ("preferences.preferred_cities", ["武汉"]),
])
def test_exact_routine_values_are_proposals_requiring_explicit_selection(key, value):
    items = q.map_routine_fields(profile(**{key: value}))
    item = next(item for item in items if item["key"] == key)
    assert item["status"] == "proposed" and item["value"] == value
    assert all(item["requires_explicit_selection"] for item in items)
    assert q.select_plan(items, [item["field_id"]]) == [{"field_id": item["field_id"], "value": value}]


@pytest.mark.parametrize("key,value", [
    ("identity.gender", "male"), ("education.highest.degree", "学士"),
    ("education.highest.graduation_date", "2027-06-30"), ("education.highest.graduation_date", 2027),
    ("language.cet6.level", "CET6"), ("language.cet6.level", "IELTS"),
    ("preferences.preferred_cities", ["武汉", "北京"]),
    ("preferences.preferred_cities", ["武汉", "武汉"]),
    ("preferences.preferred_cities", [None]), ("preferences.preferred_cities", [{"city": "武汉"}]),
    ("identity.full_name", " Synthetic"), ("identity.full_name", "Synthetic\nValue"),
    ("identity.full_name", "x" * 101), ("identity.email", "x" * 51),
    ("identity.full_name", {"private": "SECRET"}), ("identity.full_name", True),
])
def test_ambiguous_or_unsupported_facts_never_become_write_plan(key, value):
    items = q.map_routine_fields(profile(**{key: value}))
    item = next(item for item in items if item["key"] == key)
    assert item["status"] == "manual" and item["value"] is None
    with pytest.raises(ValueError, match="invalid preparation selection"):
        q.select_plan(items, [item["field_id"]])


@pytest.mark.parametrize("score,expected", [(0, "0"), (710, "710"), (500, "500"), ("500", "500"),
    (True, None), (500.0, None), (711, None), (-1, None), ("005", None), ("５００", None), ("NaN", None)])
def test_score_requires_exact_cet6_record_and_bounded_integer(score, expected):
    def mapped(certificate):
        return next(item for item in q.map_routine_fields(profile(**{
            "language.cet6.level": certificate, "language.cet6.score": score})) if item["field_id"] == "16")
    assert mapped("CET-6")["value"] == expected
    assert mapped("IELTS")["value"] is None


def test_protected_facts_and_unknown_metadata_never_enter_plan():
    source = profile(**{"identity.full_name": "Synthetic", "identity.id_number": "ID_SECRET_CANARY",
        "auth.password": "PASSWORD_CANARY", "policy.auto_accept_privacy_terms": True})
    source["assets"] = {"resume": {"path": "/private/path/RESUME_CANARY", "sha256": "a" * 64}}
    encoded = json.dumps(q.map_routine_fields(source))
    assert all(value not in encoded for value in ("ID_SECRET_CANARY", "PASSWORD_CANARY", "RESUME_CANARY"))
    for field in q.FIELDS:
        if field.protected:
            with pytest.raises(ValueError):
                q.select_plan(q.map_routine_fields(source), [field.field_id])


@pytest.mark.parametrize("selection", [[], ["0", "0"], [0], "0", ["unknown"], ["12"], ["Submit"]])
def test_selection_is_nonempty_explicit_unique_routine_only(selection):
    with pytest.raises(ValueError):
        q.select_plan(q.map_routine_fields(profile(**{"identity.full_name": "Synthetic"})), selection)


@pytest.mark.parametrize("field_index", range(len(q.FIELDS)))
@pytest.mark.parametrize("change", ["label", "field_id", "data_type", "visible", "required_marker", "controls"])
def test_whole_contract_drift_blocks_all_writes(field_index, change):
    observed = observation()
    row = observed["fields"][field_index]
    row[change] = not row[change] if isinstance(row[change], bool) else [] if change == "controls" else "changed"
    # The submit row has no input controls; substituting [] is not a change.
    if change == "controls" and not q.FIELDS[field_index].control == "submit":
        with pytest.raises(q.ContractChanged): q.validate_observation(observed)
    elif change != "controls":
        with pytest.raises(q.ContractChanged): q.validate_observation(observed)


@pytest.mark.parametrize("attr,value", [("disabled", True), ("readonly", True), ("required", True),
    ("visible", False), ("type", "password"), ("id", "new"), ("name", "new"),
    ("maxlength", "101"), ("placeholder", "changed"), ("value", "someone else's value"), ("formaction", "https://evil.test")])
def test_text_control_drift_is_refused(attr, value):
    observed = observation(); observed["fields"][0]["controls"][0][attr] = value
    with pytest.raises(q.ContractChanged): q.validate_observation(observed)


def test_duplicate_role_groups_are_not_interchangeable_and_secondary_stays_empty():
    observed = observation()
    primary, secondary = observed["fields"][12:14]
    primary["controls"][19]["checked"] = True
    assert q.validate_observation(observed, expected_values={"8": q.CONTRACT_ROLE})
    secondary["controls"][19]["occupied"] = True
    with pytest.raises(q.ContractChanged): q.validate_observation(observed, expected_values={"8": q.CONTRACT_ROLE})


def test_entire_final_readback_detects_redrawn_earlier_value():
    observed = observation(); observed["fields"][0]["controls"][0]["value"] = "Synthetic"
    assert q.validate_observation(observed, expected_values={"0": "Synthetic"})
    observed["fields"][0]["controls"][0]["value"] = ""
    with pytest.raises(q.ContractChanged): q.validate_observation(observed, expected_values={"0": "Synthetic"})


@pytest.mark.parametrize("change", ["ancestor", "owner", "protected_option", "extra_control", "extra_row"])
def test_additional_boundary_drift_is_refused(change):
    value = observation()
    if change == "ancestor": value["ancestor_form"] = True
    elif change == "owner": value["fields"][0]["controls"][0]["has_form_owner"] = True
    elif change == "protected_option": value["fields"][13]["controls"][19]["option_value"] = "different"
    elif change == "extra_control": value["control_count"] = 91
    else: value["fields"].append(copy.deepcopy(value["fields"][0]))
    with pytest.raises(q.ContractChanged): q.validate_observation(value)


@pytest.mark.parametrize("url,method", [
    ("https://www.qiyunfang.com/ajax/siteForm_h.jsp?cmd=addWafCk_addSubmit", "GET"),
    ("https://www.qiyunfang.com/ajax/siteForm_h.jsp?%63md=addWafCk_addSubmit", "GET"),
    ("https://www.qiyunfang.com/h-col-124.html?%63md=addWafCk_addSubmit", "GET"),
    ("https://www.qiyunfang.com/unknown", "GET"), (q.CONTRACT_URL, "POST"),
    ("https://www.qiyunfang.com:443/h-col-124.html", "GET"),
    ("http://www.qiyunfang.com/h-col-124.html", "GET"),
    ("https://www.qiyunfang.com/h-col-124.html#fragment", "GET"),
    ("https://www.qiyunfang.com/api/guest/form/memberModifySubmit", "POST"),
])
def test_read_only_request_gate_is_exact_finite_manifest_not_get_allowlist(url, method):
    from types import SimpleNamespace
    from executor.preparation.transport import PreparationTransport
    assert not PreparationTransport._public_get(SimpleNamespace(url=url, method=method, post_data=None))


def test_request_gate_never_forwards_redirect_or_reopens_after_seal():
    from types import SimpleNamespace
    from executor.preparation.transport import PreparationTransport
    gate = PreparationTransport()
    events = []
    request = SimpleNamespace(url=q.CONTRACT_URL, method="GET", post_data=None,
                              is_navigation_request=lambda: False)
    response = SimpleNamespace(status=302)
    route = SimpleNamespace(request=request, fetch=lambda **kwargs: (events.append(kwargs) or response),
                            abort=lambda reason: events.append(("abort", reason)),
                            fulfill=lambda **kwargs: pytest.fail("redirect forwarded"))
    gate._route(route)
    assert events == [{"max_redirects": 0, "max_retries": 0, "timeout": 15000}, ("abort", "blockedbyclient")]
    gate.installed = True
    gate.seal()
    assert gate.blocked == 1
    events.clear(); gate._route(route)
    assert events == [("abort", "blockedbyclient")]
    with pytest.raises(RuntimeError): gate.seal()
    with pytest.raises(RuntimeError): gate.require_sealed()


def test_driver_constants_match_independently_captured_public_controls():
    from pathlib import Path
    capture = json.loads((Path(__file__).parent / 'fixtures/qiyunfang/public_form_observation_20261001.json').read_text())
    assert capture['source_url'] == q.CONTRACT_URL and capture['root_selector'] == q.ROOT
    assert capture['root_count'] == 1 and capture['roots'][0]['control_count'] == 90
    observed = capture['roots'][0]['fields']
    assert [item['field_id'] for item in observed] == [field.field_id for field in q.FIELDS]
    for field, row in zip(q.FIELDS, observed):
        assert row['data_type'] == field.data_type
        assert row['title_spans'] == ([field.label] if field.label else [])
        assert row['required_marker'] == field.required_marker
        if field.options:
            assert tuple(control['public_option_value'] for control in row['controls']) == field.options
            assert [control['associated_labels'] for control in row['controls']] == [[option] for option in field.options]
        if field.control == 'text':
            assert row['controls'][0]['maxlength'] == str(field.maxlength)
            assert (row['controls'][0]['placeholder'] or '') == field.placeholder
    file_control = next(item for item in observed if item['field_id'] == '11')['controls'][1]
    assert file_control['file_occupancy'] == 'unverified_files_property_unavailable'
