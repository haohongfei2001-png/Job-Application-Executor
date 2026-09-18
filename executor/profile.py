from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ProfileField


CREDENTIAL_KEYS = {
    "password", "otp", "验证码", "token", "secret", "cookie", "authorization",
}

SENSITIVE_PATH_FRAGMENTS = (
    "id_number", "id_card", "credentials_no",
    "emergency_contact_name", "emergency_contact_phone",
    "domicile", "family_address", "mailing_address", "native_place",
    "health_status", "foreign_residency_status", "family.primary",
)

DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "identity.full_name": ("姓名", "中文名", "full name", "name zh"),
    "identity.english_name": ("英文名", "english name"),
    "identity.phone": ("手机号", "手机号码", "电话", "mobile", "phone", "tel"),
    "identity.email": ("邮箱", "电子邮件", "email", "e-mail"),
    "identity.gender": ("性别", "gender", "sex"),
    "identity.birth_date": ("出生日期", "生日", "date of birth", "birthday"),
    "identity.id_type": ("证件类型", "document type", "id type"),
    "identity.id_number": ("身份证号", "证件号码", "id number", "id card"),
    "identity.ethnicity": ("民族", "ethnicity"),
    "identity.political_status": ("政治面貌",),
    "identity.native_place": ("籍贯", "native place"),
    "identity.student_origin": ("生源地", "student origin", "place of origin"),
    "identity.current_city": ("现居城市", "当前居住地", "living city", "current city"),
    "identity.current_residence": ("现居住地", "current residence", "living address"),
    "identity.domicile": ("户籍", "户籍所在地", "domicile", "hukou"),
    "identity.pre_gaokao_domicile": ("高考前户口所在地", "生源户籍"),
    "identity.family_address": ("家庭住址", "home address"),
    "identity.mailing_address": ("通信地址", "mailing address"),
    "identity.marital_status": ("婚姻状况", "marital status"),
    "identity.driver_license": ("驾驶证", "driver license", "driving licence"),
    "identity.household_type": ("户口类别", "户籍类别", "household type", "hukou type"),
    "identity.health_status": ("健康状况", "身体状况", "health status"),
    "identity.personnel_file_place": ("人事档案所在单位", "档案所在单位", "personnel file location"),
    "identity.foreign_residency_status": (
        "是否具有外国国籍或境外长期居留身份",
        "外国国籍",
        "境外永久居留权",
        "长期居留许可",
        "foreign nationality",
        "permanent residency",
        "long-term residence permit",
    ),
    "compliance.coamc_employee_recusal_requirements_met": (
        "中国东方员工工作回避要求",
        "是否符合中国东方员工工作回避有关要求",
    ),
    "family.primary.name": ("家庭成员1姓名", "家庭成员姓名", "家属姓名"),
    "family.primary.relationship": ("家庭成员1关系", "与本人关系", "家属关系"),
    "family.primary.work_unit": ("家庭成员1工作单位", "家属工作单位"),
    "family.primary.department_title": ("家庭成员1部门及职务", "家属部门及职务", "家属职务"),
    "family.primary.work_location": ("家庭成员1工作所在地", "家属工作所在地"),
    "education.highest.school": ("最高学历学校", "院校", "学校", "university", "school"),
    "education.highest.college": ("最高学历院系", "院系", "department", "faculty"),
    "education.highest.major": ("最高学历专业", "专业", "major"),
    "education.highest.degree": ("最高学历", "学历", "degree"),
    "education.highest.start_date": ("最高学历入学时间", "入学时间", "start date"),
    "education.highest.graduation_date": ("最高学历毕业时间", "毕业时间", "graduation date"),
    "education.highest.rank_band": ("最高学历学习成绩排名档位", "成绩排名", "ranking"),
    "education.bachelor.school": ("本科院校", "本科学校"),
    "education.bachelor.college": ("本科院系",),
    "education.bachelor.major": ("本科专业",),
    "education.bachelor.start_date": ("本科入学时间",),
    "education.bachelor.graduation_date": ("本科毕业时间",),
    "education.bachelor.gpa": ("本科GPA", "gpa"),
    "education.bachelor.rank": ("本科专业排名", "专业排名"),
    "language.cet6.level": ("外语等级", "英语等级", "cet-6", "cet6"),
    "language.cet6.score": ("CET-6成绩", "六级成绩"),
    "certificates.mandarin": ("普通话", "普通话水平"),
    "certificates.computer": ("计算机证书", "计算机等级"),
    "preferences.accept_location_adjustment": ("是否接受工作地点调剂", "地点调剂"),
    "preferences.accept_role_adjustment": ("是否接受岗位调剂", "岗位调剂"),
    "preferences.accept_travel": ("是否接受出差", "出差", "business travel"),
    "preferences.accept_long_term_assignment": ("是否接受长期驻外", "长期驻外"),
    "preferences.earliest_start_date": ("最早到岗时间", "到岗时间", "available start date"),
    "preferences.preferred_cities": ("期望工作城市", "意向城市", "preferred city"),
}


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def load_profile(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("candidate profile must be a JSON object")
    for key in _walk_keys(data):
        if key.lower() in CREDENTIAL_KEYS:
            raise ValueError(f"credential-like key is not allowed in candidate profile: {key}")
    return data


def _legacy_get(profile: dict[str, Any], dotted: str):
    cur: Any = profile
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return None if cur is None or cur == "" else cur


def get_field(profile: dict[str, Any], dotted: str | None) -> ProfileField | None:
    if not dotted:
        return None
    fields = profile.get("fields")
    if isinstance(fields, dict) and dotted in fields:
        item = fields[dotted]
        if isinstance(item, dict):
            return ProfileField.model_validate(item)
    value = _legacy_get(profile, dotted)
    if value is None:
        return None
    return ProfileField(
        value=value,
        aliases=list(DEFAULT_ALIASES.get(dotted, ())),
        confidence=0.90,
        user_confirmed=False,
        sensitive=is_sensitive_key(dotted),
    )


def get_value(profile: dict[str, Any], dotted: str | None):
    field = get_field(profile, dotted)
    if field is None or field.value in (None, ""):
        return None
    return field.value


def profile_keys(profile: dict[str, Any]) -> list[str]:
    fields = profile.get("fields")
    if isinstance(fields, dict):
        return sorted(fields)
    out: list[str] = []

    def visit(value: Any, prefix: str = ""):
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict):
                visit(child, path)
            elif child not in (None, ""):
                out.append(path)

    visit(profile)
    return sorted(out)


def is_sensitive_key(key: str | None) -> bool:
    if not key:
        return False
    low = key.lower()
    return any(fragment in low for fragment in SENSITIVE_PATH_FRAGMENTS)


def aliases_for(key: str) -> tuple[str, ...]:
    return DEFAULT_ALIASES.get(key, ())


def masked_preview(key: str, value) -> str:
    text = str(value)
    low = (key or "").lower()
    if "email" in low:
        local, _, domain = text.partition("@")
        return local[:2] + "***@" + domain if domain else "***"
    if "phone" in low:
        return "***" + text[-4:] if len(text) >= 4 else "***"
    if is_sensitive_key(key):
        return "***" + text[-4:] if len(text) >= 4 else "***"
    return text[:80]
