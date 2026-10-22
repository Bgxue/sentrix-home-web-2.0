#!/bin/bash
# Backward-compatible entry point for the complete local Mac deployment.
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
export SENTRIX_DEPLOYMENT_PROFILE="${SENTRIX_DEPLOYMENT_PROFILE:-mac}"
exec "$PROJ/scripts/deploy/start_local_models_mac.sh" "$@"
