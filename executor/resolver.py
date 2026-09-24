from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from typing import Any

from .models import FieldResolution, ResolutionStatus, WebField
from .profile import (aliases_for, field_is_current, get_field, has_unresolved_conflict,
                      is_sensitive_key, profile_keys)
from .facts.representation import represent_choice
from .forms.typed import represent_scoped_salary


PRIVACY_PATTERNS = (
    r"privacy consent|privacy policy|隐私同意|隐私政策|同意.*隐私|个人信息保护|数据处理",
)
TRUTH_DECLARATION_PATTERNS = (
    r"\b(?:i\s+)?(?:certify|declare|attest)\b.*(?:true|accurate|complete)",
    r"(?:truth|accurate).*?(?:statement|declaration|information)",
    r"真实有效|真实性|信息.*真实|本人承诺|本人确认|本人声明|投递声明|简历一经投递|不可修改",
)
LEGAL_COMPLIANCE_PATTERNS = (
    r"visa|work.?authori|sponsor|签证|工作许可",
    r"conflict|利益冲突|竞业|non.?compete|回避",
    r"criminal|background.?check|犯罪|背景调查|处分",
    r"是否为政治公众人物|politically exposed",
    r"合规|compliance|法律声明|legal declaration",
)
LEGAL_ASSENT_PATTERNS = (
    r"同意.*(?:合规|法律|政策|准则|守则|条款)",
    r"承诺.*(?:遵守|合规|守则|准则|政策)",
    r"(?:agree|acknowledge|commit|undertake).*?(?:compliance|code of conduct|policy|legal|terms)",
)
CONFIRMATION_PATTERNS = (
    r"salary|薪资|薪酬|期望年薪|期望月薪",
    r"signature|electronic.?sign|电子签名|签名",
    *PRIVACY_PATTERNS,
    *TRUTH_DECLARATION_PATTERNS,
    *LEGAL_COMPLIANCE_PATTERNS,
)

RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity.email", (r"\be-?mail\b", r"邮箱", r"电子邮件")),
    ("identity.phone", (r"\bphone\b", r"mobile", r"tel", r"手机", r"电话")),
    ("identity.full_name", (r"full.?name", r"姓名", r"中文名")),
    ("identity.english_name", (r"english.?name", r"英文名")),
    ("identity.gender", (r"^性别$", r"^gender$", r"^sex$")),
    ("identity.birth_date", (r"date.?of.?birth", r"birthday", r"出生日期", r"生日")),
    ("identity.id_type", (r"证件类型", r"document.?type", r"id.?type")),
    ("identity.id_number", (r"身份证", r"证件号码", r"id.?number", r"id.?card")),
    ("identity.student_origin", (r"生源地", r"student.?origin", r"place.?of.?origin")),
    ("identity.native_place", (r"^籍贯$", r"native.?place")),
    ("identity.current_city", (r"current.?city", r"living.?city", r"现居城市", r"当前城市")),
    ("identity.current_residence", (r"current.?residence", r"living.?address", r"现居住地", r"当前居住地")),
    ("identity.domicile", (r"户籍所在地", r"户籍", r"domicile", r"hukou")),
    ("identity.pre_gaokao_domicile", (r"高考前户口", r"生源户籍")),
    ("identity.family_address", (r"家庭住址", r"home.?address")),
    ("identity.mailing_address", (r"通信地址", r"mailing.?address")),
    ("identity.marital_status", (r"婚姻状况", r"marital.?status")),
    ("identity.driver_license", (r"驾驶证", r"driver.?licen")),
    ("identity.household_type", (r"户口类别", r"户籍类别", r"household.?type", r"hukou.?type")),
    ("identity.health_status", (r"^健康状况$", r"^身体状况$", r"health.?status")),
    ("identity.personnel_file_place", (r"人事档案所在单位", r"档案所在单位", r"personnel.?file")),
    ("identity.foreign_residency_status", (
        r"是否具有外国国籍.*(?:永久居留|长期居留)",
        r"外国国籍.*(?:永久居留|长期居留)",
        r"foreign.?nationality",
        r"permanent.?residen",
        r"long.?term.?residence",
    )),
    ("compliance.coamc_employee_recusal_requirements_met", (
        r"是否符合中国东方员工工作回避有关要求",
        r"中国东方.*工作回避",
    )),
    ("family.primary.name", (r"家庭成员.*姓名", r"家属.*姓名")),
    ("family.primary.relationship", (r"家庭成员.*关系", r"家属.*关系")),
    ("family.primary.work_unit", (r"家庭成员.*工作单位", r"家属.*工作单位")),
    ("family.primary.department_title", (r"家庭成员.*(?:部门.*职务|职务)", r"家属.*(?:部门.*职务|职务)")),
    ("family.primary.work_location", (r"家庭成员.*工作所在地", r"家属.*工作所在地")),
    ("education.bachelor.school", (r"本科.*(?:院校|学校)", r"(?:bachelor|undergraduate).*(?:school|university)")),
    ("education.bachelor.college", (r"本科.*院系", r"(?:bachelor|undergraduate).*(?:department|faculty)")),
    ("education.bachelor.major", (r"本科.*专业", r"(?:bachelor|undergraduate).*major")),
    ("education.bachelor.start_date", (r"本科.*入学", r"(?:bachelor|undergraduate).*start")),
    ("education.bachelor.graduation_date", (r"本科.*毕业", r"(?:bachelor|undergraduate).*graduat")),
    ("education.bachelor.gpa", (r"本科.*gpa", r"(?:bachelor|undergraduate).*gpa")),
    ("education.bachelor.rank", (r"本科.*专业排名", r"(?:bachelor|undergraduate).*rank")),
    ("education.highest.school", (r"最高学历.*(?:学校|院校)", r"硕士.*(?:学校|院校)", r"(?:master|graduate).*(?:school|university)")),
    ("education.highest.college", (r"最高学历.*院系", r"硕士.*院系", r"(?:master|graduate).*(?:department|faculty)")),
    ("education.highest.degree", (r"最高学历", r"硕士.*学历", r"highest.?degree")),
    ("education.highest.major", (r"最高学历.*专业", r"硕士.*专业", r"(?:master|graduate).*major")),
    ("education.highest.graduation_date", (r"最高学历.*毕业", r"硕士.*毕业", r"(?:master|graduate).*graduat")),
    ("education.highest.rank_band", (r"最高学历.*排名", r"硕士.*排名", r"ranking.?band", r"排名档位")),
    ("language.cet6.level", (r"cet.?6", r"英语六级", r"外语等级")),
    ("language.cet6.score", (r"cet.?6.*成绩", r"六级成绩")),
    ("certificates.mandarin", (r"普通话", r"mandarin")),
    ("certificates.computer", (r"计算机.*证书", r"computer.*certificate")),
    ("preferences.preferred_cities", (r"期望工作城市", r"意向城市", r"preferred.?cit")),
    ("preferences.accept_location_adjustment", (r"工作地点调剂", r"location.?adjust")),
    ("preferences.accept_role_adjustment", (r"岗位调剂", r"role.?adjust")),
    ("preferences.accept_travel", (r"接受出差", r"business.?travel")),
    ("preferences.accept_long_term_assignment", (r"长期驻外", r"long.?term.?assignment")),
    ("preferences.earliest_start_date", (r"最早到岗", r"available.?start")),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


UNTRUSTED_INSTRUCTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"system\s+prompt|developer\s+(?:message|instruction)|"
    r"忽略(?:之前|以上|所有).{0,12}(?:指令|规则)|"
    r"执行(?:shell|命令)|自动(?:点击|提交)(?:申请|投递)", re.I,
)
PRIVATE_LABEL = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"(?<!\d)1[3-9]\d{9}(?!\d)|"
    r"(?<!\d)\d{15,18}[0-9Xx]?(?!\d)|CANARY_PRIVATE", re.I,
)

# Site labels are untrusted text and can embed novel personal values that a
# regex-based PII scanner cannot recognize. Only these fixed semantic hints
# may cross the model boundary; all unmatched text stays local.
MODEL_LABEL_HINTS: tuple[tuple[str, str], ...] = (
    ("email", r"\be-?mail\b|邮箱|电子邮件"),
    ("phone", r"\b(?:phone|mobile|telephone)\b|手机|电话"),
    ("name", r"\bname\b|姓名|名字"),
    ("gender", r"\bgender\b|性别"),
    ("birth_date", r"\b(?:birth|birthday)\b|出生|生日"),
    ("address", r"\baddress\b|地址|住址"),
    ("city", r"\bcity\b|城市"),
    ("nationality", r"\bnationality\b|国籍"),
    ("education", r"\b(?:education|school|university|college|degree|major|gpa)\b|教育|学校|院校|学历|学位|专业"),
    ("experience", r"\b(?:experience|employer|employment|company)\b|工作经历|实习|公司"),
    ("project", r"\bproject\b|项目"),
    ("family", r"\b(?:family|father|mother|parent)\b|家庭|父亲|母亲|家属"),
    ("political_status", r"\bpolitical\b|政治面貌"),
    ("salary", r"\bsalary\b|薪资|薪酬"),
    ("date", r"\bdate\b|日期|时间"),
)
MODEL_INPUT_TYPES = {"text", "email", "tel", "number", "select", "checkbox", "radio", "date", "textarea"}
MODEL_CANONICAL_KEYS = {key for key, _ in RULES}


def _model_label_hints(label: str) -> list[str]:
    return [hint for hint, pattern in MODEL_LABEL_HINTS if re.search(pattern, label, re.I)]


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [])


def _needs_user_confirmation(label: str) -> str | None:
    text = _norm(label)
    for pattern in CONFIRMATION_PATTERNS:
        if re.search(pattern, text, re.I):
            return pattern
    return None


def _matches_any(label: str, patterns: tuple[str, ...]) -> bool:
    text = _norm(label)
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _standing_policy_key(label: str) -> str | None:
    if _matches_any(label, PRIVACY_PATTERNS):
        return "policy.auto_accept_privacy_terms"
    if _matches_any(label, TRUTH_DECLARATION_PATTERNS):
        return "policy.auto_accept_truth_submission_declarations"
    if _matches_any(label, LEGAL_COMPLIANCE_PATTERNS):
        return "policy.auto_decide_company_legal_compliance"
    return None


def _scoped_policy_approved(policy_field, field: WebField) -> bool:
    """A general policy flag cannot authorize new site text or a new target."""
    if not policy_field or not policy_field.user_confirmed or policy_field.value is not True:
        return False
    if not field.page_url:
        return False
    scope = policy_field.normalization or {}
    text_hash = hashlib.sha256(_norm(field.label).encode("utf-8")).hexdigest()
    target_hash = hashlib.sha256(field.page_url.encode("utf-8")).hexdigest()
    return (scope.get("approved_text_sha256") == text_hash
            and scope.get("approved_target_sha256") == target_hash)


def _rule_key(label: str) -> tuple[str | None, float, str]:
    text = _norm(label)
    for key, patterns in RULES:
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            return key, 0.99, "deterministic rule"
    return None, 0.0, ""


def _contextual_rule_key(field: WebField) -> tuple[str | None, float, str]:
    label = _norm(field.label)
    context = _norm(" ".join(str(field.metadata.get(k) or "") for k in ("section", "context")))
    if not re.search(r"家庭情况|家庭成员|家属|family", context, re.I):
        return None, 0.0, ""
    mappings = (
        ("family.primary.relationship", (r"与本人关系", r"关系", r"relationship")),
        ("family.primary.work_unit", (r"工作单位", r"单位", r"company", r"employer")),
        ("family.primary.department_title", (r"所属部门及职务", r"部门.*职务", r"职务", r"title", r"position")),
        ("family.primary.work_location", (r"工作所在地", r"工作地点", r"location")),
        ("family.primary.name", (r"^姓名(?:\s|\||$)", r"^name(?:\s|\||$)")),
    )
    for key, patterns in mappings:
        if any(re.search(pattern, label, re.I) for pattern in patterns):
            return key, 1.0, "family section context"
    return None, 0.0, ""


def _alias_key(profile: dict[str, Any], label: str) -> tuple[str | None, float]:
    target = _norm(label)
    ambiguous_education = {"school", "university", "college", "major", "degree", "gpa", "graduation date", "院校", "学校", "专业", "学历", "毕业时间"}
    if target in ambiguous_education:
        return None, 0.0
    best_key, best_score = None, 0.0
    for key in profile_keys(profile):
        candidates = set(aliases_for(key))
        field = get_field(profile, key)
        if field:
            candidates.update(field.aliases)
        for alias in candidates:
            alias_norm = _norm(alias)
            if not alias_norm:
                continue
            if alias_norm in target or target in alias_norm:
                score = 0.94
            else:
                score = SequenceMatcher(None, alias_norm, target).ratio()
            if score > best_score:
                best_key, best_score = key, score
    return (best_key, best_score) if best_score >= 0.82 else (None, best_score)


def _keychain_key(service: str, account: str | None = None) -> str | None:
    cmd = ["security", "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", account]
    cmd += ["-w"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        value = proc.stdout.strip()
        return value if proc.returncode == 0 and value else None
    except Exception:
        return None


class DeepSeekMapper:
    def __init__(self, settings: dict[str, Any] | None = None):
        cfg = (settings or {}).get("deepseek") or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.base_url = str(cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.model = str(cfg.get("model") or "deepseek-v4-pro")
        self.timeout = int(cfg.get("timeout_seconds") or 120)
        self.proxy = cfg.get("proxy") or None
        self.reasoning_effort = str(cfg.get("reasoning_effort") or "low")
        self.max_tokens = int(cfg.get("max_tokens") or 500)
        env_name = str(cfg.get("api_key_env") or "DEEPSEEK_API_KEY")
        self.api_key = os.getenv(env_name)
        if not self.api_key:
            services = [
                str(cfg.get("keychain_service") or ""),
                "AI-Supervisor-DeepSeek",
                "deepseek-api-key",
                "DeepSeek API Key",
                "DeepSeek",
            ]
            for service in [x for x in services if x]:
                self.api_key = _keychain_key(service, cfg.get("keychain_account"))
                if self.api_key:
                    break

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def map_field(self, field: WebField, keys: list[str]) -> tuple[str | None, float, str]:
        if not self.available or not keys:
            return None, 0.0, "DeepSeek unavailable"
        # Site labels can contain rendered applicant data or attacker text.
        # Options are never needed to select a canonical key and may include
        # a phone, identity number or prefilled personal choice.
        label = str(field.label or "")[:180]
        if PRIVATE_LABEL.search(label) or UNTRUSTED_INSTRUCTION.search(label):
            return None, 0.0, "field label contains private data"
        hints = _model_label_hints(label)
        allowed_keys = [key for key in keys if key in MODEL_CANONICAL_KEYS]
        if not hints or not allowed_keys or field.input_type not in MODEL_INPUT_TYPES:
            return None, 0.0, "field has no safe model mapping context"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Map a web form field to exactly one supplied canonical applicant-profile key. "
                        "You may interpret field meaning only. Never invent a personal fact or value. "
                        "Return JSON with canonical_key, confidence, reason. Use canonical_key=null if ambiguous."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "label_hints": hints,
                        "input_type": field.input_type,
                        "required": field.required,
                        "allowed_keys": allowed_keys,
                    }, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            handlers = []
            if self.proxy:
                handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
            opener = urllib.request.build_opener(*handlers)
            try:
                response = opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in {400, 422}:
                    raise
                fallback = dict(payload)
                fallback.pop("thinking", None)
                fallback.pop("reasoning_effort", None)
                req = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(fallback, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )
                response = opener.open(req, timeout=self.timeout)
            with response as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = (result["choices"][0]["message"].get("content") or "").strip()
            try:
                parsed = json.loads(content)
            except Exception:
                match = re.search(r"\{.*\}", content, re.S)
                if not match:
                    raise
                parsed = json.loads(match.group(0))
            key = parsed.get("canonical_key")
            confidence = float(parsed.get("confidence") or 0.0)
            reason = str(parsed.get("reason") or "DeepSeek semantic mapping")
            if key not in allowed_keys:
                return None, 0.0, "DeepSeek returned unknown key"
            return key, confidence, reason
        except Exception as exc:
            return None, 0.0, f"DeepSeek mapping failed: {type(exc).__name__}"


class FieldResolver:
    def __init__(self, profile: dict[str, Any], settings: dict[str, Any] | None = None):
        self.profile = profile
        self.ai = DeepSeekMapper(settings)

    def resolve(self, field: WebField) -> FieldResolution:
        if UNTRUSTED_INSTRUCTION.search(field.label) or PRIVATE_LABEL.search(field.label):
            return FieldResolution(
                field_id=field.field_id, selector=field.selector, label=field.label,
                status=ResolutionStatus.UNRESOLVED, reason="untrusted field instruction",
                required=field.required,
            )
        if re.search(r"salary|薪资|薪酬|期望年薪|期望月薪", field.label, re.I):
            salary = get_field(self.profile, "preferences.expected_salary")
            represented = (represent_scoped_salary(
                salary.value, salary.normalization.get("salary") or {},
                field.metadata.get("salary") or {}, field.page_url)
                if salary and salary.user_confirmed and field_is_current(salary) else None)
            if represented is not None:
                return FieldResolution(
                    field_id=field.field_id, selector=field.selector,
                    label=field.label, canonical_key="preferences.expected_salary",
                    status=ResolutionStatus.RESOLVED, value=represented,
                    source="user_confirmed_scoped_salary", confidence=1.0,
                    reason="exact target, currency, period and tax basis; explicit unit conversion",
                    required=field.required,
                )
            return FieldResolution(
                field_id=field.field_id, selector=field.selector,
                label=field.label, canonical_key="preferences.expected_salary",
                status=ResolutionStatus.USER_CONFIRMATION, required=field.required,
                reason="salary target or typed representation requires confirmation",
            )
        key, confidence, reason = _contextual_rule_key(field)
        if not key:
            key, confidence, reason = _rule_key(field.label)
        if not key:
            key, confidence = _alias_key(self.profile, field.label)
            reason = "profile alias match" if key else ""

        if key:
            profile_field = get_field(self.profile, key)
            if profile_field and _nonempty(profile_field.value):
                if has_unresolved_conflict(self.profile, key) or not field_is_current(profile_field):
                    return FieldResolution(
                        field_id=field.field_id, selector=field.selector,
                        label=field.label, canonical_key=key,
                        status=ResolutionStatus.UNRESOLVED,
                        reason=("conflicting sources require explicit local confirmation"
                                if has_unresolved_conflict(self.profile, key)
                                else "canonical fact validity expired or unproven"),
                        required=field.required,
                        sensitive=profile_field.sensitive or is_sensitive_key(key),
                    )
                source = "user_confirmed_profile" if profile_field.user_confirmed else "evidence_profile"
                represented = profile_field.value
                if field.input_type == "select" and field.options:
                    choice = represent_choice(profile_field.value, field.options)
                    if choice is None:
                        return FieldResolution(
                            field_id=field.field_id, selector=field.selector,
                            label=field.label, canonical_key=key,
                            status=ResolutionStatus.UNRESOLVED,
                            reason="site option representation is unverified",
                            required=field.required,
                            sensitive=profile_field.sensitive or is_sensitive_key(key),
                        )
                    represented = choice.site_value
                return FieldResolution(
                    field_id=field.field_id, selector=field.selector, label=field.label,
                    canonical_key=key, status=ResolutionStatus.RESOLVED,
                    value=represented, source=source,
                    confidence=min(confidence or 0.9, profile_field.confidence),
                    reason=reason, required=field.required,
                    sensitive=profile_field.sensitive or is_sensitive_key(key),
                )

        confirmation = _needs_user_confirmation(field.label)
        if confirmation:
            policy_key = _standing_policy_key(field.label)
            policy_field = get_field(self.profile, policy_key) if policy_key else None
            policy_enabled = bool(policy_field and policy_field.user_confirmed and policy_field.value is True)
            scoped_approval = _scoped_policy_approved(policy_field, field)

            if scoped_approval and policy_key in {
                "policy.auto_accept_privacy_terms",
                "policy.auto_accept_truth_submission_declarations",
            }:
                policy_value = True
                if field.input_type == "select" and field.options:
                    choice = represent_choice(True, field.options)
                    if choice is None:
                        return FieldResolution(
                            field_id=field.field_id, selector=field.selector,
                            label=field.label, canonical_key=policy_key,
                            status=ResolutionStatus.UNRESOLVED, required=field.required,
                            reason="site option representation is unverified",
                        )
                    policy_value = choice.site_value
                return FieldResolution(
                    field_id=field.field_id, selector=field.selector, label=field.label,
                    canonical_key=policy_key, status=ResolutionStatus.RESOLVED,
                    value=policy_value, source="standing_user_policy", confidence=1.0,
                    reason=f"standing user policy: {policy_key}", required=field.required,
                )

            if policy_enabled and policy_key == "policy.auto_decide_company_legal_compliance":
                if _matches_any(field.label, LEGAL_ASSENT_PATTERNS):
                    if not scoped_approval:
                        return FieldResolution(
                            field_id=field.field_id, selector=field.selector,
                            label=field.label, canonical_key=policy_key,
                            status=ResolutionStatus.USER_CONFIRMATION,
                            reason="new legal assent text or target requires explicit approval",
                            required=field.required,
                        )
                    policy_value = True
                    if field.input_type == "select" and field.options:
                        choice = represent_choice(True, field.options)
                        if choice is None:
                            return FieldResolution(
                                field_id=field.field_id, selector=field.selector,
                                label=field.label, canonical_key=policy_key,
                                status=ResolutionStatus.UNRESOLVED, required=field.required,
                                reason="site option representation is unverified",
                            )
                        policy_value = choice.site_value
                    return FieldResolution(
                        field_id=field.field_id, selector=field.selector, label=field.label,
                        canonical_key=policy_key, status=ResolutionStatus.RESOLVED,
                        value=policy_value, source="standing_user_policy", confidence=1.0,
                        reason="standing user policy: company legal/compliance assent",
                        required=field.required,
                    )
                if _nonempty(field.current_value):
                    return FieldResolution(
                        field_id=field.field_id, selector=field.selector, label=field.label,
                        status=ResolutionStatus.KEEP_EXISTING,
                        value=field.current_value, source="site_existing", confidence=0.80,
                        reason="standing autonomous compliance policy preserved existing site value",
                        required=field.required,
                    )
                ai_key, ai_conf, ai_reason = self.ai.map_field(field, profile_keys(self.profile))
                if ai_key and ai_conf >= 0.80:
                    mapped = get_field(self.profile, ai_key)
                    if mapped and _nonempty(mapped.value) and field_is_current(mapped) and not has_unresolved_conflict(self.profile, ai_key):
                        represented = mapped.value
                        if field.input_type == "select" and field.options:
                            choice = represent_choice(mapped.value, field.options)
                            if choice is None:
                                return FieldResolution(
                                    field_id=field.field_id, selector=field.selector,
                                    label=field.label, canonical_key=ai_key,
                                    status=ResolutionStatus.UNRESOLVED, required=field.required,
                                    reason="site option representation is unverified",
                                    sensitive=mapped.sensitive or is_sensitive_key(ai_key),
                                )
                            represented = choice.site_value
                        return FieldResolution(
                            field_id=field.field_id, selector=field.selector, label=field.label,
                            canonical_key=ai_key, status=ResolutionStatus.RESOLVED,
                            value=represented, source="standing_policy_ai_mapping",
                            confidence=min(ai_conf, mapped.confidence),
                            reason=ai_reason, required=field.required,
                            sensitive=mapped.sensitive or is_sensitive_key(ai_key),
                        )
                return FieldResolution(
                    field_id=field.field_id, selector=field.selector, label=field.label,
                    status=ResolutionStatus.UNRESOLVED, required=field.required,
                    confidence=0.0,
                    reason="standing autonomous compliance policy active, but no factual basis was found",
                )

            return FieldResolution(
                field_id=field.field_id, selector=field.selector, label=field.label,
                status=ResolutionStatus.USER_CONFIRMATION, required=field.required,
                reason=f"requires user confirmation: {confirmation}",
            )

        if key:
            if _nonempty(field.current_value):
                return FieldResolution(
                    field_id=field.field_id, selector=field.selector, label=field.label,
                    canonical_key=key, status=ResolutionStatus.KEEP_EXISTING,
                    value=field.current_value, source="site_existing", confidence=0.80,
                    reason="canonical field is known but profile value is missing; preserved site value",
                    required=field.required, sensitive=is_sensitive_key(key),
                )
            return FieldResolution(
                field_id=field.field_id, selector=field.selector, label=field.label,
                canonical_key=key, status=ResolutionStatus.UNRESOLVED,
                confidence=confidence, reason="canonical profile value missing",
                required=field.required, sensitive=is_sensitive_key(key),
            )

        if _norm(field.label) in {"school", "university", "college", "major", "degree", "gpa", "graduation date", "院校", "学校", "专业", "学历", "毕业时间"}:
            return FieldResolution(
                field_id=field.field_id, selector=field.selector, label=field.label,
                status=ResolutionStatus.UNRESOLVED, required=field.required,
                confidence=0.0, reason="education level is ambiguous",
            )

        ai_key, ai_conf, ai_reason = self.ai.map_field(field, profile_keys(self.profile))
        if ai_key and ai_conf >= 0.80:
            profile_field = get_field(self.profile, ai_key)
            if profile_field and _nonempty(profile_field.value) and field_is_current(profile_field) and not has_unresolved_conflict(self.profile, ai_key):
                represented = profile_field.value
                if field.input_type == "select" and field.options:
                    choice = represent_choice(profile_field.value, field.options)
                    if choice is None:
                        return FieldResolution(
                            field_id=field.field_id, selector=field.selector,
                            label=field.label, canonical_key=ai_key,
                            status=ResolutionStatus.UNRESOLVED,
                            reason="site option representation is unverified",
                            required=field.required,
                            sensitive=profile_field.sensitive or is_sensitive_key(ai_key),
                        )
                    represented = choice.site_value
                return FieldResolution(
                    field_id=field.field_id, selector=field.selector, label=field.label,
                    canonical_key=ai_key, status=ResolutionStatus.RESOLVED,
                    value=represented, source="ai_mapping_only",
                    confidence=min(ai_conf, profile_field.confidence),
                    reason=ai_reason, required=field.required,
                    sensitive=profile_field.sensitive or is_sensitive_key(ai_key),
                )

        if _nonempty(field.current_value):
            return FieldResolution(
                field_id=field.field_id, selector=field.selector, label=field.label,
                canonical_key=ai_key, status=ResolutionStatus.KEEP_EXISTING,
                value=field.current_value, source="site_existing", confidence=0.80,
                reason=ai_reason if ai_key else "site already contains a value",
                required=field.required, sensitive=is_sensitive_key(ai_key),
            )

        return FieldResolution(
            field_id=field.field_id, selector=field.selector, label=field.label,
            canonical_key=ai_key, status=ResolutionStatus.UNRESOLVED,
            required=field.required, confidence=0.0,
            reason=ai_reason or "no unique canonical mapping",
            sensitive=is_sensitive_key(ai_key),
        )

    def resolve_all(self, fields: list[WebField]) -> list[FieldResolution]:
        return [self.resolve(field) for field in fields]
