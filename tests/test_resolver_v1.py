from executor.models import ResolutionStatus, WebField
from executor.resolver import FieldResolver
from executor.adapters.registry import adapter_for_url, site_id_for_url
from executor.adapters.generic_web import GenericWebAdapter
from executor.protected_targets import protected_target
import pytest


def _profile():
    return {
        "fields": {
            "identity.full_name": {"value": "Example User", "confidence": 1.0, "user_confirmed": True},
            "education.highest.school": {"value": "Graduate University", "confidence": 1.0},
            "education.bachelor.school": {"value": "Undergraduate University", "confidence": 1.0},
            "preferences.accept_location_adjustment": {"value": True, "confidence": 1.0},
        }
    }


def _resolver():
    return FieldResolver(_profile(), {"deepseek": {"enabled": False}})


def test_explicit_bachelor_does_not_use_highest_education():
    item = _resolver().resolve(WebField(field_id="school", selector="#school", label="本科院校", required=True))
    assert item.status == ResolutionStatus.RESOLVED
    assert item.canonical_key == "education.bachelor.school"
    assert item.value == "Undergraduate University"


def test_generic_school_is_not_guessed_between_two_degrees():
    item = _resolver().resolve(WebField(field_id="school", selector="#school", label="School", required=True))
    assert item.status == ResolutionStatus.UNRESOLVED
    assert "ambiguous" in item.reason


def test_subjective_salary_requires_user_confirmation():
    item = _resolver().resolve(WebField(field_id="salary", selector="#salary", label="Expected salary", required=True))
    assert item.status == ResolutionStatus.USER_CONFIRMATION


def test_site_existing_value_is_preserved_for_unknown_field():
    item = _resolver().resolve(WebField(
        field_id="custom", selector="#custom", label="Platform custom field",
        required=True, current_value="already saved",
    ))
    assert item.status == ResolutionStatus.KEEP_EXISTING
    assert item.value == "already saved"


def test_boss_platform_is_not_equated_with_schneider():
    url = "https://xiaoyuan.zhipin.com/volunteer/index?encryptJobId=fixture"
    assert site_id_for_url(url) == "boss_campus"
    assert isinstance(adapter_for_url(url), GenericWebAdapter)


def test_submitted_target_protection_is_target_specific():
    submitted = "https://xiaoyuan.zhipin.com/volunteer/index?encryptJobId=submitted-fixture"
    other = "https://xiaoyuan.zhipin.com/volunteer/index?encryptJobId=other-fixture"
    rules = [{
        "label": "submitted fixture",
        "status": "SUBMITTED",
        "hosts": ["xiaoyuan.zhipin.com"],
        "query_any": {"encryptJobId": ["submitted-fixture"]},
    }]
    assert protected_target(submitted, rules)
    assert protected_target(other, rules) is None


def test_missing_known_canonical_field_is_explicitly_unresolved():
    item = _resolver().resolve(WebField(
        field_id="origin", selector="#origin", label="生源地", required=True,
    ))
    assert item.status == ResolutionStatus.UNRESOLVED
    assert item.canonical_key == "identity.student_origin"
    assert "profile value missing" in item.reason


def test_ai_semantic_mapping_precedes_stale_site_value(monkeypatch):
    resolver = _resolver()
    monkeypatch.setattr(
        resolver.ai,
        "map_field",
        lambda field, keys: ("identity.full_name", 0.95, "synthetic semantic match"),
    )
    item = resolver.resolve(WebField(
        field_id="mystery", selector="#mystery", label="Applicant legal display name",
        required=True, current_value="Stale Site Value",
    ))
    assert item.status == ResolutionStatus.RESOLVED
    assert item.value == "Example User"
    assert item.source == "ai_mapping_only"
