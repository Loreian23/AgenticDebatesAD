#!/bin/bash
# Sync templates/ → static/templates/ (Jinja2 serves from static/templates/)
set -euo pipefail
cd "$(dirname "$0")/.."
for f in templates/*.html; do
    echo "Syncing $f → static/$f"
    cp "$f" "static/$f"
done
echo "Done."
