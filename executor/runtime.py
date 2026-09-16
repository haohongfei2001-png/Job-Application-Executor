from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
from .state import RuntimeState
ROOT=Path.home()/"Job-Application-Executor"; RUNTIME=ROOT/"config/runtime.json"
def load_runtime(): return RuntimeState.model_validate_json(RUNTIME.read_text(encoding="utf-8")) if RUNTIME.exists() else RuntimeState()
def save_runtime(state):
    state.last_checkpoint_at=datetime.now(timezone.utc).isoformat(); RUNTIME.write_text(state.model_dump_json(indent=2),encoding="utf-8")
