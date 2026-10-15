#!/bin/sh
# One backup: custom-format pg_dump -> verify by restoring into a scratch DB -> prune old dumps.
# Env: PGHOST PGUSER PGPASSWORD PGDATABASE, BACKUP_RETENTION_DAYS (default 14), /backups volume.
set -eu
umask 077
stamp=$(date +%Y%m%d-%H%M%S)
tmp="/backups/.${PGDATABASE}-${stamp}.dump.partial"
out="/backups/${PGDATABASE}-${stamp}.dump"

pg_dump --format=custom --compress=6 --no-owner --file="$tmp"
mv "$tmp" "$out"
echo "backup: wrote $out ($(du -h "$out" | cut -f1))"

/bin/sh /scripts/restore-test.sh "$out"
touch "$out.verified"

# leftovers of interrupted dumps
find /backups -maxdepth 1 -name ".*.dump.partial" -mmin +120 -exec rm -f {} \;
find /backups -maxdepth 1 -name "${PGDATABASE}-*.dump" -mtime "+${BACKUP_RETENTION_DAYS:-14}" |
  while read -r old; do
    rm -f "$old" "$old.verified"
    echo "backup: pruned $old"
  done
