#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Personal Doctor — Mac Mini Setup
#
# One-command setup for running the AI health advisor on a Mac Mini.
# Sets up: Python venv, dependencies, directories, launchd auto-start.
#
# Usage:
#   ./setup-mac.sh            # Full setup + start
#   ./setup-mac.sh --no-start # Setup only, don't start the service
#   ./setup-mac.sh --uninstall # Remove launchd service
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$HOME/personal-doctor/data"
LOG_DIR="$HOME/personal-doctor/logs"
VENV_DIR="$REPO_DIR/.venv"
PLIST_NAME="com.personal-doctor"
PLIST_SRC="$REPO_DIR/macos/${PLIST_NAME}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
ENV_FILE="$REPO_DIR/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $*"; }
fail()  { echo -e "${RED}[ERR]${NC} $*"; exit 1; }
step()  { echo -e "\n${BLUE}${BOLD}── $* ──${NC}"; }

# ── Uninstall ──
if [ "${1:-}" = "--uninstall" ]; then
    step "Uninstalling Personal Doctor service"
    if launchctl list "$PLIST_NAME" &>/dev/null; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        info "Service stopped."
    fi
    [ -f "$PLIST_DST" ] && rm "$PLIST_DST" && info "Removed $PLIST_DST"
    echo "Data and logs preserved at ~/personal-doctor/"
    echo "To fully remove: rm -rf ~/personal-doctor $REPO_DIR/.venv"
    exit 0
fi

NO_START=false
[ "${1:-}" = "--no-start" ] && NO_START=true

echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║   Personal Doctor — Mac Mini Setup            ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: Check prerequisites ──
step "Checking prerequisites"

# Python 3.12+
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        info "Python $PY_VERSION"
    else
        warn "Python $PY_VERSION found. 3.11+ recommended."
        if command -v brew &>/dev/null; then
            echo "  Install: brew install python@3.12"
        fi
    fi
else
    fail "Python 3 not found. Install: brew install python@3.12"
fi

# Homebrew (optional but recommended)
if command -v brew &>/dev/null; then
    info "Homebrew available"
else
    warn "Homebrew not found. Install from https://brew.sh if you need system packages."
fi

# ── Step 2: Create directories ──
step "Creating directories"
mkdir -p "$DATA_DIR" "$LOG_DIR"
info "Data: $DATA_DIR"
info "Logs: $LOG_DIR"

# ── Step 3: Python virtual environment ──
step "Setting up Python virtual environment"
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

VENV_PYTHON="$VENV_DIR/bin/python"

# ── Step 4: Environment file ──
step "Checking environment configuration"
if [ -f "$ENV_FILE" ]; then
    info ".env file exists"
    # Validate required variables
    MISSING=()
    source "$ENV_FILE" 2>/dev/null || true
    [ -z "${ANTHROPIC_API_KEY:-}" ] && MISSING+=("ANTHROPIC_API_KEY")
    [ -z "${OURA_ACCESS_TOKEN:-}" ] && MISSING+=("OURA_ACCESS_TOKEN")
    [ -z "${EMAIL_TO:-}" ] && MISSING+=("EMAIL_TO")
    [ -z "${SMTP_HOST:-}" ] && MISSING+=("SMTP_HOST")
    [ -z "${SMTP_USER:-}" ] && MISSING+=("SMTP_USER")
    [ -z "${SMTP_PASSWORD:-}" ] && MISSING+=("SMTP_PASSWORD")
    if [ ${#MISSING[@]} -gt 0 ]; then
        warn "Missing variables in .env: ${MISSING[*]}"
        echo "  Edit: $ENV_FILE"
    else
        info "All required variables set"
    fi
else
    cp "$REPO_DIR/.env.example" "$ENV_FILE"
    warn ".env created from template. You MUST edit it with your credentials:"
    echo ""
    echo "  nano $ENV_FILE"
    echo ""
    echo "  Required:"
    echo "    ANTHROPIC_API_KEY   — from console.anthropic.com"
    echo "    OURA_ACCESS_TOKEN   — from cloud.ouraring.com"
    echo "    EMAIL_TO            — your email address"
    echo "    SMTP_HOST           — smtp.yahoo.com"
    echo "    SMTP_USER           — your Yahoo email"
    echo "    SMTP_PASSWORD       — Yahoo App Password"
    echo ""
fi

# ── Step 5: Google Drive credentials ──
step "Google Drive setup"
GDRIVE_DIR="$HOME/.config/personal-doctor/gdrive"
if [ -f "$GDRIVE_DIR/credentials.json" ]; then
    info "Google Drive credentials found: $GDRIVE_DIR"
    # Ensure GDRIVE_CREDENTIALS_DIR is in .env
    if ! grep -q "GDRIVE_CREDENTIALS_DIR" "$ENV_FILE" 2>/dev/null || grep -q "^#.*GDRIVE_CREDENTIALS_DIR" "$ENV_FILE" 2>/dev/null; then
        echo "" >> "$ENV_FILE"
        echo "GDRIVE_CREDENTIALS_DIR=$GDRIVE_DIR" >> "$ENV_FILE"
        info "Added GDRIVE_CREDENTIALS_DIR to .env"
    fi
else
    warn "Google Drive not configured (optional)."
    echo "  To enable:"
    echo "    1. Create a project at https://console.cloud.google.com"
    echo "    2. Enable Google Drive API"
    echo "    3. Create OAuth2 credentials (Desktop app)"
    echo "    4. Download credentials.json to: $GDRIVE_DIR/"
    echo "    5. Run: python -m app.sync.cli --source gdrive"
    echo "       (opens browser for first-time OAuth2 consent)"
    mkdir -p "$GDRIVE_DIR"
fi

# ── Step 6: Quick validation ──
step "Validating configuration"
"$VENV_PYTHON" -c "
from app.sync.config import load_config
cfg = load_config()
checks = {
    'Anthropic API key': bool(cfg.anthropic_api_key),
    'Oura token': bool(cfg.oura_access_token),
    'SMTP configured': bool(cfg.smtp_host and cfg.smtp_password),
    'Email recipient': bool(cfg.email_to),
    'Google Drive': bool(cfg.gdrive_credentials_dir),
}
for name, ok in checks.items():
    status = 'OK' if ok else 'MISSING'
    print(f'  {name}: {status}')
" 2>&1 || warn "Validation failed — check your .env file"

# ── Step 7: Install launchd service ──
step "Installing macOS launch agent"

# Generate plist with correct paths
sed -e "s|__VENV_PYTHON__|${VENV_PYTHON}|g" \
    -e "s|__REPO_DIR__|${REPO_DIR}|g" \
    -e "s|__LOG_DIR__|${LOG_DIR}|g" \
    "$PLIST_SRC" > "$PLIST_DST"
info "Installed: $PLIST_DST"

# ── Step 8: Start the service ──
if [ "$NO_START" = false ]; then
    step "Starting Personal Doctor service"

    # Unload first if already loaded
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    info "Service started!"

    # Wait a moment and check
    sleep 2
    if launchctl list "$PLIST_NAME" &>/dev/null; then
        info "Service is running"
    else
        warn "Service may not have started. Check logs:"
        echo "  tail -f $LOG_DIR/launchd-stderr.log"
    fi
else
    info "Service installed but not started (--no-start)"
    echo "  Start manually: launchctl load $PLIST_DST"
fi

# ── Summary ──
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Personal Doctor — Ready on Mac Mini${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Web dashboard:    http://localhost:8000"
echo "  Health check:     http://localhost:8000/health"
echo "  Last advice:      http://localhost:8000/advice"
echo "  Trigger now:      curl -X POST http://localhost:8000/run"
echo ""
echo "  Schedule (daily):"
echo "    07:00  Google Drive scan"
echo "    07:20  Oura Ring sync"
echo "    07:30  AI advisor → email"
echo ""
echo "  Logs:             tail -f $LOG_DIR/personal-doctor.log"
echo "  Service logs:     tail -f $LOG_DIR/launchd-stdout.log"
echo ""
echo "  Manage service:"
echo "    Stop:           launchctl unload $PLIST_DST"
echo "    Start:          launchctl load $PLIST_DST"
echo "    Restart:        launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
echo "    Uninstall:      ./setup-mac.sh --uninstall"
echo ""
echo "  Manual pipeline:  $VENV_PYTHON -m app.sync.cli --source advisor --email"
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
