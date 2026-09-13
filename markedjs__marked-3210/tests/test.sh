#!/bin/bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt
exec /usr/bin/python3 -I -B "$HERE/sweb_grade.py" --config "$HERE/config.json" "$@"
