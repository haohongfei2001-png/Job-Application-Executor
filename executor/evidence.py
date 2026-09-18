from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docx import Document

from .models import ApplicantProfile, AssetRef, EvidenceRef, ProfileField
from .profile import aliases_for, is_sensitive_key


LABEL_MAP = {
    "姓名": "identity.full_name",
    "英文名": "identity.english_name",
    "性别": "identity.gender",
    "出生日期": "identity.birth_date",
    "民族": "identity.ethnicity",
    "政治面貌": "identity.political_status",
    "证件类型": "identity.id_type",
    "身份证号": "identity.id_number",
    "证件号码": "identity.id_number",
    "手机号码": "identity.phone",
    "手机号": "identity.phone",
    "邮箱": "identity.email",
    "籍贯": "identity.native_place",
    "户籍所在地": "identity.domicile",
    "户籍": "identity.domicile",
    "家庭住址": "identity.family_address",
    "家庭住址（网申复制）": "identity.family_address",
    "高考前户口所在地": "identity.pre_gaokao_domicile",
    "现居住地": "identity.current_residence",
    "现居住城市": "identity.current_city",
    "通信地址": "identity.mailing_address",
    "紧急联系人姓名": "identity.emergency_contact_name",
    "紧急联系人联系方式": "identity.emergency_contact_phone",
    "婚姻状况": "identity.marital_status",
    "驾驶证": "identity.driver_license",
    "生源地": "identity.student_origin",
    "求职身份": "preferences.candidate_status",
    "预计毕业日期": "education.highest.graduation_date",
    "培养/学历取得方式": "education.highest.study_type",
    "期望工作城市": "preferences.preferred_cities",
    "是否接受工作地点调剂": "preferences.accept_location_adjustment",
    "是否接受岗位调剂": "preferences.accept_role_adjustment",
    "是否接受出差": "preferences.accept_travel",
    "是否接受长期驻外": "preferences.accept_long_term_assignment",
    "最早到岗时间": "preferences.earliest_start_date",
    "期望薪资": "preferences.salary_policy",
    "最高学历": "education.highest.degree",
    "最高学历学校": "education.highest.school",
    "最高学历院系": "education.highest.college",
    "最高学历专业": "education.highest.major",
    "最高学历入学时间": "education.highest.start_date",
    "最高学历毕业时间": "education.highest.graduation_date",
    "最高学历受教育类型": "education.highest.study_type",
    "最高学历学习成绩排名档位": "education.highest.rank_band",
    "本科院校": "education.bachelor.school",
    "本科院系": "education.bachelor.college",
    "本科专业": "education.bachelor.major",
    "本科入学时间": "education.bachelor.start_date",
    "本科毕业时间": "education.bachelor.graduation_date",
    "本科受教育类型": "education.bachelor.study_type",
    "本科GPA": "education.bachelor.gpa",
    "培养项目": "education.bachelor.training_program",
    "外语等级": "language.cet6.level",
    "CET-6成绩": "language.cet6.score",
    "普通话": "certificates.mandarin",
    "计算机证书": "certificates.computer",
    "学生干部经历": "experience.student_leadership",
    "校园组织经历": "experience.campus_organization",
    "正式实习经历": "experience.internship",
    "正式工作经历": "experience.work",
    "奖学金 / 荣誉": "awards.summary",
    "个人特长": "narrative.strengths",
    "兴趣爱好": "narrative.interests",
    "自我评价": "narrative.self_evaluation",
}

BOOL_KEYS = {
    "preferences.accept_location_adjustment",
    "preferences.accept_role_adjustment",
}

PENDING_MARKERS = ("待补充", "待查", "未固定", "根据毕业", "建议按")
CONFIRM_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})确认")
ANNOTATION_RE = re.compile(r"（[^）]*(?:确认|未变|按此顺序)[^）]*）")




def _project_title(text: str) -> bool:
    return "｜" in text and not text.startswith(("•", "-", "·"))


def _parse_project_lines(lines: list[str], source_kind: str) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_bullet = False
    for raw in lines:
        line = " ".join(str(raw or "").split()).strip()
        if not line:
            continue
        if _project_title(line):
            if current:
                projects.append(current)
            parts = [x.strip() for x in line.split("｜") if x.strip()]
            current = {
                "title": parts[0],
                "metadata": parts[1:],
                "bullets": [],
                "source_kind": source_kind,
            }
            last_bullet = False
            continue
        if current is None:
            continue
        if line.startswith(("•", "-", "·")):
            current["bullets"].append(line.lstrip("•-· ").strip())
            last_bullet = True
            continue
        if re.fullmatch(r"20\d{2}\.\d{2}[–-](?:20\d{2}\.\d{2}|至今)", line) or line in {"在研", "独立产品设计 / AI 协同开发"}:
            current["metadata"].append(line)
            last_bullet = False
            continue
        if last_bullet and current["bullets"]:
            current["bullets"][-1] += " " + line
        else:
            current["metadata"].append(line)
    if current:
        projects.append(current)
    return projects


def _merge_projects(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [dict(x) for x in existing]
    def project_key(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).casefold()

    index = {project_key(x.get("title", "")): i for i, x in enumerate(out)}
    for item in incoming:
        key = project_key(item.get("title", ""))
        if not key:
            continue
        if key not in index:
            index[key] = len(out)
            out.append(dict(item))
            continue
        old = out[index[key]]
        old_meta = list(old.get("metadata") or [])
        for value in item.get("metadata") or []:
            if value not in old_meta:
                old_meta.append(value)
        old["metadata"] = old_meta
        old_bullets = list(old.get("bullets") or [])
        for value in item.get("bullets") or []:
            if value not in old_bullets:
                old_bullets.append(value)
        old["bullets"] = old_bullets
        sources = set(old.get("sources") or [old.get("source_kind")])
        sources.add(item.get("source_kind"))
        old["sources"] = sorted(x for x in sources if x)
    return out

def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_value(key: str, raw: str):
    text = raw.strip()
    if any(marker in text for marker in PENDING_MARKERS):
        return None
    if key in BOOL_KEYS:
        if text.startswith("是"):
            return True
        if text.startswith("否"):
            return False
    text = ANNOTATION_RE.sub("", text).strip()
    if key == "identity.driver_license":
        match = re.search(r"C[12]", text, re.I)
        return match.group(0).upper() if match else text
    if key == "certificates.mandarin":
        match = re.search(r"[一二三]级[甲乙]等", text)
        return match.group(0) if match else text
    if key == "certificates.computer":
        match = re.search(r"全国计算机等级考试二级（C语言程序设计）", text)
        return match.group(0) if match else text
    if key == "preferences.preferred_cities":
        return [x.strip().removesuffix("市") for x in re.split(r"[>＞]", text) if x.strip()]
    if key == "education.highest.graduation_date":
        m = re.match(r"(20\d{2}-\d{2}(?:-\d{2})?)", text)
        if m:
            return m.group(1)
    return text


def _evidence_priority(kind: str, user_confirmed: bool = False) -> int:
    if kind == "user_explicit":
        return 500
    if kind in {"max_docx", "resume_pdf"}:
        return 420 if user_confirmed else 400
    if kind == "verified_history":
        return 300
    if kind == "legacy_profile":
        return 200
    return 100


def _field_priority(field: ProfileField) -> int:
    priorities = [_evidence_priority(source.kind, field.user_confirmed) for source in field.sources]
    return max(priorities, default=0)


class ProfileBuilder:
    def __init__(self):
        self.profile = ApplicantProfile()
        self.conflicts: list[dict[str, Any]] = []

    def add_field(self, key: str, value: Any, source: EvidenceRef, *, confidence: float = 0.98,
                  user_confirmed: bool = False, last_verified: str | None = None,
                  normalization: dict[str, Any] | None = None):
        if value in (None, ""):
            return
        existing = self.profile.fields.get(key)
        incoming = ProfileField(
            value=value,
            sources=[source],
            confidence=confidence,
            last_verified=last_verified,
            aliases=list(aliases_for(key)),
            normalization=normalization or {},
            user_confirmed=user_confirmed,
            sensitive=is_sensitive_key(key),
        )
        if existing is None:
            self.profile.fields[key] = incoming
            return
        if existing.value == value:
            existing.sources.append(source)
            existing.confidence = max(existing.confidence, confidence)
            existing.user_confirmed = existing.user_confirmed or user_confirmed
            existing.last_verified = existing.last_verified or last_verified
            return
        existing_rank = (_field_priority(existing), existing.confidence)
        incoming_rank = (
            _evidence_priority(source.kind, incoming.user_confirmed),
            incoming.confidence,
        )
        self.conflicts.append({
            "key": key,
            "existing_value": existing.value,
            "incoming_value": value,
            "existing_sources": [s.model_dump() for s in existing.sources],
            "incoming_source": source.model_dump(),
        })
        if incoming_rank > existing_rank:
            self.profile.fields[key] = incoming

    def import_max_docx(self, path: str | Path):
        path = Path(path).expanduser().resolve()
        doc = Document(path)
        source_doc = EvidenceRef(kind="max_docx", path=str(path), note=f"sha256:{file_sha256(path)}")
        self.profile.source_documents.append(source_doc)
        project_lines: list[dict[str, Any]] = []
        in_projects = False
        for index, paragraph in enumerate(doc.paragraphs):
            text = " ".join((paragraph.text or "").split()).strip()
            if not text:
                continue
            if text.startswith("四、科研与项目经历"):
                in_projects = True
                continue
            if text.startswith("五、技能、语言与证书"):
                in_projects = False
            if in_projects:
                project_lines.append({"text": text, "source": f"paragraph:{index}"})
            if "：" not in text:
                continue
            label, raw = text.split("：", 1)
            key = LABEL_MAP.get(label.strip())
            if not key:
                continue
            value = _clean_value(key, raw)
            verified = CONFIRM_RE.search(text)
            verified_at = verified.group(1) if verified else None
            self.add_field(
                key,
                value,
                EvidenceRef(kind="max_docx", path=str(path), locator=f"paragraph:{index}", verified_at=verified_at),
                confidence=1.0 if verified else 0.98,
                user_confirmed=bool(verified),
                last_verified=verified_at,
                normalization={"strategy": "max_label_map", "source_label": label.strip()},
            )
            if label.strip() == "本科专业排名":
                pass

        for index, paragraph in enumerate(doc.paragraphs):
            text = " ".join((paragraph.text or "").split()).strip()
            if text.startswith("本科专业排名："):
                raw = text.split("：", 1)[1]
                rank = raw.split("；", 1)[0].strip()
                band_match = re.search(r"排名档位：([^；]+)$", raw)
                src = EvidenceRef(kind="max_docx", path=str(path), locator=f"paragraph:{index}")
                self.add_field("education.bachelor.rank", rank, src)
                if band_match:
                    self.add_field("education.bachelor.rank_band", band_match.group(1).strip(), src)

        self.profile.collections["projects_from_max"] = project_lines
        parsed_max = _parse_project_lines([x["text"] for x in project_lines], "max_docx")
        self.profile.collections["projects"] = _merge_projects(
            self.profile.collections.get("projects") or [],
            parsed_max,
        )
        return self

    def import_legacy_profile(self, path: str | Path):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            return self
        data = json.loads(path.read_text(encoding="utf-8"))
        mappings = {
            "identity.full_name": ("identity", "full_name"),
            "identity.phone": ("identity", "phone"),
            "identity.email": ("identity", "email"),
            "identity.current_residence": ("identity", "location"),
            "identity.current_city": ("identity", "current_city"),
            "identity.pre_gaokao_domicile": ("identity", "household_before_gaokao"),
            "identity.domicile": ("identity", "household_address"),
            "identity.family_address": ("identity", "family_address"),
            "education.highest.school": ("education", "school"),
            "education.highest.degree": ("education", "degree"),
            "education.highest.major": ("education", "major"),
            "education.highest.graduation_date": ("education", "graduation_date"),
        }
        for key, route in mappings.items():
            cur: Any = data
            for part in route:
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur not in (None, ""):
                self.add_field(
                    key, cur,
                    EvidenceRef(kind="legacy_profile", path=str(path), locator=".".join(route)),
                    confidence=0.90,
                    normalization={"strategy": "legacy_mapping", "source_key": ".".join(route)},
                )
        return self

    def import_resume_pdf(self, path: str | Path):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            return self
        text = ""
        try:
            import pymupdf
            with pymupdf.open(path) as pdf:
                text = "\n".join(page.get_text("text") for page in pdf)
        except Exception as exc:
            self.profile.collections["resume_parse_warning"] = type(exc).__name__
        self.profile.source_documents.append(
            EvidenceRef(kind="resume_pdf", path=str(path), note=f"sha256:{file_sha256(path)}")
        )
        if text.strip():
            self.profile.collections["resume_text_snapshot"] = text.strip()
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            relevant: list[str] = []
            section = None
            for line in lines:
                if line in {"产品项目", "科研经历"}:
                    section = line
                    continue
                if line == "技能与语言":
                    section = None
                if section:
                    relevant.append(line)
            parsed_resume = _parse_project_lines(relevant, "resume_pdf")
            self.profile.collections["projects"] = _merge_projects(
                self.profile.collections.get("projects") or [],
                parsed_resume,
            )
        self.profile.assets["resume"] = AssetRef(
            path=str(path), kind="resume_pdf", sha256=file_sha256(path),
            source=EvidenceRef(kind="resume_pdf", path=str(path)),
        )
        return self

    def add_asset(self, name: str, path: str | Path, kind: str):
        path = Path(path).expanduser().resolve()
        if path.is_file():
            self.profile.assets[name] = AssetRef(
                path=str(path), kind=kind, sha256=file_sha256(path),
                source=EvidenceRef(kind="local_asset", path=str(path)),
            )
        return self

    def import_historical_json(self, scope: str, path: str | Path, *, reconfirm_keys: list[str] | None = None):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            return self
        data = json.loads(path.read_text(encoding="utf-8"))
        records = self.profile.collections.setdefault("historical_answers", {})
        records[scope] = {
            "source": {"kind": "historical_application", "path": str(path), "imported_at": _now()},
            "answers": data,
            "requires_reconfirmation": list(reconfirm_keys or []),
        }

        canonical = data.get("canonical_fields") if isinstance(data, dict) else None
        if isinstance(canonical, dict):
            for key, raw in canonical.items():
                item = raw if isinstance(raw, dict) else {"value": raw}
                if item.get("value") in (None, ""):
                    continue
                self.add_field(
                    str(key),
                    item.get("value"),
                    EvidenceRef(
                        kind="verified_history",
                        path=str(path),
                        locator=f"canonical_fields.{key}",
                        verified_at=item.get("verified_at"),
                        note=f"scope:{scope}",
                    ),
                    confidence=min(float(item.get("confidence", 0.92)), 0.95),
                    user_confirmed=bool(item.get("user_confirmed", False)),
                    last_verified=item.get("verified_at"),
                    normalization={"strategy": "verified_history", "scope": scope},
                )
        return self

    def import_user_overrides(self, path: str | Path):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            return self
        try:
            existing = ApplicantProfile.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return self
        for key, field in existing.fields.items():
            if not any(source.kind == "user_explicit" for source in field.sources):
                continue
            self.add_field(
                key,
                field.value,
                EvidenceRef(
                    kind="user_explicit",
                    path=str(path),
                    locator=key,
                    verified_at=field.last_verified,
                    note="preserved canonical user override",
                ),
                confidence=1.0,
                user_confirmed=True,
                last_verified=field.last_verified,
                normalization={"strategy": "user_explicit"},
            )
        return self

    def build(self) -> ApplicantProfile:
        if self.conflicts:
            self.profile.collections["conflicts"] = self.conflicts
        return self.profile


def set_user_confirmed_field(
    profile_path: str | Path,
    key: str,
    value: Any,
    *,
    note: str | None = None,
) -> Path:
    profile_path = Path(profile_path).expanduser().resolve()
    profile = ApplicantProfile.model_validate_json(profile_path.read_text(encoding="utf-8"))
    previous = profile.fields.get(key)
    verified_at = _now()
    revisions = profile.collections.setdefault("profile_revisions", [])
    revisions.append({
        "key": key,
        "at": verified_at,
        "previous_source_kinds": [x.kind for x in previous.sources] if previous else [],
        "note": note,
    })
    sources = list(previous.sources) if previous else []
    sources.append(EvidenceRef(
        kind="user_explicit",
        path=str(profile_path),
        locator=key,
        verified_at=verified_at,
        note=note,
    ))
    profile.fields[key] = ProfileField(
        value=value,
        sources=sources,
        confidence=1.0,
        last_verified=verified_at,
        aliases=list(aliases_for(key)),
        normalization={"strategy": "user_explicit"},
        user_confirmed=True,
        sensitive=is_sensitive_key(key),
    )
    return write_profile(profile, profile_path)


def write_profile(profile: ApplicantProfile, path: str | Path) -> Path:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path
