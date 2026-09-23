from __future__ import annotations

import re
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
        if title and title not in titles:
            titles.append(title)
    return titles


def structured_project_names(fields: list[FieldResolution]) -> list[str]:
    names: list[str] = []
    for item in fields:
        label = str(item.label or "")
        if not PROJECT_NAME_PATTERN.search(label):
            continue
        value = item.value
        if isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())
    return names


def _covered(title: str, names: list[str]) -> bool:
    nt = _norm(title)
    if not nt:
        return False
    for name in names:
        nn = _norm(name)
        if nn and nt == nn:
            return True
    return False


def project_coverage_review(
    profile: dict[str, Any],
    plan: ApplicationPlan,
) -> dict[str, Any]:
    records = [item for item in ((profile.get("collections") or {}).get("projects") or [])
               if isinstance(item, dict) and str(item.get("title") or "").strip()]
    canonical = canonical_project_titles(profile)
    structured = structured_project_names(plan.fields)
    exclusions = [
        str(x).strip()
        for x in (plan.metadata.get("project_exclusions") or [])
        if str(x).strip()
    ]
    scoped = ((profile.get("collections") or {}).get("fact_exclusions") or {}).get(
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

    def fully_excluded_duplicate(title: str) -> bool:
        matches = [record for record in records if _norm(record["title"]) == _norm(title)]
        return len(matches) > 1 and all(record.get("id") in scoped_ids for record in matches)

    uncovered = [
        title for title in canonical
        if (
            (sum(_norm(record["title"]) == _norm(title) for record in records) > 1
             and not fully_excluded_duplicate(title))
            or (not _covered(title, structured) and not _covered(title, exclusions))
        )
    ]
    return {
        "canonical_projects": canonical,
        "structured_projects": structured,
        "explicit_exclusions": exclusions,
        "uncovered_projects": uncovered,
        "canonical_count": len(canonical),
        "structured_count": len(structured),
        "status": "REVIEW_REQUIRED" if uncovered else "COVERED_OR_EXPLICITLY_EXCLUDED",
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
