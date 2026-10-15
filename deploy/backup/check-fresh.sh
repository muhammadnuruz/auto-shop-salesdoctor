#!/bin/sh
# Healthy when a verified dump newer than 26 hours exists.
recent=$(find /backups -maxdepth 1 -name '*.dump.verified' -mmin -1560 | head -n 1)
[ -n "$recent" ]
