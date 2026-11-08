#!/bin/sh
# macOS / Linux counterpart of start-local.bat: starts the API, the frontend and the workers in
# this terminal (scripts/start_local.py). Run it from anywhere: ./start-local.sh [--check] ...
set -eu
cd "$(dirname "$0")"

# Use a project environment, never a Python installation from PATH.
LOCAL_PYTHON=""
for venv in .venv .venv313; do
    candidate="$venv/bin/python"
    if [ -x "$candidate" ] &&
        "$candidate" -c "import sys, uvicorn, app.main; assert sys.version_info[:2] == (3, 13)" >/dev/null 2>&1; then
        LOCAL_PYTHON="$candidate"
        break
    fi
done
if [ -z "$LOCAL_PYTHON" ]; then
    echo "[ERROR] No working project Python 3.13 environment found." >&2
    echo "Create .venv or .venv313 and install the project dependencies." >&2
    echo "See README.md. No packages or settings were changed." >&2
    exit 1
fi
exec "$LOCAL_PYTHON" scripts/start_local.py "$@"
