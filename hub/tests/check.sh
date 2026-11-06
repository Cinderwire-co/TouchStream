#!/bin/bash
# Quick checks before committing hub changes: Python compiles, unit tests pass,
# and every dashboard module parses (catches quoting slips the browser would hit).
set -e
cd "$(dirname "$0")/.."
PY=python3
[ -x ../.dev/venv/bin/python ] && PY=../.dev/venv/bin/python   # tools/dev's, with the hub's packages
$PY -m compileall -q touchstream_hub
$PY -m unittest discover -s tests -q
for f in touchstream_hub/static/app.js touchstream_hub/static/js/*.js; do
    node --input-type=module --check < "$f" || { echo "Syntax error in $f"; exit 1; }
done
echo "Hub checks passed"
