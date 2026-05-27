#!/usr/bin/env bash
# bootstrap.sh — Thin wrapper that delegates to bootstrap.py.
#
# This script exists for backward compatibility. The real implementation
# lives in scripts/bootstrap.py and works natively on Windows, Mac, and Linux.
#
# Usage (either works):
#   scripts/bootstrap.sh <name> "<description>" --stack python|node|none
#   python scripts/bootstrap.py <name> "<description>" --stack python|node|none
#
# For the full help, run: python scripts/bootstrap.py --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve Python interpreter
PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)"
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: python is required. Install Python 3.11+ and try again." >&2
  exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/bootstrap.py" "$@"
