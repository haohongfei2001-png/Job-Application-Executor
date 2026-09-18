from executor.models import ApplicationPlan, ApplicationStage, FieldResolution, ResolutionStatus
from executor.review import build_final_review, project_coverage_review


def _profile():
    return {
        "collections": {
            "projects": [
                {"title": "Project Alpha"},
                {"title": "Research Beta"},
                {"title": "Project Gamma"},
            ]
        }
    }


def _field(label, value):
    return FieldResolution(
        field_id=label,
        selector="#" + label,
        label=label,
        status=ResolutionStatus.KEEP_EXISTING,
        value=value,
    )


def test_project_coverage_surfaces_silent_omissions():
    plan = ApplicationPlan(
        execution_id="review-fixture",
        target_url="https://example.test/apply",
        site_id="generic_web",
        stage=ApplicationStage.READY_TO_SUBMIT,
        fields=[
            _field("项目名称", "Project Alpha"),
            _field("Project name", "Project Gamma"),
        ],
    )
    review = project_coverage_review(_profile(), plan)
    assert review["structured_count"] == 2
    assert review["uncovered_projects"] == ["Research Beta"]
    assert review["status"] == "REVIEW_REQUIRED"
    assert review["rules"]["attachment_is_not_substitute_for_structured_project_fields"] is True


def test_explicit_project_exclusion_closes_coverage_gap():
    plan = ApplicationPlan(
        execution_id="review-exclusion",
        target_url="https://example.test/apply",
        site_id="generic_web",
        stage=ApplicationStage.READY_TO_SUBMIT,
        fields=[
            _field("项目名称", "Project Alpha"),
            _field("Project name", "Project Gamma"),
        ],
        metadata={"project_exclusions": ["Research Beta"]},
    )
    review = project_coverage_review(_profile(), plan)
    assert review["uncovered_projects"] == []
    assert review["status"] == "COVERED_OR_EXPLICITLY_EXCLUDED"


def test_final_review_requires_user_click_and_parser_audit():
    plan = ApplicationPlan(
        execution_id="review-final",
        target_url="https://example.test/apply",
        site_id="generic_web",
        stage=ApplicationStage.READY_TO_SUBMIT,
        attachments={"resume": "/tmp/example-resume.docx"},
    )
    review = build_final_review(_profile(), plan, "Confirm submit")
    assert review["human_review_required"] is True
    assert review["final_click_actor"] == "user"
    assert review["attachment_basenames"]["resume"] == "example-resume.docx"
    assert any("parser-derived resume sections" in item for item in review["checklist"])
