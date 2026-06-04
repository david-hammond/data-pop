#!/bin/bash
# Incremental update: resets tried = 0 for all indicators, then re-runs the
# loader. The UN WPP is published every two years; schedule accordingly.
#
# Schedule via cron, e.g. (annual, 3am on Jan 1):
#   0 3 1 1 * /home/elliptica/projects/data-pop/update.sh >> /home/elliptica/projects/data-pop/cron.log 2>&1
set -e
cd "$(dirname "$0")"

echo "$(date) resetting load_queue for full refresh..."
psql -d db-pop -c "UPDATE load_queue SET tried = 0, last_error = NULL;"

pending=$(psql -d db-pop -t -c "SELECT COUNT(*) FROM load_queue WHERE tried = 0;" | tr -d ' ')
echo "$(date) $pending indicator(s) to refresh"
.venv/bin/python 02_load_unpop.py
