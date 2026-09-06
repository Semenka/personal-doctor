#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Personal Doctor — OpenClaw Integration Setup
#
# Installs Personal Doctor as an OpenClaw skill (on-demand interface).
# After running this, OpenClaw can:
#   - Run the health pipeline on command ("run my health pipeline")
#   - Sync the watches, show health status, logs, and advice
#
# Scheduling is the launchd service's job (setup-mac.sh). OpenClaw cron jobs
# for the pipeline are OFF by default: they would run the same pipeline a
# second time each morning and send a second digest. --with-cron re-enables
# them for a machine that runs OpenClaw but not the service.
#
# Prerequisites:
#   - OpenClaw installed (npm install -g openclaw@latest)
#   - Python 3.11+ installed
#   - This repo cloned to ~/personal-doctor
#
# Usage:
#   ./setup-openclaw.sh              # Full setup (venv + skill, no cron)
#   ./setup-openclaw.sh --skill-only # Only install the OpenClaw skill
#   ./setup-openclaw.sh --with-cron  # Also register OpenClaw cron jobs
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
WITH_CRON=false
[ "${1:-}" = "--skill-only" ] && SKILL_ONLY=true
[ "${1:-}" = "--with-cron" ] && WITH_CRON=true

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

# ── OpenClaw cron jobs (opt-in) ──
PYTHON_CMD="$VENV_DIR/bin/python"
if [ "$WITH_CRON" = true ]; then
    step "Setting up OpenClaw cron jobs (--with-cron)"
    # Only for a host WITHOUT the launchd service — otherwise the digest goes out twice.
    openclaw cron add \
        --name "personal-doctor-daily" \
        --cron "0 8 * * *" \
        --message "Run my health pipeline now. Execute: cd $REPO_DIR && $PYTHON_CMD -m app.sync.run_pipeline 2>&1 | tee -a $LOG_DIR/pipeline.log. Then summarize the output." \
        2>/dev/null && info "Cron: daily pipeline at 08:00" || warn "Cron job may already exist (personal-doctor-daily)"
    echo ""
    echo "Active OpenClaw cron jobs:"
    openclaw cron list 2>/dev/null || true
else
    step "OpenClaw cron jobs"
    # Remove jobs left behind by earlier versions of this script — the
    # launchd service already runs the same pipeline at 07:38 → 08:00.
    for job_name in "personal-doctor-daily" "personal-doctor-oura" "personal-doctor-gdrive"; do
        openclaw cron rm "$job_name" 2>/dev/null && info "Removed stale cron: $job_name" || true
    done
    info "Scheduling stays with the launchd service (use --with-cron on a host without it)"
fi

# ── Summary ──
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Personal Doctor — OpenClaw Integration Ready${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  OpenClaw skill installed. You can now say:"
echo ""
echo '    "Run my health pipeline"'
echo '    "Check my health status"        (is watch data arriving?)'
echo '    "Sync my Fitbit data"'
echo '    "Get my health advice for today"'
echo '    "Show my last health advice"'
echo '    "Analyze this MRI image"'
echo '    "Show my health logs"'
echo ""
echo "  Automated schedule: launchd service (see openclaw/personal-doctor/SKILL.md)"
echo "    07:38  Oura ring sweep   07:40  Fitbit Air + Pebble sync"
echo "    08:00  AI advisor → email + WhatsApp"
echo ""
echo "  Uninstall:"
echo "    ./setup-openclaw.sh --uninstall"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
