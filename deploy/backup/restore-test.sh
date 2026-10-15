#!/bin/sh
# Restore a dump into a throw-away database and sanity-check it. Usage: restore-test.sh FILE
set -eu
file="${1:?usage: restore-test.sh DUMP_FILE}"
scratch="${PGDATABASE}_restore_test"

psql -v ON_ERROR_STOP=1 -d postgres -qc "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)"
psql -v ON_ERROR_STOP=1 -d postgres -qc "CREATE DATABASE \"$scratch\""
trap 'psql -d postgres -qc "DROP DATABASE IF EXISTS \"$scratch\" WITH (FORCE)" >/dev/null 2>&1 || true' EXIT

pg_restore --no-owner --exit-on-error --dbname="$scratch" "$file"

check() {  # the table must exist in the restored copy (psql fails otherwise); log its size
  restored=$(psql -v ON_ERROR_STOP=1 -d "$scratch" -tAc "SELECT count(*) FROM $1")
  echo "restore-test: $1 = $restored rows"
}
version=$(psql -d "$scratch" -tAc "SELECT version_num FROM alembic_version")
[ -n "$version" ] || { echo "restore-test: FAILED (no alembic_version)"; exit 1; }
for t in admin_user access_link sd_client sd_product shop_order shop_order_line audit_log; do
  check "$t"
done
echo "restore-test: OK ($file, schema $version)"
