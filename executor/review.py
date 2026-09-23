from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import ApplicationPlan, FieldResolution


PROJECT_NAME_PATTERN = re.compile(
    r"(?:^|\b)(?:项目名称|project\s*(?:name|title)|project)(?:\b|$)",
    re.I,
)


def _norm(value: Any) -> str:
    text = str(value or "").casefold()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def canonical_project_titles(profile: dict[str, Any]) -> list[str]:
    projects = (profile.get("collections") or {}).get("projects") or []
    titles: list[str] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        title = str(project.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def structured_project_names(fields: list[FieldResolution]) -> list[str]:
    names: list[str] = []
    seen_fields: set[tuple[str, str]] = set()
    for item in fields:
        label = str(item.label or "")
        if not PROJECT_NAME_PATTERN.search(label):
            continue
        value = item.value
        identity = (item.field_id, item.selector)
        if isinstance(value, str) and value.strip() and identity not in seen_fields:
            names.append(value.strip())
            seen_fields.add(identity)
    return names


def _covered(title: str, names: list[str]) -> bool:
    """Exact normalized title match retained for historical review contracts."""
    normalized = _norm(title)
    return bool(normalized and any(_norm(name) == normalized for name in names))


def project_coverage_review(
    profile: dict[str, Any],
    plan: ApplicationPlan,
) -> dict[str, Any]:
    collections = profile.get("collections") or {}
    parse_status = collections.get("resume_project_parse_status")
    records = [item for item in (collections.get("projects") or [])
               if isinstance(item, dict) and str(item.get("title") or "").strip()]
    canonical = canonical_project_titles(profile)
    structured = structured_project_names(plan.fields)
    exclusions = [
        str(x).strip()
        for x in (plan.metadata.get("project_exclusions") or [])
        if str(x).strip()
    ]
    scoped = (collections.get("fact_exclusions") or {}).get(
        plan.execution_id, {})
    scoped_ids = set()
    if isinstance(scoped, dict):
        for record in records:
            decision = scoped.get(record.get("id"))
            if (isinstance(decision, dict) and decision.get("source") == "user_explicit_task"
                    and str(decision.get("reason") or "").strip()):
                scoped_ids.add(record.get("id"))
                title = str(record["title"]).strip()
                if title not in exclusions:
                    exclusions.append(title)

    # Exact unique titles can be matched once. Same-title records require an
    # explicit canonical record binding; two identical strings alone cannot
    # prove which education/project row belongs to which source record.
    available = Counter()
    bound: dict[str, set[str]] = defaultdict(set)
    for field in plan.fields:
        if not PROJECT_NAME_PATTERN.search(str(field.label or "")):
            continue
        if not isinstance(field.value, str) or not field.value.strip():
            continue
        if field.record_id:
            bound[field.record_id].add(_norm(field.value))
        else:
            available[_norm(field.value)] += 1
    title_counts = Counter(_norm(record["title"]) for record in records)
    uncovered = []
    legacy_exclusions = {_norm(title) for title in (plan.metadata.get("project_exclusions") or [])}
    for record in records:
        title = str(record["title"]).strip()
        normalized = _norm(title)
        if record.get("id") in scoped_ids:
            continue
        if title_counts[normalized] == 1 and normalized in legacy_exclusions:
            continue
        if record.get("id") and normalized in bound.get(record["id"], set()):
            continue
        if title_counts[normalized] == 1 and available[normalized]:
            available[normalized] -= 1
        else:
            uncovered.append(title)
    return {
        "canonical_projects": canonical,
        "structured_projects": structured,
        "explicit_exclusions": exclusions,
        "uncovered_projects": uncovered,
        "canonical_count": len(canonical),
        "structured_count": len(structured),
        "resume_parse_status": parse_status,
        "status": "REVIEW_REQUIRED" if uncovered or parse_status == "UNPARSED_SECTION"
                  else "COVERED_OR_EXPLICITLY_EXCLUDED",
        "rules": {
            "attachment_is_not_substitute_for_structured_project_fields": True,
            "resume_parser_output_requires_section_by_section_audit": True,
            "silent_project_omission_forbidden": True,
            "unknown_facts_or_dates_must_not_be_invented": True,
        },
    }


def build_final_review(
    profile: dict[str, Any],
    plan: ApplicationPlan,
    final_control: str,
) -> dict[str, Any]:
    return {
        "human_review_required": True,
        "final_click_actor": "user",
        "final_submit_control": final_control,
        "exact_target_review_required": True,
        "unresolved_field_count": len(plan.unresolved_fields),
        "attachment_basenames": {
            key: Path(value).name for key, value in plan.attachments.items()
        },
        "project_coverage": project_coverage_review(profile, plan),
        "checklist": [
            "verify exact company / role / position identifier / location",
            "audit parser-derived resume sections; remove misclassified work, project, education or skill rows",
            "compare canonical project inventory with structured project rows and record every exclusion explicitly",
            "verify education, dates, ranking, attachments and site-specific answers",
            "review any site-specific representation compromise before submission",
            "user performs the final submit/update click",
        ],
    }
