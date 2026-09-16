#!/bin/zsh
set -e
cd "$HOME/Job-Application-Executor"
exec .venv/bin/python -m executor.cli review
