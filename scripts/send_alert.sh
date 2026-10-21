#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${ECHOSENSE_VENV_DIR:-}" ]]; then
  python_bin="$repository_root/$ECHOSENSE_VENV_DIR/bin/python"
elif [[ -x "$repository_root/.venv-local/bin/python" ]]; then
  python_bin="$repository_root/.venv-local/bin/python"
else
  python_bin="$repository_root/.venv/bin/python"
fi

if [[ ! -x "$python_bin" ]]; then
  echo "Missing Python environment; install requirements-dev.txt first" >&2
  exit 1
fi

exec "$python_bin" "$repository_root/scripts/send_alert.py" "$@"
