from __future__ import annotations

from pathlib import Path

from .application import ApplicationExecutor
from .audit import AuditStore
from .models import ApplicationPlan, ApplicationStage


TERMINAL_STAGES = {ApplicationStage.SUBMITTED, ApplicationStage.VERIFIED}


def recover_execution(execution_id: str, profile_path: str | Path, settings: dict | None = None, *, max_pages: int = 15) -> ApplicationPlan:
    store = AuditStore(execution_id)
    previous = store.load_plan()
    if previous.stage in TERMINAL_STAGES:
        return previous

    runner = ApplicationExecutor(
        previous.target_url,
        profile_path,
        settings,
        execution_id=execution_id,
    )
    runner.plan.metadata["recovered_from_stage"] = str(previous.stage)
    return runner.run(max_pages=max_pages)
