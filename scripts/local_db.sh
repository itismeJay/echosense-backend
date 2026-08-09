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
alembic_bin="$repository_root/$virtualenv_dir/bin/alembic"
if [[ ! -x "$python_bin" || ! -x "$alembic_bin" ]]; then
  echo "Missing $virtualenv_dir dependencies. Install requirements-dev.txt in Python 3.11." >&2
  exit 1
fi
"$python_bin" scripts/local_safety.py

case "${1:-}" in
  up)
    docker compose --env-file "$environment_file" up -d --wait db
    "$alembic_bin" upgrade head
    migration="$($alembic_bin current | awk 'NR == 1 {print $1}')"
    echo "EchoSense local database"
    echo "Database: healthy"
    echo "Migration: $migration"
    ;;
  status)
    docker compose --env-file "$environment_file" ps db
    "$alembic_bin" current
    ;;
  down)
    docker compose --env-file "$environment_file" down
    echo "Local containers stopped; PostgreSQL data volume preserved"
    ;;
  *)
    echo "Usage: ./scripts/local_db.sh {up|status|down}" >&2
    exit 2
    ;;
esac
