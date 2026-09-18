from docx import Document

from executor.evidence import ProfileBuilder


def test_max_docx_builds_provenance_and_keeps_pending_unset(tmp_path):
    path = tmp_path / "max.docx"
    doc = Document()
    doc.add_paragraph("姓名：Example User")
    doc.add_paragraph("现居住城市：测试市（2026-09-17确认）")
    doc.add_paragraph("驾驶证：C2机动车驾驶证")
    doc.add_paragraph("驾驶证：C2")
    doc.add_paragraph("生源地：[待补充，如网申要求]")
    doc.save(path)

    builder = ProfileBuilder().import_max_docx(path)
    profile = builder.build()

    assert profile.fields["identity.full_name"].value == "Example User"
    city = profile.fields["identity.current_city"]
    assert city.user_confirmed is True
    assert city.last_verified == "2026-09-17"
    assert city.sources[0].kind == "max_docx"
    assert "identity.student_origin" not in profile.fields
    assert profile.fields["identity.driver_license"].value == "C2"
    assert not profile.collections.get("conflicts")


def test_project_merge_normalizes_whitespace():
    from executor.evidence import _merge_projects

    merged = _merge_projects(
        [{"title": "PQCD 框架下 B→K", "metadata": [], "bullets": [], "source_kind": "max_docx"}],
        [{"title": "PQCD框架下 B→K", "metadata": [], "bullets": ["result"], "source_kind": "resume_pdf"}],
    )
    assert len(merged) == 1
    assert merged[0]["bullets"] == ["result"]
    assert set(merged[0]["sources"]) == {"max_docx", "resume_pdf"}


def test_verified_history_requires_explicit_canonical_mapping(tmp_path):
    import json

    raw = tmp_path / "history.json"
    raw.write_text(json.dumps({
        "site_question_123": "old raw answer",
        "canonical_fields": {
            "identity.current_city": {
                "value": "Verified City",
                "verified_at": "2026-01-01",
                "confidence": 0.92,
            }
        },
    }), encoding="utf-8")
    profile = ProfileBuilder().import_historical_json("fixture", raw).build()
    assert profile.fields["identity.current_city"].value == "Verified City"
    assert profile.fields["identity.current_city"].sources[0].kind == "verified_history"
    assert "site_question_123" not in profile.fields


def test_user_explicit_override_survives_profile_rebuild_and_outranks_confirmed_max(tmp_path):
    from executor.evidence import set_user_confirmed_field, write_profile

    source = tmp_path / "max.docx"
    doc = Document()
    doc.add_paragraph("现居住城市：Document City（2026-09-17确认）")
    doc.save(source)

    profile_path = tmp_path / "profile.json"
    write_profile(ProfileBuilder().import_max_docx(source).build(), profile_path)
    set_user_confirmed_field(
        profile_path,
        "identity.current_city",
        "Explicit City",
        note="test confirmation",
    )

    rebuilt = (
        ProfileBuilder()
        .import_max_docx(source)
        .import_user_overrides(profile_path)
        .build()
    )
    field = rebuilt.fields["identity.current_city"]
    assert field.value == "Explicit City"
    assert field.user_confirmed is True
    assert any(x.kind == "user_explicit" for x in field.sources)

def test_new_confirmed_personal_facts_are_parsed_from_max(tmp_path):
    path = tmp_path / "max-new-facts.docx"
    doc = Document()
    doc.add_paragraph("户口类别：农业户口（2026-09-18确认）")
    doc.add_paragraph("生源地：示例省示例市示例县（2026-09-18确认）")
    doc.add_paragraph("健康状况：健康（2026-09-18确认）")
    doc.add_paragraph("人事档案所在单位：示例大学（2026-09-18确认）")
    doc.add_paragraph("是否具有外国国籍或境外长期居留身份：否（2026-09-18确认）")
    doc.add_paragraph("中国东方员工工作回避要求：是（2026-09-18确认）")
    doc.add_paragraph("家庭成员1姓名：示例家属（2026-09-18确认）")
    doc.add_paragraph("家庭成员1关系：母亲（2026-09-18确认）")
    doc.add_paragraph("家庭成员1工作单位：示例村（2026-09-18确认）")
    doc.add_paragraph("家庭成员1部门及职务：务农（2026-09-18确认）")
    doc.add_paragraph("家庭成员1工作所在地：示例省示例市示例县（2026-09-18确认）")
    doc.add_paragraph("网申隐私政策自动决策：是（2026-09-18确认）")
    doc.add_paragraph("网申真实性/投递声明自动决策：是（2026-09-18确认）")
    doc.add_paragraph("新公司法律/合规声明自动决策：是（2026-09-18确认）")
    doc.add_paragraph("最终投递必须本人点击：是（2026-09-18确认）")
    doc.save(path)

    profile = ProfileBuilder().import_max_docx(path).build()
    assert profile.fields["identity.household_type"].value == "农业户口"
    assert profile.fields["identity.student_origin"].value == "示例省示例市示例县"
    assert profile.fields["identity.health_status"].value == "健康"
    assert profile.fields["identity.personnel_file_place"].value == "示例大学"

    assert profile.fields["identity.foreign_residency_status"].value is False
    assert profile.fields["compliance.coamc_employee_recusal_requirements_met"].value is True
    assert profile.fields["family.primary.name"].value == "示例家属"
    assert profile.fields["family.primary.relationship"].value == "母亲"
    assert profile.fields["family.primary.work_unit"].value == "示例村"
    assert profile.fields["family.primary.department_title"].value == "务农"
    assert profile.fields["family.primary.work_location"].value == "示例省示例市示例县"
    assert profile.fields["policy.auto_accept_privacy_terms"].value is True
    assert profile.fields["policy.auto_accept_truth_submission_declarations"].value is True
    assert profile.fields["policy.auto_decide_company_legal_compliance"].value is True
    assert profile.fields["policy.final_submission_requires_user_click"].value is True
    assert all(
        profile.fields[key].user_confirmed
        for key in [
            "identity.household_type",
            "identity.student_origin",
            "identity.health_status",
            "identity.personnel_file_place",
            "identity.foreign_residency_status",
            "compliance.coamc_employee_recusal_requirements_met",
            "family.primary.name",
            "policy.final_submission_requires_user_click",
        ]
    )


def test_docx_resume_is_supported_as_current_asset(tmp_path):
    resume = tmp_path / "resume.docx"
    doc = Document()
    doc.add_paragraph("产品项目")
    doc.add_paragraph("Example Product｜独立项目")
    doc.add_paragraph("• Example bullet")

    doc.add_paragraph("技能与语言")
    doc.save(resume)

    profile = ProfileBuilder().import_resume(resume).build()
    assert profile.assets["resume"].path == str(resume.resolve())
    assert profile.assets["resume"].kind == "resume_docx"
    assert any(x.kind == "resume_docx" for x in profile.source_documents)
