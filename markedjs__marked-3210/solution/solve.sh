#!/bin/bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
git -C /testbed apply --check "$HERE/gold.patch"
git -C /testbed apply "$HERE/gold.patch"
