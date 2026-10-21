#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
environment_file="$repository_root/.env.local"

if [[ ! -f "$environment_file" ]]; then
  echo "Missing .env.local. Run: python3.11 scripts/init_local_env.py" >&2
  exit 1
fi

cd "$repository_root"
set -a
source "$environment_file"
set +a
virtualenv_dir="${ECHOSENSE_VENV_DIR:-.venv}"
python_bin="$repository_root/$virtualenv_dir/bin/python"
uvicorn_bin="$repository_root/$virtualenv_dir/bin/uvicorn"
if [[ ! -x "$python_bin" || ! -x "$uvicorn_bin" ]]; then
  echo "Missing $virtualenv_dir dependencies. Install requirements-dev.txt in Python 3.11." >&2
  exit 1
fi
"$python_bin" scripts/local_safety.py

lan_ip="$("$python_bin" scripts/lan_ip.py || true)"
if [[ -n "$lan_ip" ]]; then
  export ECHOSENSE_CORS_ORIGINS="${ECHOSENSE_CORS_ORIGINS},http://${lan_ip}:3000"
  lan_backend="http://${lan_ip}:8000"
else
  lan_backend="unavailable (see docs/local_development.md)"
fi

migration="$("$repository_root/$virtualenv_dir/bin/alembic" current | awk 'NR == 1 {print $1}')"
if [[ "$migration" != "20261021_0010" ]]; then
  echo "Local database migration is $migration; expected 20261021_0010." >&2
  echo "Run: ./scripts/local_db.sh up" >&2
  exit 1
fi

echo "EchoSense local development"
echo "Database: healthy"
echo "Migration: $migration"
echo "Backend: http://localhost:8000"
echo "LAN backend: $lan_backend"
echo "Health: curl http://127.0.0.1:8000/health after startup"

exec "$uvicorn_bin" app.main:app --host 0.0.0.0 --port 8000 --reload
