#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment_file="$repository_root/.env.integration"

if [[ ! -f "$environment_file" ]]; then
  echo "Missing .env.integration. Run: .venv-local/bin/python scripts/init_integration_env.py" >&2
  exit 1
fi

cd "$repository_root"
set -a
source "$environment_file"
set +a
unset ALEMBIC_DATABASE_URL
unset ECHOSENSE_TEST_DATABASE_URL

virtualenv_dir="${ECHOSENSE_VENV_DIR:-.venv}"
python_bin="$repository_root/$virtualenv_dir/bin/python"
uvicorn_bin="$repository_root/$virtualenv_dir/bin/uvicorn"
if [[ ! -x "$python_bin" || ! -x "$uvicorn_bin" ]]; then
  echo "Missing $virtualenv_dir dependencies. Install requirements-dev.txt in Python 3.11." >&2
  exit 1
fi

"$python_bin" scripts/integration_safety.py
"$python_bin" scripts/check_integration_database.py

lan_ip="$("$python_bin" scripts/lan_ip.py || true)"
if [[ -n "$lan_ip" ]]; then
  backend_url="http://${lan_ip}:8000"
else
  backend_url="http://<MAC-LAN-IP>:8000"
fi

echo "EchoSense Local Integration"
echo "Backend: $backend_url"
echo "Database: Neon"
echo "Environment: integration"

exec "$uvicorn_bin" app.main:app --host 0.0.0.0 --port 8000 --reload
