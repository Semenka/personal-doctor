#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Personal Doctor — Google Cloud Deployment
#
# Sets up:
#   1. Artifact Registry repository
#   2. Secret Manager secrets (API keys, SMTP credentials)
#   3. Docker image build & push
#   4. Cloud Run Job (daily health pipeline)
#   5. Cloud Scheduler trigger (07:30 Europe/Paris)
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - Docker installed
#   - A GCP project with billing enabled
#
# Usage:
#   ./deploy-gcp.sh                    # Interactive: prompts for project ID
#   ./deploy-gcp.sh my-gcp-project     # Non-interactive
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

REGION="europe-west1"            # Close to Europe/Paris timezone
REPO_NAME="personal-doctor"
IMAGE_NAME="personal-doctor"
JOB_NAME="personal-doctor-daily"
SCHEDULER_NAME="personal-doctor-trigger"
SERVICE_ACCOUNT_NAME="personal-doctor-sa"
SCHEDULE="30 7 * * *"           # 07:30 daily
TIMEZONE="Europe/Paris"

# ── Colour helpers ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
fail()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }

# ── Resolve GCP project ──
PROJECT_ID="${1:-}"
if [ -z "$PROJECT_ID" ]; then
    CURRENT=$(gcloud config get-value project 2>/dev/null || true)
    if [ -n "$CURRENT" ] && [ "$CURRENT" != "(unset)" ]; then
        echo "Current GCP project: $CURRENT"
        read -rp "Use this project? [Y/n] " yn
        case "$yn" in [nN]*) read -rp "Enter GCP project ID: " PROJECT_ID ;; *) PROJECT_ID="$CURRENT" ;; esac
    else
        read -rp "Enter GCP project ID: " PROJECT_ID
    fi
fi
[ -z "$PROJECT_ID" ] && fail "No project ID provided."
gcloud config set project "$PROJECT_ID"
info "Project: $PROJECT_ID"

# ── Enable required APIs ──
echo ""
echo "Enabling required GCP APIs..."
gcloud services enable \
    artifactregistry.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com \
    --quiet
info "APIs enabled."

# ── Create service account ──
echo ""
SA_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$SA_EMAIL" &>/dev/null; then
    info "Service account exists: $SA_EMAIL"
else
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --display-name="Personal Doctor Pipeline" --quiet
    info "Created service account: $SA_EMAIL"
fi

# Grant roles
for ROLE in roles/run.invoker roles/secretmanager.secretAccessor; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$ROLE" --quiet --condition=None 2>/dev/null || true
done
info "Service account roles configured."

# ── Create secrets ──
echo ""
echo "Setting up Secret Manager secrets..."
echo "(You can skip any secret by pressing Enter to keep the existing value)"

create_or_update_secret() {
    local name="$1" prompt="$2" default="${3:-}"
    if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
        warn "Secret '$name' already exists."
        read -rp "  Update it? [y/N] " yn
        case "$yn" in
            [yY]*)
                read -rsp "  $prompt: " val; echo
                [ -z "$val" ] && { warn "  Skipped."; return; }
                echo -n "$val" | gcloud secrets versions add "$name" --data-file=- --quiet
                info "  Updated secret: $name"
                ;;
            *) info "  Kept existing." ;;
        esac
    else
        read -rsp "  $prompt: " val; echo
        if [ -z "$val" ] && [ -n "$default" ]; then val="$default"; fi
        [ -z "$val" ] && { warn "  Skipped (empty)."; return; }
        echo -n "$val" | gcloud secrets create "$name" --data-file=- \
            --replication-policy="automatic" --quiet
        info "  Created secret: $name"
    fi
}

create_or_update_secret "pd-anthropic-key"    "Anthropic API key (sk-ant-...)"
create_or_update_secret "pd-oura-token"       "Oura Ring access token"
create_or_update_secret "pd-smtp-password"    "Yahoo SMTP app password"
create_or_update_secret "pd-smtp-user"        "SMTP username (e.g. user@yahoo.com)"
create_or_update_secret "pd-email-to"         "Recipient email address"

# ── Artifact Registry ──
echo ""
if gcloud artifacts repositories describe "$REPO_NAME" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    info "Artifact Registry repo exists: $REPO_NAME"
else
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Personal Doctor images" \
        --quiet
    info "Created Artifact Registry repo: $REPO_NAME"
fi

# ── Build & push Docker image ──
echo ""
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"
echo "Building Docker image..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet 2>/dev/null || true

docker build -t "$IMAGE_URI" .
info "Built image: $IMAGE_URI"

echo "Pushing image..."
docker push "$IMAGE_URI"
info "Pushed image: $IMAGE_URI"

# ── Deploy Cloud Run Job ──
echo ""
echo "Deploying Cloud Run Job..."

# Build secrets flag
SECRETS_FLAG=""
SECRETS_FLAG+="ANTHROPIC_API_KEY=pd-anthropic-key:latest,"
SECRETS_FLAG+="OURA_ACCESS_TOKEN=pd-oura-token:latest,"
SECRETS_FLAG+="SMTP_PASSWORD=pd-smtp-password:latest,"
SECRETS_FLAG+="SMTP_USER=pd-smtp-user:latest,"
SECRETS_FLAG+="EMAIL_TO=pd-email-to:latest"

gcloud run jobs create "$JOB_NAME" \
    --image="$IMAGE_URI" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-secrets="$SECRETS_FLAG" \
    --set-env-vars="SMTP_HOST=smtp.yahoo.com,SMTP_PORT=465,HEALTH_TIMEZONE=Europe/Paris,HEALTH_DATA_DIR=/tmp/data" \
    --command="python" \
    --args="-m,app.sync.run_pipeline" \
    --task-timeout=600 \
    --max-retries=1 \
    --memory=512Mi \
    --cpu=1 \
    --quiet 2>/dev/null \
|| gcloud run jobs update "$JOB_NAME" \
    --image="$IMAGE_URI" \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-secrets="$SECRETS_FLAG" \
    --set-env-vars="SMTP_HOST=smtp.yahoo.com,SMTP_PORT=465,HEALTH_TIMEZONE=Europe/Paris,HEALTH_DATA_DIR=/tmp/data" \
    --command="python" \
    --args="-m,app.sync.run_pipeline" \
    --task-timeout=600 \
    --max-retries=1 \
    --memory=512Mi \
    --cpu=1 \
    --quiet

info "Deployed Cloud Run Job: $JOB_NAME"

# ── Cloud Scheduler ──
echo ""
echo "Setting up Cloud Scheduler..."

if gcloud scheduler jobs describe "$SCHEDULER_NAME" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null 2>&1; then
    gcloud scheduler jobs update http "$SCHEDULER_NAME" \
        --location="$REGION" \
        --schedule="$SCHEDULE" \
        --time-zone="$TIMEZONE" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
        --http-method=POST \
        --oauth-service-account-email="$SA_EMAIL" \
        --quiet
    info "Updated Cloud Scheduler: $SCHEDULER_NAME"
else
    gcloud scheduler jobs create http "$SCHEDULER_NAME" \
        --location="$REGION" \
        --schedule="$SCHEDULE" \
        --time-zone="$TIMEZONE" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
        --http-method=POST \
        --oauth-service-account-email="$SA_EMAIL" \
        --quiet
    info "Created Cloud Scheduler: $SCHEDULER_NAME"
fi

# ── Summary ──
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Personal Doctor — Deployed to Google Cloud!"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  Project:     $PROJECT_ID"
echo "  Region:      $REGION"
echo "  Image:       $IMAGE_URI"
echo "  Job:         $JOB_NAME"
echo "  Schedule:    $SCHEDULE ($TIMEZONE)"
echo "  Recipient:   (stored in Secret Manager: pd-email-to)"
echo ""
echo "  Run manually:"
echo "    gcloud run jobs execute $JOB_NAME --region=$REGION"
echo ""
echo "  View logs:"
echo "    gcloud run jobs executions list --job=$JOB_NAME --region=$REGION"
echo "    gcloud logging read 'resource.type=\"cloud_run_job\"' --limit=50"
echo ""
echo "  Update image after code changes:"
echo "    docker build -t $IMAGE_URI . && docker push $IMAGE_URI"
echo "    gcloud run jobs update $JOB_NAME --image=$IMAGE_URI --region=$REGION"
echo ""
echo "════════════════════════════════════════════════════════════════"
