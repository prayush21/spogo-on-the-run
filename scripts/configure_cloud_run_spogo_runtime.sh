#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="${1:-}"
PROJECT_ID="${2:-}"
REGION="${3:-}"
SPOGO_VERSION="${4:-0.2.0}"
RUNTIME_DIR="${5:-/tmp/spogo-runtime}"
REQUIRE_DEVICE_COOKIE="${6:-FALSE}"

if [[ -z "$SERVICE_NAME" || -z "$PROJECT_ID" || -z "$REGION" ]]; then
  cat <<'USAGE'
Usage: bash scripts/configure_cloud_run_spogo_runtime.sh <service_name> <project_id> <region> [spogo_version] [runtime_dir] [require_device_cookie]

Step 4 setup for Cloud Run mobile voice demo:
  1) Enables agent-managed spogo auto-install in runtime when missing.
  2) Enables startup auth bootstrap from SPOGO_AUTH_BLOB secret env.
  3) Enables startup auth verification before playback tools run.

Runtime env vars applied:
  SPOGO_AUTO_INSTALL=TRUE
  SPOGO_VERSION=<spogo_version>
  SPOGO_RUNTIME_DIR=<runtime_dir>
  SPOGO_REQUIRE_AUTH_BLOB=TRUE
  SPOGO_VERIFY_AUTH_ON_STARTUP=TRUE
  SPOGO_REQUIRE_DEVICE_COOKIE=<TRUE|FALSE>
  SPOGO_ENGINE=connect
  SPOGO_PROFILE=default

Defaults:
  spogo_version          = 0.2.0
  runtime_dir            = /tmp/spogo-runtime
  require_device_cookie  = FALSE
USAGE
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "FAIL: gcloud CLI is required but was not found in PATH."
  exit 1
fi

normalized_require_device_cookie="$(echo "$REQUIRE_DEVICE_COOKIE" | tr '[:lower:]' '[:upper:]')"
if [[ "$normalized_require_device_cookie" != "TRUE" && "$normalized_require_device_cookie" != "FALSE" ]]; then
  echo "FAIL: require_device_cookie must be TRUE or FALSE."
  exit 1
fi

service_exists() {
  gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" >/dev/null 2>&1
}

if service_exists; then
  echo "Updating Cloud Run service '$SERVICE_NAME' in project '$PROJECT_ID' region '$REGION' for spogo runtime bootstrap..."
  gcloud run services update "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --set-env-vars="SPOGO_AUTO_INSTALL=TRUE,SPOGO_VERSION=$SPOGO_VERSION,SPOGO_RUNTIME_DIR=$RUNTIME_DIR,SPOGO_REQUIRE_AUTH_BLOB=TRUE,SPOGO_VERIFY_AUTH_ON_STARTUP=TRUE,SPOGO_REQUIRE_DEVICE_COOKIE=$normalized_require_device_cookie,SPOGO_ENGINE=connect,SPOGO_PROFILE=default" >/dev/null
  STEP4_APPLIED="yes"
else
  STEP4_APPLIED="no"
  echo "Cloud Run service '$SERVICE_NAME' was not found in region '$REGION'."
  echo "Step 4 env values were not applied yet."
fi

echo ""
if [[ "$STEP4_APPLIED" == "yes" ]]; then
  echo "Step 4 runtime env applied."
  echo ""
  echo "Verify env vars on the service:"
  echo "gcloud run services describe '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --format='flattened(spec.template.spec.containers[0].env)' | grep -E 'SPOGO_AUTO_INSTALL|SPOGO_VERSION|SPOGO_RUNTIME_DIR|SPOGO_REQUIRE_AUTH_BLOB|SPOGO_VERIFY_AUTH_ON_STARTUP|SPOGO_REQUIRE_DEVICE_COOKIE|SPOGO_ENGINE|SPOGO_PROFILE'"
  echo ""
  echo "Check startup/bootstrap logs:"
  echo "gcloud run services logs read '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --limit=200 | grep -Ei 'spogo|bootstrap|auth'"
else
  echo "Run this command after the service exists:"
  echo "gcloud run services update '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --set-env-vars='SPOGO_AUTO_INSTALL=TRUE,SPOGO_VERSION=$SPOGO_VERSION,SPOGO_RUNTIME_DIR=$RUNTIME_DIR,SPOGO_REQUIRE_AUTH_BLOB=TRUE,SPOGO_VERIFY_AUTH_ON_STARTUP=TRUE,SPOGO_REQUIRE_DEVICE_COOKIE=$normalized_require_device_cookie,SPOGO_ENGINE=connect,SPOGO_PROFILE=default'"
fi
