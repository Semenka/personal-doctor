#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# sync-and-relaunch.sh — Pull latest from GitHub, relaunch everything
#
# Pulls from origin/main, reinstalls deps if needed, restarts:
#   1. OpenClaw skill (re-copies skill files)
#   2. Docker containers (docker-compose rebuild)
#   3. Python launchd service (if running on macOS)
#
# Usage:
#   ./sync-and-relaunch.sh           # Full sync + relaunch
#   ./sync-and-relaunch.sh --check   # Only check if updates available
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
LOG_DIR="$REPO_DIR/logs"
OPENCLAW_SKILLS_DIR="$HOME/.openclaw/skills"
SKILL_NAME="personal-doctor"
BRANCH="main"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
LOG_FILE="$LOG_DIR/sync.log"

mkdir -p "$LOG_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC} $*" | tee -a "$LOG_FILE"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $*" | tee -a "$LOG_FILE"; }
fail()  { echo -e "${RED}[ERR]${NC} $*" | tee -a "$LOG_FILE"; exit 1; }
step()  { echo -e "\n${BLUE}${BOLD}── $* ──${NC}" | tee -a "$LOG_FILE"; }

echo "[$TIMESTAMP] Sync started" >> "$LOG_FILE"

# ── Fetch latest from origin ──
step "Fetching latest from GitHub"
cd "$REPO_DIR"
git fetch origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"

# ── Check if updates are available ──
LOCAL_SHA=$(git rev-parse HEAD 2>/dev/null || echo "none")
REMOTE_SHA=$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "none")

if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    info "Already up to date (${LOCAL_SHA:0:7})"
    if [ "${1:-}" = "--check" ]; then
        echo "NO_UPDATES"
        exit 0
    fi
    # Still allow forced relaunch without --check
    if [ "${1:-}" != "--force" ]; then
        info "No updates found. Use --force to relaunch anyway."
        exit 0
    fi
fi

if [ "${1:-}" = "--check" ]; then
    echo "UPDATES_AVAILABLE"
    echo "Local:  ${LOCAL_SHA:0:7}"
    echo "Remote: ${REMOTE_SHA:0:7}"
    git log --oneline "$LOCAL_SHA..$REMOTE_SHA" 2>/dev/null | head -10
    exit 0
fi

# ── Pull changes ──
step "Pulling changes"
# Stash any local changes (like .env edits)
git stash --include-untracked 2>/dev/null || true
git checkout "$BRANCH" 2>&1 | tee -a "$LOG_FILE"
git pull origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"
# Re-apply stashed changes
git stash pop 2>/dev/null || true
info "Updated to $(git rev-parse --short HEAD)"

# ── Update Python dependencies if requirements changed ──
step "Checking Python dependencies"
if git diff "$LOCAL_SHA..$REMOTE_SHA" --name-only 2>/dev/null | grep -q "requirements.txt"; then
    if [ -d "$VENV_DIR" ]; then
        source "$VENV_DIR/bin/activate"
        pip install -r "$REPO_DIR/requirements.txt" -q 2>&1 | tee -a "$LOG_FILE"
        info "Python dependencies updated"
    else
        warn "No venv found at $VENV_DIR — skipping pip install"
    fi
else
    info "requirements.txt unchanged — skipping pip install"
fi

# ── Reinstall OpenClaw skill ──
step "Updating OpenClaw skill"
if [ -d "$REPO_DIR/openclaw/$SKILL_NAME" ]; then
    mkdir -p "$OPENCLAW_SKILLS_DIR"
    rm -rf "$OPENCLAW_SKILLS_DIR/$SKILL_NAME"
    cp -r "$REPO_DIR/openclaw/$SKILL_NAME" "$OPENCLAW_SKILLS_DIR/$SKILL_NAME"
    info "OpenClaw skill reinstalled"
else
    warn "No OpenClaw skill directory found"
fi

# Refresh OpenClaw cron jobs
if command -v openclaw &>/dev/null; then
    PYTHON_CMD="$VENV_DIR/bin/python"

    # Remove old cron jobs and re-add them (picks up any schedule changes)
    for job_name in "personal-doctor-daily" "personal-doctor-oura" "personal-doctor-gdrive"; do
        openclaw cron rm "$job_name" 2>/dev/null || true
    done

    openclaw cron add \
        --name "personal-doctor-daily" \
        --cron "50 7 * * *" \
        --message "Run my health pipeline now. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.run_pipeline 2>&1 | tee -a $LOG_DIR/pipeline.log. Then summarize the output." \
        2>/dev/null && info "Cron: daily pipeline at 07:50" || warn "Could not set cron: personal-doctor-daily"

    openclaw cron add \
        --name "personal-doctor-oura" \
        --cron "40 7 * * *" \
        --message "Sync my Oura Ring data. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.cli --source oura 2>&1. Report the result briefly." \
        2>/dev/null && info "Cron: Oura sync at 07:40" || warn "Could not set cron: personal-doctor-oura"

    openclaw cron add \
        --name "personal-doctor-gdrive" \
        --cron "30 7 * * *" \
        --message "Scan my Google Drive health folder for new reports. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.cli --source gdrive 2>&1. Report any new files found." \
        2>/dev/null && info "Cron: Drive scan at 07:30" || warn "Could not set cron: personal-doctor-gdrive"

    info "OpenClaw cron jobs refreshed"
else
    warn "OpenClaw CLI not found — skipping cron refresh"
fi

# ── Restart Docker containers ──
step "Restarting Docker containers"
if command -v docker &>/dev/null && [ -f "$REPO_DIR/docker-compose.yml" ]; then
    cd "$REPO_DIR"
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
    docker compose up -d --build 2>&1 | tee -a "$LOG_FILE" || \
        docker-compose up -d --build 2>&1 | tee -a "$LOG_FILE" || \
        warn "Docker compose failed — containers may not be running"
    info "Docker containers rebuilt and started"
else
    warn "Docker or docker-compose.yml not found — skipping"
fi

# ── Restart macOS launchd service ──
step "Restarting macOS launchd service"
PLIST="$HOME/Library/LaunchAgents/com.personal-doctor.plist"
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST" 2>/dev/null || true
    info "launchd service restarted"
else
    info "No launchd plist found — skipping (normal if not on Mac Mini)"
fi

# ── Summary ──
echo "" | tee -a "$LOG_FILE"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
echo -e "${GREEN}${BOLD}  Sync complete at $TIMESTAMP${NC}" | tee -a "$LOG_FILE"
echo -e "  Commit: $(git rev-parse --short HEAD)" | tee -a "$LOG_FILE"
echo -e "  Message: $(git log -1 --format='%s')" | tee -a "$LOG_FILE"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
