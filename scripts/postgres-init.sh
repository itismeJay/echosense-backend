#!/usr/bin/env sh
set -eu

test_database="${ECHOSENSE_POSTGRES_TEST_DB:-echosense_test}"

case "$test_database" in
  *_test) ;;
  *)
    echo "Refusing to create a test database whose name does not end in _test." >&2
    exit 1
    ;;
esac

if ! psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only \
  --command "SELECT 1 FROM pg_database WHERE datname = '$test_database'" | grep -q 1; then
  createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" "$test_database"
fi
