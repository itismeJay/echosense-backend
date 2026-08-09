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
pytest_bin="$repository_root/$virtualenv_dir/bin/pytest"
if [[ ! -x "$python_bin" || ! -x "$alembic_bin" || ! -x "$pytest_bin" ]]; then
  echo "Missing $virtualenv_dir development dependencies for Python 3.11." >&2
  exit 1
fi

"$python_bin" scripts/local_safety.py
test_migration_url="${ECHOSENSE_TEST_DATABASE_URL/postgresql+asyncpg:/postgresql:}"
ALEMBIC_DATABASE_URL="$test_migration_url" "$alembic_bin" upgrade head

# Application-default tests must not inherit the local runtime's CORS, notification,
# or synthetic-ingest choices. tests/conftest.py sets its own explicit test policy.
unset APP_NAME
unset ECHOSENSE_CONTROLLED_TEST_MODE
unset ECHOSENSE_CONTROLLED_TEST_USER_ID
unset ECHOSENSE_CORS_ORIGINS
unset EXPO_ACCESS_TOKEN
ECHOSENSE_TEST_DATABASE_URL="$ECHOSENSE_TEST_DATABASE_URL" "$pytest_bin" "$@"
