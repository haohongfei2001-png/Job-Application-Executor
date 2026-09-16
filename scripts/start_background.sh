#!/bin/zsh
set -e
ROOT="$HOME/Job-Application-Executor"
if [[ $# -lt 1 ]]; then echo 'Usage: start_background.sh <job-url> [resume-path] [profile-path]'; exit 2; fi
cd "$ROOT"
if [[ -f run.pid ]] && kill -0 "$(cat run.pid)" 2>/dev/null; then echo "already running pid=$(cat run.pid)"; exit 1; fi
JOB="$1"; shift
ARGS=(run --job-url "$JOB")
if [[ $# -ge 1 ]]; then ARGS+=(--resume "$1"); shift; fi
if [[ $# -ge 1 ]]; then ARGS+=(--profile "$1"); shift; fi
nohup .venv/bin/python -m executor.cli "${ARGS[@]}" >> logs/executor.log 2>&1 &
echo $! > run.pid
echo "started pid=$!"
