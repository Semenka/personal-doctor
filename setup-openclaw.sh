#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Personal Doctor — OpenClaw Integration Setup
#
# Installs Personal Doctor as an OpenClaw skill with cron scheduling.
# After running this, OpenClaw can:
#   - Run the health pipeline on command ("run my health pipeline")
#   - Show health status, logs, and advice
#   - Auto-run daily at 07:30 via OpenClaw cron
#
# Prerequisites:
#   - OpenClaw installed (npm install -g openclaw@latest)
#   - Python 3.11+ installed
#   - This repo cloned to ~/personal-doctor-python
#
# Usage:
#   ./setup-openclaw.sh              # Full setup
#   ./setup-openclaw.sh --skill-only # Only install the OpenClaw skill
#   ./setup-openclaw.sh --uninstall  # Remove skill + cron jobs
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$HOME/personal-doctor/data"
LOG_DIR="$HOME/personal-doctor/logs"
VENV_DIR="$REPO_DIR/.venv"
ENV_FILE="$REPO_DIR/.env"
OPENCLAW_SKILLS_DIR="$HOME/.openclaw/skills"
SKILL_NAME="personal-doctor"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $*"; }
fail()  { echo -e "${RED}[ERR]${NC} $*"; exit 1; }
step()  { echo -e "\n${BLUE}${BOLD}── $* ──${NC}"; }

# ── Uninstall ──
if [ "${1:-}" = "--uninstall" ]; then
    step "Uninstalling Personal Doctor from OpenClaw"
    rm -rf "$OPENCLAW_SKILLS_DIR/$SKILL_NAME" 2>/dev/null && info "Removed skill" || true
    if command -v openclaw &>/dev/null; then
        # Remove cron jobs
        for job_name in "personal-doctor-daily" "personal-doctor-oura" "personal-doctor-gdrive"; do
            openclaw cron rm "$job_name" 2>/dev/null && info "Removed cron: $job_name" || true
        done
    fi
    echo "Done. Data preserved at ~/personal-doctor/"
    exit 0
fi

SKILL_ONLY=false
[ "${1:-}" = "--skill-only" ] && SKILL_ONLY=true

echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║   Personal Doctor — OpenClaw Setup            ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Check OpenClaw ──
step "Checking OpenClaw installation"
if command -v openclaw &>/dev/null; then
    OC_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
    info "OpenClaw found: $OC_VERSION"
else
    fail "OpenClaw not found. Install: npm install -g openclaw@latest && openclaw onboard"
fi

if [ "$SKILL_ONLY" = false ]; then
    # ── Python + venv ──
    step "Setting up Python environment"
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        info "Python $PY_VERSION"
    else
        fail "Python 3 not found. Install: brew install python@3.12"
    fi

    mkdir -p "$DATA_DIR" "$LOG_DIR"

    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        info "Created venv: $VENV_DIR"
    else
        info "Venv exists: $VENV_DIR"
    fi

    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip -q
    pip install -r "$REPO_DIR/requirements.txt" -q
    info "Dependencies installed"

    # ── Environment file ──
    step "Checking environment configuration"
    if [ -f "$ENV_FILE" ]; then
        info ".env file exists"
    else
        cp "$REPO_DIR/.env.example" "$ENV_FILE"
        warn ".env created from template — edit it with your credentials:"
        echo "  nano $ENV_FILE"
    fi
fi

# ── Install OpenClaw skill ──
step "Installing OpenClaw skill"
mkdir -p "$OPENCLAW_SKILLS_DIR"
# Copy skill directory
rm -rf "$OPENCLAW_SKILLS_DIR/$SKILL_NAME"
cp -r "$REPO_DIR/openclaw/$SKILL_NAME" "$OPENCLAW_SKILLS_DIR/$SKILL_NAME"
info "Installed skill: $OPENCLAW_SKILLS_DIR/$SKILL_NAME"

# ── Set up OpenClaw cron jobs ──
step "Setting up OpenClaw cron jobs"

PYTHON_CMD="$VENV_DIR/bin/python"

# Daily pipeline at 07:30 Europe/Paris
# OpenClaw cron runs shell commands via the agent
openclaw cron add \
    --name "personal-doctor-daily" \
    --cron "30 7 * * *" \
    --message "Run my health pipeline now. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.run_pipeline 2>&1 | tee -a $LOG_DIR/pipeline.log. Then summarize the output." \
    2>/dev/null && info "Cron: daily pipeline at 07:30" || warn "Cron job may already exist (personal-doctor-daily)"

# Optional: Oura sync at 07:20 (data available earlier)
openclaw cron add \
    --name "personal-doctor-oura" \
    --cron "20 7 * * *" \
    --message "Sync my Oura Ring data. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.cli --source oura 2>&1. Report the result briefly." \
    2>/dev/null && info "Cron: Oura sync at 07:20" || warn "Cron job may already exist (personal-doctor-oura)"

# Optional: Google Drive scan at 07:00
openclaw cron add \
    --name "personal-doctor-gdrive" \
    --cron "0 7 * * *" \
    --message "Scan my Google Drive health folder for new reports. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.cli --source gdrive 2>&1. Report any new files found." \
    2>/dev/null && info "Cron: Drive scan at 07:00" || warn "Cron job may already exist (personal-doctor-gdrive)"

# List active cron jobs
echo ""
echo "Active OpenClaw cron jobs:"
openclaw cron list 2>/dev/null || true

# ── Summary ──
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Personal Doctor — OpenClaw Integration Ready${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  OpenClaw skill installed. You can now say:"
echo ""
echo '    "Run my health pipeline"'
echo '    "Get my health advice for today"'
echo '    "Check my health status"'
echo '    "Show my last health advice"'
echo '    "Sync my Oura data"'
echo '    "Analyze this MRI image"'
echo '    "Show my health logs"'
echo ""
echo "  Automated schedule (OpenClaw cron):"
echo "    07:00  Google Drive health folder scan"
echo "    07:20  Oura Ring data sync"
echo "    07:30  AI advisor → email delivery"
echo ""
echo "  Manage cron jobs:"
echo "    openclaw cron list"
echo "    openclaw cron rm personal-doctor-daily"
echo ""
echo "  Uninstall:"
echo "    ./setup-openclaw.sh --uninstall"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
