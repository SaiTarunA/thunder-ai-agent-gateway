#!/usr/bin/env bash
# Quarterly infrastructure report helper.

set -euo pipefail

is_healthy() {
  local utilization="$1"
  awk -v u="$utilization" 'BEGIN { exit !(u < 0.85) }'
}

if is_healthy "0.72"; then
  echo "Region is healthy"
else
  echo "Region is at capacity"
fi
