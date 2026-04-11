#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  setup_host_rpi.sh — Raspberry Pi 5 host setup for the Coil-Gun Sequencer
#
#  Installs system packages, creates a Python virtual environment, installs
#  pip dependencies, and verifies that all required imports resolve.
#
#  Usage:
#    chmod +x setup_host_rpi.sh
#    ./setup_host_rpi.sh
#
#  Run from the repository root.  Safe to re-run (idempotent).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

VENV_DIR=".venv"
MIN_PYTHON="3.9"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Helpers ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

ok()   { printf "${GREEN}[OK]${NC}   %s\n" "$*"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
fail() { printf "${RED}[FAIL]${NC} %s\n" "$*"; exit 1; }
info() { printf "       %s\n" "$*"; }

# ── 1. Check Python version ─────────────────────────────────────────────────

printf "\n=== Checking Python ===\n"

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -z "$PYTHON" ] && fail "python3 not found. Install with: sudo apt install python3"

PY_VER="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_VER_OK="$($PYTHON -c "import sys; print(int(sys.version_info >= (${MIN_PYTHON//./, })))")"

if [ "$PY_VER_OK" = "1" ]; then
    ok "Python $PY_VER (>= $MIN_PYTHON)"
else
    fail "Python $PY_VER found, but >= $MIN_PYTHON is required"
fi

# ── 2. Install system packages ───────────────────────────────────────────────

printf "\n=== System packages (apt) ===\n"

# Packages needed:
#   python3-venv   — venv module (not always present on minimal installs)
#   python3-dev    — headers for building C extensions (some pip packages)
#   python3-lgpio  — RPi 5 GPIO backend (C extension + libgpiod)
#   python3-gpiozero — gpiozero library (also pulls in dependencies)
#   python3-spidev — SPI access (needed for future MCP3008 ADC)

APT_PACKAGES=(
    python3-venv
    python3-dev
    python3-lgpio
    python3-gpiozero
    python3-spidev
)

MISSING=()
for pkg in "${APT_PACKAGES[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        ok "$pkg"
    else
        MISSING+=("$pkg")
        warn "$pkg — not installed"
    fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
    info ""
    info "Installing missing packages: ${MISSING[*]}"
    sudo apt update -qq
    sudo apt install -y "${MISSING[@]}"
    ok "System packages installed"
else
    ok "All system packages present"
fi

# ── 3. Create virtual environment ────────────────────────────────────────────

printf "\n=== Virtual environment ===\n"

cd "$REPO_ROOT"

if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    ok "$VENV_DIR already exists"
else
    info "Creating $VENV_DIR with --system-site-packages (for lgpio/gpiozero)..."
    $PYTHON -m venv --system-site-packages "$VENV_DIR"
    ok "$VENV_DIR created"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Activated $VENV_DIR ($(python3 --version))"

# ── 4. Install pip dependencies ──────────────────────────────────────────────

printf "\n=== Pip dependencies ===\n"

# Upgrade pip quietly
python3 -m pip install --quiet --upgrade pip

# Install from requirements.txt
python3 -m pip install --quiet -r requirements.txt

ok "Pip packages installed from requirements.txt"

# ── 5. Create data directory ─────────────────────────────────────────────────

printf "\n=== Data directory ===\n"

mkdir -p "$REPO_ROOT/data"
ok "data/ directory exists"

# ── 6. Verify imports ────────────────────────────────────────────────────────

printf "\n=== Verifying imports ===\n"

IMPORT_CHECKS=(
    "flask:flask"
    "flask_sqlalchemy:flask-sqlalchemy"
    "waitress:waitress"
    "gpiozero:gpiozero"
    "lgpio:lgpio (RPi 5 pin factory)"
    "spidev:spidev (SPI for future ADC)"
)

ALL_OK=true
for entry in "${IMPORT_CHECKS[@]}"; do
    mod="${entry%%:*}"
    label="${entry#*:}"
    if python3 -c "import $mod" 2>/dev/null; then
        ok "$label"
    else
        warn "$label — import failed"
        ALL_OK=false
    fi
done

# Verify the app itself loads
if python3 -c "from app import create_app; create_app()" 2>/dev/null; then
    ok "app.create_app() — sequencer loads cleanly"
else
    warn "app.create_app() — failed (check output above)"
    ALL_OK=false
fi

# ── 7. SPI interface reminder ────────────────────────────────────────────────

printf "\n=== Hardware interfaces ===\n"

if [ -e /dev/spidev0.0 ]; then
    ok "SPI enabled (/dev/spidev0.0 present)"
else
    warn "SPI not enabled — needed for future ADC (MCP3008/ADS1115)"
    info "Enable with: sudo raspi-config nonint do_spi 0"
    info "Or: Preferences > Raspberry Pi Configuration > Interfaces > SPI"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

printf "\n"
if [ "$ALL_OK" = true ]; then
    printf "${GREEN}=== Setup complete ===${NC}\n"
else
    printf "${YELLOW}=== Setup complete (with warnings — see above) ===${NC}\n"
fi

printf "\n  To run the sequencer:\n"
printf "    cd %s\n" "$REPO_ROOT"
printf "    source .venv/bin/activate\n"
printf "    python run.py\n"
printf "\n  The app will be available at http://<pi-ip>:5000\n\n"
