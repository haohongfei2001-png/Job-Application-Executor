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
            "identity.household_type": {"value": "农业户口", "confidence": 1.0, "user_confirmed": True},
            "identity.health_status": {"value": "健康", "confidence": 1.0, "user_confirmed": True},
            "identity.personnel_file_place": {"value": "Example University", "confidence": 1.0, "user_confirmed": True},
            "identity.foreign_residency_status": {"value": False, "confidence": 1.0, "user_confirmed": True},
            "compliance.coamc_employee_recusal_requirements_met": {"value": True, "confidence": 1.0, "user_confirmed": True},
            "family.primary.name": {"value": "Example Mother", "confidence": 1.0, "user_confirmed": True},
            "family.primary.relationship": {"value": "母亲", "confidence": 1.0, "user_confirmed": True},
            "family.primary.work_unit": {"value": "Example Village", "confidence": 1.0, "user_confirmed": True},
            "family.primary.department_title": {"value": "务农", "confidence": 1.0, "user_confirmed": True},
            "family.primary.work_location": {"value": "Example County", "confidence": 1.0, "user_confirmed": True},
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

def test_stable_confirmed_facts_do_not_require_reconfirmation():
    resolver = _resolver()
    cases = [
        ("户口类别", "identity.household_type", "农业户口"),
        ("健康状况", "identity.health_status", "健康"),
        ("人事档案所在单位", "identity.personnel_file_place", "Example University"),
        (
            "是否具有外国国籍，或者国（境）外永久居留权、长期居留许可等境外身份",
            "identity.foreign_residency_status",
            False,
        ),
        (
            "是否符合中国东方员工工作回避有关要求",
            "compliance.coamc_employee_recusal_requirements_met",
            True,
        ),
    ]
    for label, key, value in cases:
        item = resolver.resolve(WebField(field_id=key, selector="#" + key, label=label, required=True))
        assert item.status == ResolutionStatus.RESOLVED
        assert item.canonical_key == key
        assert item.value == value

def test_family_section_context_disambiguates_name_and_related_fields():
    resolver = _resolver()
    cases = [
        ("姓名", "family.primary.name", "Example Mother"),
        ("与本人关系", "family.primary.relationship", "母亲"),
        ("工作单位", "family.primary.work_unit", "Example Village"),
        ("所属部门及职务", "family.primary.department_title", "务农"),
        ("工作所在地", "family.primary.work_location", "Example County"),
    ]
    for label, key, value in cases:
        item = resolver.resolve(WebField(
            field_id=key,
            selector="#" + key,
            label=label,
            required=True,
            metadata={"section": "家庭情况"},
        ))
        assert item.status == ResolutionStatus.RESOLVED
        assert item.canonical_key == key
        assert item.value == value

def test_privacy_and_truth_declarations_still_require_confirmation():
    resolver = _resolver()
    for label in ["我已阅读并同意隐私政策", "本人承诺以上信息真实有效"]:
        item = resolver.resolve(WebField(
            field_id="decl", selector="#decl", label=label, required=True
        ))
        assert item.status == ResolutionStatus.USER_CONFIRMATION
