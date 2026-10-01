"""Isolated interpreter entry for every owned consumer CLI subprocess."""

from __future__ import annotations

import sys
from pathlib import Path


# -I excludes inherited PYTHONHOME/PYTHONPATH, user site and working-directory
# imports. Insert only the explicit owned source after interpreter startup.
CLI_ENTRY_SCRIPT = (
    "import runpy,sys;"
    "sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('executor.autonomy.cli',run_name='__main__',alter_sys=True)"
)


def isolated_cli_command(*args: str, python: str | Path | None = None,
                         source: str | Path | None = None) -> list[str]:
    owned_source = (Path(source) if source is not None
                    else Path(__file__).resolve().parents[2]).resolve()
    return [str(python or sys.executable), "-I", "-B", "-c", CLI_ENTRY_SCRIPT,
            str(owned_source), *map(str, args)]
