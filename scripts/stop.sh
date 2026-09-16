#!/bin/zsh
set -e
ROOT="$HOME/Job-Application-Executor"; cd "$ROOT"
if [[ -f run.pid ]] && kill -0 "$(cat run.pid)" 2>/dev/null; then kill "$(cat run.pid)"; echo "stopped $(cat run.pid)"; fi
rm -f run.pid
