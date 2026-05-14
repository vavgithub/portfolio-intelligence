#!/usr/bin/env bash
set -euo pipefail

# Example Cloud Run deploy — set IMAGE, SERVICE, REGION, PROJECT before running.
# IMPORTANT: Keep max-instances=1 until results_store is replaced with Redis
# Scaling beyond 1 instance will break job polling consistency
gcloud run deploy "${CLOUD_RUN_SERVICE:-portfolio-intelligence}" \
  --image="${CONTAINER_IMAGE:?Set CONTAINER_IMAGE}" \
  --region="${GCP_REGION:-us-central1}" \
  --project="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}" \
  --set-env-vars "MAX_PROJECTS_TO_ANALYZE=3" \
  --max-instances 1 \
  "$@"
