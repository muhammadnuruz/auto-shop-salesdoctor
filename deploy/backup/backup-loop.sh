#!/bin/sh
# Daily at BACKUP_HOUR (TZ of the container); a failed backup is retried in 30 minutes.
set -u
hour="${BACKUP_HOUR:-2}"
echo "backup: daily at ${hour}:00 ($(date +%Z)), keeping ${BACKUP_RETENTION_DAYS:-14} days"
last=""
while :; do
  today=$(date +%Y-%m-%d)
  if [ "$(date +%H)" -ge "$hour" ] && [ "$last" != "$today" ]; then
    if /bin/sh /scripts/backup.sh; then
      last="$today"
    else
      echo "backup: FAILED, retrying in 30 min" >&2
      sleep 1800
      continue
    fi
  fi
  sleep 300
done
