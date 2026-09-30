"""Profile import validates supported shapes before publishing private state."""
import json
import stat
from pathlib import Path

import pytest

from executor import settings
from executor.autonomy import profile_setup
from executor.models import ApplicantProfile, AssetRef, EvidenceRef, ProfileField
from executor.profile import get_field


def _canonical(value="Synthetic Applicant"):
    return {"fields": {"identity.full_name": {"value": value}}}


@pytest.fixture
def selected_profile(tmp_path, monkeypatch):
    parent = tmp_path / "profile-import"
    parent.mkdir(mode=0o700)
    previous = parent / "previous-profile.json"
    previous.write_text(json.dumps(_canonical("PRIVATE_PREVIOUS")), encoding="utf-8")
    previous.chmod(0o400)
    monkeypatch.setattr(settings, "PATH", parent / "settings.json")
    monkeypatch.setattr(settings, "PACKAGED", True)
    original = {"profile_path": str(previous),
                "deepseek": {"enabled": False, "keychain_service": "unchanged"},
                "unknown_preference": {"keep": True}}
    settings.save_settings(original)
    return previous, original


def _snapshot(parent):
    return (parent.stat().st_mode,
            {path.name: (path.read_bytes(), path.stat().st_mode)
             for path in parent.iterdir()})


@pytest.mark.parametrize("profile", [
    {"name": "PRIVATE_ARBITRARY"},
    {"unrelated": {"value": "PRIVATE_ARBITRARY"}},
    {"schema_version": "1.0"},
    {"fields": None},
    {"fields": []},
    {"fields": "PRIVATE_FIELDS"},
    {"fields": {"identity.full_name": "PRIVATE_NOT_A_FIELD"}},
    {"fields": {"identity.full_name": None}},
    {"fields": {"identity.full_name": []}},
    {"fields": {" ": {"value": "PRIVATE_EMPTY_KEY"}}},
    {**_canonical(), "schema_version": "2.0"},
    {**_canonical(), "schema_version": 1},
    {**_canonical(), "generated_at": {}},
    {**_canonical(), "collections": []},
    {**_canonical(), "assets": []},
    {**_canonical(), "assets": {"resume": "PRIVATE_PATH"}},
    {**_canonical(), "assets": {"resume": {"path": "PRIVATE_PATH"}}},
    {**_canonical(), "assets": {"resume": {"path": [], "kind": "resume_pdf"}}},
    {**_canonical(), "source_documents": {}},
    {**_canonical(), "source_documents": ["PRIVATE_SOURCE"]},
    {**_canonical(), "source_documents": [{"path": "PRIVATE_SOURCE"}]},
    {"identity": "PRIVATE_NOT_AN_OBJECT"},
    {"identity": {}},
    {"identity": {"unrelated": "PRIVATE_UNKNOWN"}},
    {"identity": {"full_name": {"value": "PRIVATE_WRAPPED_LEGACY"}}},
    {"identity": {"full_name": "PRIVATE_VALID"}, "education": []},
    {"identity": {"full_name": "PRIVATE_VALID"}, "fields": []},
    {"identity": {"full_name": "PRIVATE_VALID"}, "source_documents": [None]},
])
def test_invalid_structure_preserves_selected_profile_and_modes(selected_profile, profile):
    previous, original = selected_profile
    before = _snapshot(previous.parent)
    version = settings.settings_version(original)

    with pytest.raises(ValueError, match="^invalid_profile$"):
        profile_setup.select_profile(json.dumps(profile), version)

    assert _snapshot(previous.parent) == before
    assert settings.load_settings() == original


@pytest.mark.parametrize("attribute,value", [
    ("confidence", "PRIVATE_NOT_A_NUMBER"),
    ("confidence", True),
    ("user_confirmed", "true"),
    ("user_confirmed", 1),
    ("sensitive", "false"),
    ("aliases", "PRIVATE_NOT_A_LIST"),
    ("aliases", [1]),
    ("normalization", []),
    ("last_verified", []),
    ("sources", {}),
    ("sources", ["PRIVATE_NOT_A_SOURCE"]),
    ("sources", [{}]),
    ("sources", [{"kind": [], "path": "PRIVATE_SOURCE"}]),
    ("sources", [{"kind": "user_explicit", "verified_at": {}}]),
])
def test_invalid_field_metadata_is_not_coerced_or_exposed(selected_profile, attribute, value):
    previous, original = selected_profile
    before = _snapshot(previous.parent)
    profile = _canonical()
    profile["fields"]["identity.full_name"][attribute] = value

    with pytest.raises(ValueError, match="^invalid_profile$"):
        profile_setup.select_profile(json.dumps(profile), settings.settings_version(original))

    assert _snapshot(previous.parent) == before


@pytest.mark.parametrize("text", [
    '{"fields":{},"fields":{"identity.full_name":{"value":"PRIVATE_DUPLICATE"}}}',
    '{"fields":{"identity.full_name":{"value":"PRIVATE_FIRST","value":"PRIVATE_SECOND"}}}',
    '{"fields":{"identity.full_name":{"value":NaN}}}',
    '{"fields":{"identity.full_name":{"value":Infinity}}}',
    '{"fields":{"identity.full_name":{"value":1e999}}}',
    '{"fields":{"identity.full_name":{"value":"\\ud800"}}}',
    '{"fields":{"identity.full_name":{"value":"' + 'x' * (256 * 1024) + '"}}}',
])
def test_valid_shape_does_not_bypass_json_or_size_guards(selected_profile, text):
    previous, original = selected_profile
    before = _snapshot(previous.parent)

    with pytest.raises(ValueError, match="^invalid_profile$"):
        profile_setup.select_profile(text, settings.settings_version(original))

    assert _snapshot(previous.parent) == before


@pytest.mark.parametrize("profile", [
    {"fields": {}},
    _canonical(),
    {"fields": {"custom.fact": {"value": None}}},
    {"fields": {"custom.fact": {}}},
    {"identity": {"full_name": "Synthetic Applicant"}},
    {"identity": {"full_name": "", "phone": None}},
    {"education": {"highest": {"school": "Synthetic School"}}},
    {"education": {"school": "Synthetic School", "gpa": 0}},
    {"family": {"primary": {"relationship": "Synthetic Relationship"}}},
    {"preferences": {"preferred_cities": ["Synthetic City"], "accept_travel": False}},
    {"identity": {"household_before_gaokao": "Synthetic City"}},
    {"application_policy": {"final_submission_requires_user_click": True}},
])
def test_supported_incomplete_and_legacy_shapes_round_trip(profile):
    assert json.loads(profile_setup._profile_bytes(json.dumps(profile))) == profile


def test_documented_legacy_candidate_example_is_still_supported():
    example = Path(__file__).resolve().parents[1] / "config" / "candidate-profile.example.json"
    text = example.read_text(encoding="utf-8")
    assert json.loads(profile_setup._profile_bytes(text)) == json.loads(text)


def test_generated_canonical_profile_keeps_facts_provenance_assets_and_extensions(selected_profile):
    previous, original = selected_profile
    old_bytes, old_mode = previous.read_bytes(), previous.stat().st_mode
    source = EvidenceRef(kind="user_explicit", locator="synthetic:fixture",
                         verified_at="2026-09-30", note="Synthetic provenance")
    profile = ApplicantProfile(
        fields={
            "identity.full_name": ProfileField(value="Synthetic Applicant", sources=[source],
                                                aliases=["姓名"], user_confirmed=True),
            "preferences.accept_travel": ProfileField(value=False, confidence=0,
                                                       user_confirmed=False, sensitive=True),
            "custom.pending": ProfileField(value=None),
        },
        assets={"resume": AssetRef(path="/synthetic/not-read.pdf", kind="resume_pdf",
                                    sha256="a" * 64, source=source)},
        collections={"projects": [{"id": "synthetic-project", "metadata": ["Synthetic"]}],
                     "profile_write_version": 2},
        source_documents=[source],
    ).model_dump(mode="json")
    profile["unrecognized_evidence"] = {"original": "完整 👩🏽‍💻", "future": [False, 0, None]}
    profile["fields"]["identity.full_name"]["extension"] = {"keep": "original"}

    selected = profile_setup.select_profile(json.dumps(profile), settings.settings_version(original))
    imported = Path(selected["profile_path"])
    observed = json.loads(imported.read_text(encoding="utf-8"))

    assert observed == profile
    assert imported != previous and imported.parent == previous.parent
    assert stat.S_IMODE(imported.stat().st_mode) == 0o600
    assert (previous.read_bytes(), previous.stat().st_mode) == (old_bytes, old_mode)
    assert selected == {**original, "profile_path": str(imported)}
    assert get_field(observed, "preferences.accept_travel").value is False
    assert get_field(observed, "identity.full_name").sources[0] == source


def test_empty_generated_canonical_profile_is_still_supported():
    profile = ApplicantProfile().model_dump(mode="json")
    assert json.loads(profile_setup._profile_bytes(json.dumps(profile))) == profile


@pytest.mark.parametrize("key", ["password", " OTP ", "auth.token", "API_KEY",
                                  "access_token", "refresh_token", "deepseek"])
@pytest.mark.parametrize("legacy", [False, True])
def test_secret_scan_still_covers_unknown_nested_metadata(selected_profile, key, legacy):
    previous, original = selected_profile
    before = _snapshot(previous.parent)
    profile = {"identity": {"full_name": "Synthetic Applicant"}} if legacy else _canonical()
    profile["unrecognized_evidence"] = [{"nested": {key: "PRIVATE_SECRET"}}]

    with pytest.raises(ValueError, match="^invalid_profile$"):
        profile_setup.select_profile(json.dumps(profile), settings.settings_version(original))

    assert _snapshot(previous.parent) == before


@pytest.mark.parametrize("fault", ["fchmod", "fsync", "replace"])
def test_failed_publication_preserves_prior_profile_settings_and_modes(selected_profile, monkeypatch, fault):
    previous, original = selected_profile
    parent_mode, before = _snapshot(previous.parent)

    def fail(*_args, **_kwargs):
        raise OSError("PRIVATE_FAILURE_DETAILS")

    monkeypatch.setattr(profile_setup.os, fault, fail)
    with pytest.raises(ValueError, match="^settings_unavailable$"):
        profile_setup.select_profile(json.dumps(_canonical()), settings.settings_version(original))

    after_mode, after = _snapshot(previous.parent)
    assert parent_mode == after_mode
    assert {name: after[name] for name in before} == before
    assert settings.load_settings() == original
    assert not list(previous.parent.glob(".settings-*.tmp"))
    for name in after.keys() - before.keys():
        assert fault == "replace"  # An inactive, complete private version may remain.
        assert json.loads(after[name][0]) == _canonical()
        assert stat.S_IMODE(after[name][1]) == 0o600


def test_invalid_profile_does_not_create_missing_settings_authority(tmp_path, monkeypatch):
    target = tmp_path / "missing" / "settings.json"
    monkeypatch.setattr(settings, "PATH", target)

    with pytest.raises(ValueError, match="^invalid_profile$"):
        profile_setup.select_profile('{"unrelated":"PRIVATE_VALUE"}', "a" * 64)

    assert not target.parent.exists()
