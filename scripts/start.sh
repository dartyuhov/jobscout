#!/bin/bash
set -eu
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${PYTHON:-python3.11}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  printf '%s\n' 'Python 3.11 is required. Install it from python.org or your user-managed runtime manager, then set PYTHON=/absolute/path/to/python3.11. See docs/native-setup.md.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
exec "$PYTHON" "$SCRIPT_DIR/native.py" start "$@"
