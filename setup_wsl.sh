#!/usr/bin/env bash
# setup_wsl.sh - Windows WSL development setup for Coil-Gun Sequencer
#
# Run from the repository root:
#   chmod +x setup_wsl.sh
#   ./setup_wsl.sh
#
# If this repo is on a Windows-mounted filesystem that does not allow chmod:
#   bash setup_wsl.sh
#
# Activate the virtual environment after setup:
#   source .venv/bin/activate
#
# Run the app in WSL development mode with mock hardware:
#   COILGUN_HW=mock python run.py
#
# Run tests:
#   python -m pytest tests/
#
# Leave the virtual environment:
#   deactivate
#
# This script is safe to re-run. It installs missing apt packages, creates or
# reuses .venv, installs Python dependencies into that venv, creates data/,
# and performs a few lightweight verification checks.

set -euo pipefail

VENV_DIR=".venv"
MIN_PYTHON="3.9"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok() { printf "${GREEN}[OK]${NC}   %s\n" "$*"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
fail() { printf "${RED}[FAIL]${NC} %s\n" "$*"; exit 1; }
info() { printf "       %s\n" "$*"; }

cd "$REPO_ROOT"

printf "\n=== WSL check ===\n"
if grep -qi microsoft /proc/version 2>/dev/null; then
    ok "Running under WSL"
else
    warn "This does not look like WSL; continuing anyway"
fi

printf "\n=== System packages ===\n"

if ! command -v apt-get >/dev/null 2>&1; then
    fail "apt-get not found. This script expects an Ubuntu/Debian WSL distro."
fi

APT_PACKAGES=(
    build-essential
    ca-certificates
    curl
    git
    python3
    python3-dev
    python3-pip
    python3-venv
    sqlite3
)

MISSING=()
for pkg in "${APT_PACKAGES[@]}"; do
    if dpkg -s "$pkg" >/dev/null 2>&1; then
        ok "$pkg"
    else
        warn "$pkg is missing"
        MISSING+=("$pkg")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    info "Installing missing packages: ${MISSING[*]}"
    sudo apt-get update
    sudo apt-get install -y "${MISSING[@]}"
else
    ok "All required apt packages are installed"
fi

printf "\n=== Python ===\n"

PYTHON="python3"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    fail "python3 not found after package installation"
fi

PY_VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_VER_OK="$("$PYTHON" -c "import sys; print(int(sys.version_info >= (${MIN_PYTHON//./, })))")"
if [ "$PY_VER_OK" = "1" ]; then
    ok "Python $PY_VER >= $MIN_PYTHON"
else
    fail "Python $PY_VER found, but Python >= $MIN_PYTHON is required"
fi

printf "\n=== Git safe.directory ===\n"

# Use a per-command safe.directory override here so this setup can repair a
# checkout that Git currently considers dubious.
if git -c "safe.directory=$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git config --global --get-all safe.directory | grep -Fxq "$REPO_ROOT"; then
        ok "Git safe.directory already includes $REPO_ROOT"
    else
        git config --global --add safe.directory "$REPO_ROOT"
        ok "Added Git safe.directory for $REPO_ROOT"
    fi
else
    warn "Not inside a Git work tree; skipping safe.directory"
fi

printf "\n=== Virtual environment ===\n"

if [ -x "$VENV_DIR/bin/python" ]; then
    ok "$VENV_DIR already exists"
else
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Created $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Activated $VENV_DIR ($(python --version))"

printf "\n=== Python dependencies ===\n"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install pytest
ok "Installed project and test dependencies"

printf "\n=== Local directories ===\n"

mkdir -p data
ok "data/ exists"

printf "\n=== Verification ===\n"

IMPORTS=(
    flask
    flask_sqlalchemy
    flask_socketio
    simple_websocket
    pytest
)

ALL_OK=true
for mod in "${IMPORTS[@]}"; do
    if python -c "import ${mod}" >/dev/null 2>&1; then
        ok "import ${mod}"
    else
        warn "import ${mod} failed"
        ALL_OK=false
    fi
done

if COILGUN_HW=mock python -c "from app import create_app; app = create_app(); print('app ok')" >/dev/null; then
    ok "app.create_app() works with COILGUN_HW=mock"
else
    warn "app.create_app() failed with COILGUN_HW=mock"
    ALL_OK=false
fi

printf "\n"
if [ "$ALL_OK" = true ]; then
    printf "${GREEN}=== WSL setup complete ===${NC}\n"
else
    printf "${YELLOW}=== WSL setup complete with warnings ===${NC}\n"
fi

printf "\nNext commands:\n"
printf "  source .venv/bin/activate\n"
printf "  COILGUN_HW=mock python run.py\n"
printf "\nOpen the app at http://localhost:5000\n"
