#!/bin/zsh
set -e
ROOT="$HOME/Job-Application-Executor"
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m executor.app_cli "$@"
