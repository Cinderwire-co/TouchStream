#!/bin/bash
# Quick checks before committing hub changes: Python compiles, unit tests pass,
# and every dashboard module parses (catches quoting slips the browser would hit).
set -e
cd "$(dirname "$0")/.."
python3 -m compileall -q touchstream_hub
python3 -m unittest discover -s tests -q
for f in touchstream_hub/static/app.js touchstream_hub/static/js/*.js; do
    node --input-type=module --check < "$f" || { echo "Syntax error in $f"; exit 1; }
done
echo "Hub checks passed"
