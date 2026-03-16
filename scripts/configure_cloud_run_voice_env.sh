#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="${1:-}"
PROJECT_ID="${2:-}"
REGION="${3:-}"
MODEL_NAME="${4:-gemini-live-2.5-flash-native-audio}"

if [[ -z "$SERVICE_NAME" || -z "$PROJECT_ID" || -z "$REGION" ]]; then
  cat <<'USAGE'
Usage: bash scripts/configure_cloud_run_voice_env.sh <service_name> <project_id> <region> [model_name]

Locks Cloud Run runtime env vars for Vertex Live voice mode:
  GOOGLE_GENAI_USE_VERTEXAI=TRUE
  GOOGLE_CLOUD_PROJECT=<project_id>
  GOOGLE_CLOUD_LOCATION=<region>
  AGENT_MODEL=<model_name>

Default model_name:
  gemini-live-2.5-flash-native-audio
USAGE
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "FAIL: gcloud CLI is required but was not found in PATH."
  exit 1
fi

echo "Updating Cloud Run service '$SERVICE_NAME' in project '$PROJECT_ID' region '$REGION'..."
gcloud run services update "$SERVICE_NAME" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,AGENT_MODEL=$MODEL_NAME"

echo ""
echo "Runtime env lock applied. Verify these keys are present:"
echo "gcloud run services describe '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --format='flattened(spec.template.spec.containers[0].env)' | grep -E 'GOOGLE_GENAI_USE_VERTEXAI|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION|AGENT_MODEL'"
