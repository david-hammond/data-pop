#!/bin/bash
# Runs the loader repeatedly until all indicators in load_queue are done.
# Useful for the initial full load if you want to break it across sessions.
set -e
cd "$(dirname "$0")"

while true; do
    .venv/bin/python 02_load_unpop.py

    remaining=$(psql -d db-pop -t -c "SELECT COUNT(*) FROM load_queue WHERE tried = 0;" | tr -d ' ')
    if [ "$remaining" -eq 0 ]; then
        echo "$(date) all indicators loaded"
        break
    fi

    echo "$(date) $remaining indicator(s) still pending — restarting"
done
