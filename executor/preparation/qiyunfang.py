"""Observed Qiyunfang form contract and conservative, value-private mapping.

This module has no browser write, request, upload, account or submit capability.
The contract is a dated public observation, not a server-side validation claim.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..autonomy.task_preparation import CONTRACT_ROLE, CONTRACT_URL, _field_value

CONTRACT_VERSION = "qiyunfang-public-form-2026-10-01-v1"
OBSERVED_AT = "2026-10-01"
ROOT = "#popupLevelWrap #module1567 .m_siteform .form_container"
ROLE_OPTIONS = (
    "AI应用工程师（武汉）", "AI模型工程师（武汉）", "AI算法工程师（武汉/深圳）",
    "AI Agent工程师（武汉）", "算法工程师-布局布线（武汉）", "算法工程师-安全方向 （武汉/深圳）",
    "智能排程算法工程师-博士（深圳）", "视觉算法工程师-博士（深圳）", "AI for Fab算法工程师-博士（深圳）",
    "软件开发工程师（武汉/深圳）", "测试工程师（武汉）", "渗透测试工程师（武汉）",
    "数字后端设计工程师（成都）", "模拟版图工程师（成都）", "模拟设计工程师（成都）",
    "数字前端设计工程师（成都）", "初级资料开发工程师（武汉）", "运维SRE工程师（武汉/深圳)",
    "服务交付工程师（武汉）", CONTRACT_ROLE, "AI数字化工程师（武汉）", "客户服务与支持工程师（武汉）",
    "运营运维工程师（武汉）", "客户经理（深圳）", "合同商务专员（武汉）", "品牌营销专员（深圳）",
    "HR（武汉）", "文秘（武汉）", "法务专员（武汉）", "财经专员（武汉）", "采购经理（武汉/深圳）",
)


@dataclass(frozen=True)
class Field:
    field_id: str
    data_type: str
    label: str
    control: str
    required_marker: bool
    key: str | None = None
    maxlength: int | None = None
    placeholder: str = ""
    options: tuple[str, ...] = ()
    protected: bool = False

    @property
    def selector(self):
        return ROOT + ' .form_item[data-formid="' + self.field_id + '"][data-type="' + self.data_type + '"]'


FIELDS = (
    Field("0", "0", "姓名", "text", True, "identity.full_name", 100),
    Field("4", "0", "电话号码", "text", True, "identity.phone", 100),
    Field("5", "9", "邮箱号码", "text", True, "identity.email", 50),
    Field("12", "10", "身份证号码", "text", True, maxlength=18, protected=True),
    Field("2", "2", "性别", "radio", True, "identity.gender", options=("男", "女")),
    Field("14", "2", "最高学历", "radio", True, "education.highest.degree", options=("本科", "硕士", "博士")),
    Field("6", "0", "毕业院校", "text", True, "education.highest.school", 100, "最高学历院校"),
    Field("20", "0", "学院", "text", True, "education.highest.college", 100, "最高学历学院"),
    Field("7", "0", "专业", "text", True, "education.highest.major", 100, "最高学历专业"),
    Field("19", "2", "毕业时间", "radio", True, "education.highest.graduation_date", options=("2026年", "2027年", "2028年")),
    Field("15", "2", "英语证书情况", "radio", True, "language.cet6.level", options=("CET-4", "CET-6", "IELTS", "TOEFL", "其他")),
    Field("16", "0", "英语考级分数", "text", False, "language.cet6.score", 100),
    Field("8", "2", "投递岗位1", "radio", True, options=ROLE_OPTIONS),
    Field("13", "2", "投递岗位2", "radio", False, options=ROLE_OPTIONS, protected=True),
    Field("17", "3", "期望工作城市（若岗位涉及多城市可选）", "checkbox", False, "preferences.preferred_cities", options=("武汉", "深圳", "成都")),
    Field("11", "7", "附件简历", "upload", True, protected=True),
    Field("ValidateCode", "ValidateCode", "验证码", "text", True, maxlength=4, protected=True),
    Field("protocol", "protocol", "", "protocol", False, protected=True),
    Field("Submit", "Submit", "", "submit", False, protected=True),
)
ROUTINE_FIELDS = tuple(field for field in FIELDS if not field.protected)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit
            and value == value.strip() and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def map_routine_fields(profile):
    """Return only bounded routine proposals. Never infer missing facts or consent.

    All proposals require private value review and an explicit field selection.
    Exact choice mapping is intentional: a date, degree alias, broad city list or
    different certificate is not permission to select a convenient website option.
    """
    result = []
    for field in ROUTINE_FIELDS:
        value = _field_value(profile, field.key) if field.key else CONTRACT_ROLE
        status, mapped, reason = "manual", None, "ambiguous_or_unsupported"
        if value in (None, "", []):
            status, reason = "missing", "no_local_value"
        elif field.field_id == "15":
            # This source key only represents the applicant's CET-6 record.
            if value == "CET-6":
                status, mapped, reason = "proposed", value, "exact_option"
        elif field.field_id == "16":
            certificate = _field_value(profile, "language.cet6.level")
            if certificate == "CET-6" and type(value) is int and 0 <= value <= 710:
                status, mapped, reason = "proposed", str(value), "cet6_integer_score"
            elif certificate == "CET-6" and _text(value, 3) and value.isascii() and value.isdecimal() and str(int(value)) == value and 0 <= int(value) <= 710:
                status, mapped, reason = "proposed", value, "cet6_integer_score"
        elif field.control == "text" and _text(value, field.maxlength):
            status, mapped, reason = "proposed", value, "exact_text"
        elif field.control == "radio" and isinstance(value, str) and value in field.options:
            status, mapped, reason = "proposed", value, "exact_option"
        elif (field.control == "checkbox" and isinstance(value, list) and value
              and all(isinstance(item, str) for item in value)
              and len(set(value)) == len(value)):
            if all(item in field.options for item in value):
                status, mapped, reason = "proposed", list(value), "exact_options"
        result.append({"field_id": field.field_id, "key": field.key, "label": field.label,
                       "status": status, "value": mapped, "reason": reason,
                       "requires_explicit_selection": True})
    return result


def select_plan(proposals, selected_ids):
    """Strict, ordered routine-only plan. Caller binds this digest in approval."""
    if (not isinstance(selected_ids, list) or not selected_ids
            or any(not isinstance(item, str) for item in selected_ids)
            or len(selected_ids) != len(set(selected_ids))):
        raise ValueError("invalid preparation selection")
    available = {item["field_id"]: item for item in proposals if item["status"] == "proposed"}
    if any(item not in available for item in selected_ids):
        raise ValueError("invalid preparation selection")
    return [{"field_id": field.field_id, "value": available[field.field_id]["value"]}
            for field in ROUTINE_FIELDS if field.field_id in selected_ids]

# Read-only observation of exactly one retained root. Protected values are never
# returned; only emptiness is observed, to refuse an already-used application.
OBSERVE_ROOT = r'''(root, shapeOnly=false) => {
  const visible = e => e.isConnected && !!e.getClientRects().length &&
    !e.closest('[hidden],[inert],[aria-hidden="true"]') &&
    getComputedStyle(e).visibility === 'visible' && getComputedStyle(e).display !== 'none';
  const protectedIds = new Set(['12','13','11','ValidateCode','protocol','Submit']);
  return {
    connected: root.isConnected,
    visible: visible(root),
    fields: [...root.querySelectorAll('.form_item')].map(row => {
      const id = row.getAttribute('data-formid');
      return {
        field_id: id, data_type: row.getAttribute('data-type'),
        label: (row.querySelector('.title > span')?.textContent || '').trim(),
        required_marker: !!row.querySelector('.star'), visible: visible(row),
        controls: [...row.querySelectorAll('input,textarea,select,button,[contenteditable="true"]')].map(e => ({
          tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
          id: e.id, name: e.getAttribute('name') || '', required: !!e.required,
          disabled: !!e.disabled, readonly: !!e.readOnly,
          maxlength: e.getAttribute('maxlength'), placeholder: e.getAttribute('placeholder') || '',
          ...(shapeOnly ? {} : {
          value: protectedIds.has(id) ? null : e.value,
          option_value: e.type === 'radio' || (e.type === 'checkbox' && id === '17') ? e.value : null,
          occupied: e.type === 'radio' || e.type === 'checkbox' ? !!e.checked :
              e.type === 'file' ? !!e.files.length : e.type === 'button' ? false : !!e.value,
          checked: protectedIds.has(id) ? null : !!e.checked,
          }),
          label: e.labels?.length === 1 ? e.labels[0].textContent.trim() : '',
          visible: visible(e), formaction: e.getAttribute('formaction'), has_form_owner: !!e.form,
        })),
        submit_text: id === 'Submit' ? (row.querySelector('.submit.s_1 > .m[data-formid="6"]')?.textContent || '').trim() : '',
        protocol_text: id === 'protocol' ? (row.querySelector('.form_protocol_text')?.textContent || '').trim() : '',
        protocol_title: id === 'protocol' ? (row.querySelector('.form_protocol_title')?.textContent || '').trim() : '',
      };
    }),
    native_forms: root.querySelectorAll('form').length, ancestor_form: !!root.closest('form'),
    control_count: root.querySelectorAll('input,textarea,select,button,[contenteditable="true"]').length,
  };
}'''

# The shape-only branch does not even read dynamic input values. It is used for
# identity re-fencing during human review, including after unexpected metadata
# drift, before any routine-only kernel readback can be considered.
OBSERVE_SHAPE_ROOT = 'root => (' + OBSERVE_ROOT + ')(root, true)'


class ContractChanged(RuntimeError):
    def __init__(self):
        super().__init__("preparation_form_contract_changed")


def validate_observation(observation, *, expected_values=None):
    """Reject drift/ambiguous controls before any primitive; never return values."""
    expected_values = expected_values or {}
    if (not isinstance(observation, dict) or observation.get("connected") is not True
            or observation.get("visible") is not True or observation.get("native_forms") != 0
            or observation.get("ancestor_form") is not False
            or observation.get("control_count") != 90
            or not isinstance(observation.get("fields"), list)
            or len(observation["fields"]) != len(FIELDS)):
        raise ContractChanged()
    for field, observed in zip(FIELDS, observation["fields"]):
        if (observed.get("field_id") != field.field_id or observed.get("data_type") != field.data_type
                or observed.get("label") != field.label or observed.get("visible") is not True
                or observed.get("required_marker") is not field.required_marker):
            raise ContractChanged()
        controls = observed.get("controls")
        count = len(field.options) if field.options else 2 if field.control == "upload" else 0 if field.control == "submit" else 1
        if not isinstance(controls, list) or len(controls) != count:
            raise ContractChanged()
        for index, control in enumerate(controls):
            if (control.get("tag") != "input" or control.get("disabled") is not False
                    or control.get("readonly") is not False or control.get("required") is not False
                    or control.get("formaction") is not None or control.get("has_form_owner") is not False):
                raise ContractChanged()
            expected_type = ("button" if index == 0 else "file") if field.control == "upload" else "checkbox" if field.control == "protocol" else field.control
            if control.get("type") != expected_type:
                raise ContractChanged()
            if field.control == "upload" and index == 1:
                if control.get("name") != "fileselect[]":
                    raise ContractChanged()
            elif control.get("visible") is not True:
                raise ContractChanged()
            if field.options:
                if (control.get("id") != f"M1567R{field.field_id}I{index}"
                        or control.get("name") != f"M1567R{field.field_id}"
                        or control.get("label") != field.options[index]
                        or control.get("option_value") != field.options[index]
                        or (not field.protected and control.get("value") != field.options[index])):
                    raise ContractChanged()
            elif field.control == "text":
                if (control.get("id") or control.get("name")
                        or control.get("maxlength") != str(field.maxlength)
                        or control.get("placeholder") != field.placeholder):
                    raise ContractChanged()
            if field.protected:
                if control.get("occupied") is not False or control.get("value") is not None:
                    raise ContractChanged()
            elif field.options:
                desired = expected_values.get(field.field_id, [])
                desired = [desired] if isinstance(desired, str) else desired
                if control.get("checked") is not (field.options[index] in desired):
                    raise ContractChanged()
            elif control.get("value") != expected_values.get(field.field_id, ""):
                raise ContractChanged()
        if field.control == "submit" and observed.get("submit_text") != "提交":
            raise ContractChanged()
        if field.control == "protocol" and (observed.get("protocol_text") != "我已经阅读并同意" or observed.get("protocol_title") != "《隐私保护协议》"):
            raise ContractChanged()
    return True
