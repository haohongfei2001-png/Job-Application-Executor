#!/bin/zsh
set -e
ROOT="$HOME/Job-Application-Executor"
if [[ $# -ne 3 ]]; then echo 'Usage: run.sh <job-url> <resume-path> <profile-json-path>'; exit 2; fi
cd "$ROOT"
exec .venv/bin/python -m executor.cli run --job-url "$1" --resume "$2" --profile "$3"
