from __future__ import annotations

import argparse
import json
from pathlib import Path

from .application import ApplicationExecutor
from .audit import AuditStore
from .evidence import ProfileBuilder, set_user_confirmed_field, write_profile
from .models import ApplicationStage, utc_now
from .settings import load_settings
from .recovery import recover_execution


ROOT = Path.home() / "Job-Application-Executor"
DEFAULT_PROFILE = ROOT / "config" / "applicant-profile.json"


def _profile_path(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
    elif DEFAULT_PROFILE.is_file():
        path = DEFAULT_PROFILE
    else:
        configured = load_settings().get("profile_path")
        path = Path(configured).expanduser().resolve() if configured else DEFAULT_PROFILE
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _unresolved_view(item) -> dict:
    return {
        "field_id": item.field_id,
        "selector": item.selector,
        "label": item.label,
        "canonical_key": item.canonical_key,
        "status": item.status,
        "required": item.required,
        "reason": item.reason,
    }


def _build_profile(args) -> int:
    builder = ProfileBuilder().import_max_docx(args.max_docx)
    if args.legacy_profile:
        builder.import_legacy_profile(args.legacy_profile)
    if args.resume:
        builder.import_resume(args.resume)
    if args.photo:
        builder.add_asset("photo", args.photo, "photo")
    if args.history_json:
        if not args.history_scope:
            raise RuntimeError("--history-scope is required with --history-json")
        builder.import_historical_json(
            args.history_scope,
            args.history_json,
            reconfirm_keys=args.reconfirm_key or [],
        )
    builder.import_user_overrides(args.output)
    profile = builder.build()
    output = write_profile(profile, args.output)
    print(json.dumps({
        "profile": str(output),
        "field_count": len(profile.fields),
        "asset_count": len(profile.assets),
        "conflict_count": len(profile.collections.get("conflicts", [])),
    }, ensure_ascii=False, indent=2))
    return 0

def _execute(args) -> int:
    settings = load_settings()
    executor = ApplicationExecutor(
        args.url,
        _profile_path(args.profile),
        settings,
        submit_authorized=args.submit_authorized,
    )
    plan = executor.run(max_pages=args.max_pages)
    unresolved = [_unresolved_view(item) for item in plan.unresolved_fields]
    print(json.dumps({
        "execution_id": plan.execution_id,
        "site_id": plan.site_id,
        "stage": plan.stage,
        "submit_authorized": plan.submit_authorized,
        "audit_dir": str(executor.audit.root),
        "unresolved_fields": unresolved,
        "verification": plan.metadata.get("verification"),
        "final_review": plan.metadata.get("final_review"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0

def _recover(args) -> int:
    plan = recover_execution(
        args.execution_id,
        _profile_path(args.profile),
        load_settings(),
        max_pages=args.max_pages,
    )
    print(json.dumps({
        "execution_id": plan.execution_id,
        "stage": plan.stage,
        "recovered_from_stage": plan.metadata.get("recovered_from_stage"),
        "unresolved_fields": [_unresolved_view(item) for item in plan.unresolved_fields],
        "final_review": plan.metadata.get("final_review"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0


def _parse_value(text: str):
    try:
        return json.loads(text)
    except Exception:
        return text


def _answer(args) -> int:
    store = AuditStore(args.execution_id)
    plan = store.load_plan()
    if plan.stage in {ApplicationStage.SUBMITTED, ApplicationStage.VERIFIED}:
        raise RuntimeError("terminal submitted execution is read-only")

    candidates = list(plan.unresolved_fields)
    if args.selector:
        matches = [item for item in candidates if item.selector == args.selector]
    else:
        matches = [item for item in candidates if item.field_id == args.field_id]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one unresolved field match, found {len(matches)}; use --selector when needed"
        )

    item = matches[0]
    value = _parse_value(args.value_json)
    canonical_key = args.canonical_key or item.canonical_key
    scope = "profile" if args.promote_profile else "execution"
    answer = {
        "field_id": item.field_id,
        "selector": item.selector,
        "label": item.label,
        "canonical_key": canonical_key,
        "value": value,
        "scope": scope,
        "confirmed_at": utc_now(),
    }
    store.add_user_answer(answer)

    if args.promote_profile:
        if not canonical_key:
            raise RuntimeError("--promote-profile requires a canonical key")
        profile_path = _profile_path(args.profile)
        set_user_confirmed_field(
            profile_path,
            canonical_key,
            value,
            note=f"confirmed for execution {args.execution_id}",
        )

    print(json.dumps({
        "execution_id": args.execution_id,
        "field_id": item.field_id,
        "selector": item.selector,
        "canonical_key": canonical_key,
        "scope": scope,
        "answer_saved": True,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="application-executor")
    sub = ap.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile-build")
    profile.add_argument("--max-docx", required=True)
    profile.add_argument("--output", default=str(DEFAULT_PROFILE))
    profile.add_argument("--legacy-profile")
    profile.add_argument("--resume")
    profile.add_argument("--photo")
    profile.add_argument("--history-json")
    profile.add_argument("--history-scope")
    profile.add_argument("--reconfirm-key", action="append")

    execute = sub.add_parser("execute")
    execute.add_argument("--url", required=True)
    execute.add_argument("--profile")
    execute.add_argument("--max-pages", type=int, default=15)
    execute.add_argument(
        "--submit-authorized",
        action="store_true",
        help="legacy target-authorization flag; never authorizes an automated final submit click",
    )

    recover = sub.add_parser("recover")
    recover.add_argument("--execution-id", required=True)
    recover.add_argument("--profile")
    recover.add_argument("--max-pages", type=int, default=15)

    answer = sub.add_parser("answer")
    answer.add_argument("--execution-id", required=True)
    target = answer.add_mutually_exclusive_group(required=True)
    target.add_argument("--field-id")
    target.add_argument("--selector")
    answer.add_argument("--value-json", required=True)
    answer.add_argument("--canonical-key")
    answer.add_argument("--promote-profile", action="store_true")
    answer.add_argument("--profile")

    args = ap.parse_args()
    if args.command == "profile-build":
        return _build_profile(args)
    if args.command == "execute":
        return _execute(args)
    if args.command == "recover":
        return _recover(args)
    if args.command == "answer":
        return _answer(args)
    raise RuntimeError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
