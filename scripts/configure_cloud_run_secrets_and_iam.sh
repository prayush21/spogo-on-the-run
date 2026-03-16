#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="${1:-}"
PROJECT_ID="${2:-}"
REGION="${3:-}"
GOOGLE_API_KEY_FILE="${4:-}"
SPOGO_AUTH_FILE="${5:-}"
GOOGLE_API_KEY_SECRET="${6:-spogo-google-api-key}"
SPOGO_AUTH_SECRET="${7:-spogo-spotify-auth}"
SERVICE_ACCOUNT_OVERRIDE="${8:-}"

if [[ -z "$SERVICE_NAME" || -z "$PROJECT_ID" || -z "$REGION" || -z "$GOOGLE_API_KEY_FILE" || -z "$SPOGO_AUTH_FILE" ]]; then
  cat <<'USAGE'
Usage: bash scripts/configure_cloud_run_secrets_and_iam.sh <service_name> <project_id> <region> <google_api_key_file> <spogo_auth_file> [google_api_key_secret_name] [spogo_auth_secret_name] [service_account_email]

Step 3 setup for Cloud Run mobile voice demo:
  1) Creates or reuses Secret Manager secrets.
  2) Adds fresh secret versions from local files.
  3) Grants Cloud Run runtime service account access to these secrets.
  4) Grants roles/aiplatform.user to the runtime service account (Vertex model access).
  5) Binds secrets to Cloud Run runtime env vars:
       GOOGLE_API_KEY
       GEMINI_API_KEY
       SPOGO_AUTH_BLOB

Required inputs:
  <google_api_key_file>  Plain text file containing only the API key.
  <spogo_auth_file>      File containing exported spogo auth material.

Defaults:
  google_api_key_secret_name = spogo-google-api-key
  spogo_auth_secret_name     = spogo-spotify-auth

Service account resolution:
  - Uses Cloud Run service's configured service account when present.
  - Falls back to PROJECT_NUMBER-compute@developer.gserviceaccount.com.
USAGE
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "FAIL: gcloud CLI is required but was not found in PATH."
  exit 1
fi

if [[ ! -f "$GOOGLE_API_KEY_FILE" ]]; then
  echo "FAIL: GOOGLE API key file '$GOOGLE_API_KEY_FILE' does not exist."
  exit 1
fi

if [[ ! -f "$SPOGO_AUTH_FILE" ]]; then
  echo "FAIL: spogo auth file '$SPOGO_AUTH_FILE' does not exist."
  exit 1
fi

ensure_secret() {
  local secret_name="$1"

  if gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "Secret '$secret_name' already exists."
  else
    echo "Creating secret '$secret_name'..."
    gcloud secrets create "$secret_name" \
      --project="$PROJECT_ID" \
      --replication-policy="automatic" >/dev/null
  fi
}

add_secret_version_from_file() {
  local secret_name="$1"
  local source_file="$2"

  echo "Adding new secret version for '$secret_name' from '$source_file'..."
  gcloud secrets versions add "$secret_name" \
    --project="$PROJECT_ID" \
    --data-file="$source_file" >/dev/null
}

grant_secret_access() {
  local secret_name="$1"
  local service_account="$2"

  echo "Granting secret accessor on '$secret_name' to '$service_account'..."
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:$service_account" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
}

service_exists() {
  gcloud run services describe "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" >/dev/null 2>&1
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

TRIMMED_GOOGLE_API_KEY_FILE="$TMP_DIR/google_api_key.txt"
TRIMMED_GOOGLE_API_KEY="$(tr -d '\r\n' < "$GOOGLE_API_KEY_FILE")"
if [[ -z "$TRIMMED_GOOGLE_API_KEY" ]]; then
  echo "FAIL: GOOGLE API key file '$GOOGLE_API_KEY_FILE' is empty after trimming whitespace."
  exit 1
fi
printf "%s" "$TRIMMED_GOOGLE_API_KEY" > "$TRIMMED_GOOGLE_API_KEY_FILE"

if [[ -n "$SERVICE_ACCOUNT_OVERRIDE" ]]; then
  SERVICE_ACCOUNT="$SERVICE_ACCOUNT_OVERRIDE"
else
  if service_exists; then
    SERVICE_ACCOUNT="$(gcloud run services describe "$SERVICE_NAME" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format='value(spec.template.spec.serviceAccountName)')"
  else
    SERVICE_ACCOUNT=""
  fi

  if [[ -z "$SERVICE_ACCOUNT" ]]; then
    PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
    SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  fi
fi

echo "Using runtime service account: '$SERVICE_ACCOUNT'"

ensure_secret "$GOOGLE_API_KEY_SECRET"
ensure_secret "$SPOGO_AUTH_SECRET"

add_secret_version_from_file "$GOOGLE_API_KEY_SECRET" "$TRIMMED_GOOGLE_API_KEY_FILE"
add_secret_version_from_file "$SPOGO_AUTH_SECRET" "$SPOGO_AUTH_FILE"

grant_secret_access "$GOOGLE_API_KEY_SECRET" "$SERVICE_ACCOUNT"
grant_secret_access "$SPOGO_AUTH_SECRET" "$SERVICE_ACCOUNT"

echo "Granting Vertex model access role to '$SERVICE_ACCOUNT'..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/aiplatform.user" >/dev/null

if service_exists; then
  echo "Binding secrets to Cloud Run service '$SERVICE_NAME'..."
  gcloud run services update "$SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --update-secrets="GOOGLE_API_KEY=${GOOGLE_API_KEY_SECRET}:latest,GEMINI_API_KEY=${GOOGLE_API_KEY_SECRET}:latest,SPOGO_AUTH_BLOB=${SPOGO_AUTH_SECRET}:latest" >/dev/null
  SERVICE_BINDING_DONE="yes"
else
  SERVICE_BINDING_DONE="no"
  echo "Cloud Run service '$SERVICE_NAME' was not found in region '$REGION'."
  echo "Secrets and IAM are configured, but runtime secret env binding was skipped."
fi

echo ""
echo "Step 3 complete. Secret Manager + IAM bindings are configured."
echo ""
echo "Verify runtime service account and Vertex role:"
echo "gcloud run services describe '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --format='value(spec.template.spec.serviceAccountName)'"
echo "gcloud projects get-iam-policy '$PROJECT_ID' --flatten='bindings[].members' --filter='bindings.members:serviceAccount:$SERVICE_ACCOUNT AND bindings.role:roles/aiplatform.user' --format='table(bindings.role,bindings.members)'"

if [[ "$SERVICE_BINDING_DONE" == "yes" ]]; then
  echo ""
  echo "Verify secret env bindings:"
  echo "gcloud run services describe '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --format='flattened(spec.template.spec.containers[0].env)' | grep -E 'GOOGLE_API_KEY|GEMINI_API_KEY|SPOGO_AUTH_BLOB'"
else
  echo ""
  echo "After the Cloud Run service exists, bind runtime secret env vars with:"
  echo "gcloud run services update '$SERVICE_NAME' --project='$PROJECT_ID' --region='$REGION' --update-secrets='GOOGLE_API_KEY=${GOOGLE_API_KEY_SECRET}:latest,GEMINI_API_KEY=${GOOGLE_API_KEY_SECRET}:latest,SPOGO_AUTH_BLOB=${SPOGO_AUTH_SECRET}:latest'"
fi
