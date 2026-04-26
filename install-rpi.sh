#!/bin/bash
#
# ============================================================================
# IdeaHacks 2026 — Raspberry Pi 4 Kiosk Installation Script (LEAN)
# ============================================================================
# Designed for fresh Raspberry Pi OS (64-bit) on 8 GB SD cards.
# Uses only prebuilt binaries — no compilation, no build dependencies.
#
# WHAT IT DOES:
#   1. apt-get update (no upgrade — saves ~1-2 GB)
#   2. Installs only runtime system libraries (no -dev packages)
#   3. Installs Calibre via official installer (skips if already present)
#   4. Installs Node.js 22 + pnpm (skips if already present)
#   5. Installs uv + Python 3.14 via prebuilt binaries (skips if present)
#   6. Clones or uses existing ideahacks-2026 repo
#   7. Builds frontend for production
#   8. Installs Python backend dependencies
#   9. Patches Flask to serve frontend/dist
#  10. Creates systemd service for auto-start on boot
#  11. Sets up USB permissions for e-reader hotplug
#
# SPACE FOOTPRINT (estimated on fresh Pi OS):
#   OS base:          ~4.5 GB
#   Calibre:          ~600 MB
#   Node.js 22:       ~200 MB
#   uv + Python 3.14: ~150 MB
#   Repo + node_modules + venv: ~400 MB
#   Total after install: ~5.8 GB / 6.6 GB
#
# USAGE:
#   sudo bash install-rpi.sh
#
# RESUME-FRIENDLY:
#   If the script fails or you re-run it, already-completed steps are skipped.
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration — edit these if needed
# ----------------------------------------------------------------------------
REPO_URL=""                       # Leave blank to auto-detect, or set e.g. "https://github.com/yourname/ideahacks-2026.git"
INSTALL_DIR="/opt/ideahacks"      # Where the project will live
SERVICE_USER=""                   # Leave blank to use the default non-root user (e.g. pi)
SERVICE_NAME="ideahacks"
PYTHON_VERSION="3.14"
NODE_MAJOR="22"
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# Pretty logging
# ----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_step()   { echo -e "${BLUE}${BOLD}==>${NC}${BOLD} $1${NC}"; }
log_info()   { echo -e "   ${CYAN}→${NC} $1"; }
log_success(){ echo -e "   ${GREEN}✓${NC} $1"; }
log_warn()   { echo -e "   ${YELLOW}⚠${NC} $1"; }
log_error()  { echo -e "   ${RED}✗${NC} $1"; }
log_fatal()  { echo -e "${RED}${BOLD}FATAL:${NC} $1"; }
log_skip()   { echo -e "   ${GREEN}⊘${NC} $1 (already done, skipping)"; }

# ----------------------------------------------------------------------------
# Error trap — prints helpful context when anything fails
# ----------------------------------------------------------------------------
trap 'echo "";
      log_fatal "Script exited with an error at line $LINENO.
The command above failed. Here is how to recover:

  1. Read the error message directly above this box.
  2. If it is an apt failure, run:   sudo apt --fix-broken install
  3. If it is a network failure, check your Pi is online:  ping google.com
  4. If Python ${PYTHON_VERSION} fails to install, uv may not have a build yet.
     In that case, lower requires-python in apps/backend/pyproject.toml to >=3.11
     and re-run this script.
  5. For Calibre issues, try:  sudo apt install -y calibre
  6. After fixing the underlying issue, re-run:  sudo bash install-rpi.sh

Quick diagnostics you can run right now:
  sudo systemctl status ${SERVICE_NAME}   # service status (after install)
  sudo journalctl -u ${SERVICE_NAME} -n 50  # recent service logs
  lsusb                                      # see USB devices
  calibre-debug --version                   # verify Calibre
";
      exit 1' ERR

# ----------------------------------------------------------------------------
# Pre-flight checks
# ----------------------------------------------------------------------------
log_step "Pre-flight checks"

if [[ $EUID -ne 0 ]]; then
    log_fatal "This script must be run as root or with sudo.\n   Example: sudo bash install-rpi.sh"
    exit 1
fi

ARCH=$(dpkg --print-architecture)
log_info "Detected architecture: ${ARCH}"
if [[ "$ARCH" != "arm64" && "$ARCH" != "amd64" && "$ARCH" != "armhf" ]]; then
    log_warn "Untested architecture (${ARCH}). This script is designed for arm64 Raspberry Pi 4."
fi

# Detect the non-root user to run the service under
if [[ -z "${SERVICE_USER}" ]]; then
    SERVICE_USER="${SUDO_USER:-}"
    if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == "root" ]]; then
        for candidate in pi ideahacks kiosk; do
            if id "$candidate" &>/dev/null; then
                SERVICE_USER="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$SERVICE_USER" || "$SERVICE_USER" == "root" ]]; then
        log_fatal "Cannot determine a non-root user to run the service.\n   Set SERVICE_USER at the top of this script and try again."
        exit 1
    fi
fi

if ! id "$SERVICE_USER" &>/dev/null; then
    log_step "Creating service user '${SERVICE_USER}'"
    useradd -m -s /bin/bash "$SERVICE_USER"
    log_success "User created"
fi

SERVICE_HOME=$(eval echo "~$SERVICE_USER")
log_info "Service will run as user: ${BOLD}${SERVICE_USER}${NC} (home: ${SERVICE_HOME})"

# Detect if we are already inside the repo
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -z "${REPO_URL}" && -f "${SCRIPT_DIR}/package.json" && -d "${SCRIPT_DIR}/apps/backend" && -d "${SCRIPT_DIR}/apps/frontend" ]]; then
    INSTALL_DIR="$SCRIPT_DIR"
    log_info "Detected existing repo at ${INSTALL_DIR} — using it instead of cloning."
    USING_LOCAL_REPO=1
else
    USING_LOCAL_REPO=0
fi

# Check internet
if ! curl -fsSL https://deb.nodesource.com >/dev/null 2>&1; then
    log_fatal "This Pi does not appear to have internet access.\n   Connect to Wi-Fi or Ethernet and retry."
    exit 1
fi
log_success "Internet connection OK"

# ----------------------------------------------------------------------------
# 1. System update — lean: update package lists only, no full upgrade
# ----------------------------------------------------------------------------
log_step "Updating package lists..."
apt-get update
log_success "Package lists updated"

# ----------------------------------------------------------------------------
# 2. Minimal runtime dependencies (no build tools, no -dev packages)
# ----------------------------------------------------------------------------
# We intentionally skip build-essential and all -dev packages.
# Everything we install (Node, uv, Python, Calibre) comes as prebuilt binaries.
log_step "Installing minimal runtime dependencies..."

apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg \
    libusb-1.0-0 xdg-utils \
    systemd || {
        log_error "apt install failed. Trying with --fix-missing..."
        apt-get install -f -y
        apt-get install -y --no-install-recommends git curl ca-certificates libusb-1.0-0 systemd
    }

apt-get clean
log_success "Runtime dependencies installed"

# ----------------------------------------------------------------------------
# 3. Calibre (official prebuilt installer — MUST work for e-reader support)
# ----------------------------------------------------------------------------
log_step "Checking Calibre..."

if command -v calibre-debug &>/dev/null; then
    log_skip "Calibre"
else
    log_info "Installing Calibre via official prebuilt installer..."
    # The installer needs xdg-utils to create desktop entries, and xz to unpack.
    if ! dpkg -l xdg-utils 2>/dev/null | grep -q "^ii"; then
        apt-get install -y --no-install-recommends xdg-utils
        apt-get clean
    fi
    wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | sh /dev/stdin install_dir=/opt/calibre isolated=y

    # The installer may place binaries directly in /opt/calibre/ or in
    # /opt/calibre/calibre/. Detect the actual location and symlink correctly.
    CALIBRE_BIN_DIR=""
    for candidate in /opt/calibre /opt/calibre/calibre; do
        if [[ -x "${candidate}/calibre-debug" ]]; then
            CALIBRE_BIN_DIR="$candidate"
            break
        fi
    done

    if [[ -z "$CALIBRE_BIN_DIR" ]]; then
        log_fatal "Calibre installer ran but calibre-debug binary was not found in /opt/calibre or /opt/calibre/calibre.\n   Check /tmp/calibre-installer*.log for errors.\n   The prebuilt installer requires xdg-utils and xz."
        exit 1
    fi

    ln -sf "${CALIBRE_BIN_DIR}/calibre-debug" /usr/local/bin/calibre-debug
    ln -sf "${CALIBRE_BIN_DIR}/calibredb"     /usr/local/bin/calibredb
    ln -sf "${CALIBRE_BIN_DIR}/ebook-convert" /usr/local/bin/ebook-convert

    # Clean up installer download cache (~180 MB)
    rm -rf /tmp/calibre-installer-cache
fi

# Verify it actually works — fail hard if not
if ! calibre-debug --version >/dev/null 2>&1; then
    log_fatal "Calibre binary exists but does not run.\n   This usually means a missing runtime library.\n   Try:  ldd $(which calibre-debug) | grep 'not found'\n   The prebuilt installer MUST succeed for e-reader detection to work."
    exit 1
fi

CALIBRE_VERSION=$(calibre-debug --version 2>/dev/null || echo "unknown")
log_success "Calibre ready: ${CALIBRE_VERSION}"
log_info "calibre-debug path: $(which calibre-debug)"
log_info "calibredb   path: $(which calibredb)"

# ----------------------------------------------------------------------------
# 4. Node.js & pnpm (skips if already correct version)
# ----------------------------------------------------------------------------
log_step "Checking Node.js ${NODE_MAJOR}..."

if command -v node &>/dev/null && [[ "$(node -v | cut -d'v' -f2 | cut -d'.' -f1)" == "$NODE_MAJOR" ]]; then
    log_skip "Node.js ${NODE_MAJOR}"
else
    log_info "Installing Node.js ${NODE_MAJOR}..."
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
    apt-get install -y --no-install-recommends nodejs
    apt-get clean
    log_success "Node.js installed: $(node -v)"
fi

log_step "Checking pnpm..."

if command -v pnpm &>/dev/null; then
    log_skip "pnpm"
else
    log_info "Installing pnpm via corepack (Node.js built-in)..."
    # corepack is bundled with Node.js 16+. It manages pnpm/yarn globally.
    corepack enable
    corepack prepare pnpm@latest --activate
    if ! command -v pnpm &>/dev/null; then
        log_fatal "pnpm installation failed.\n   Try manually: corepack enable && corepack prepare pnpm@latest --activate"
        exit 1
    fi
    log_success "pnpm installed: $(pnpm -v)"
fi

# ----------------------------------------------------------------------------
# 5. uv (Python toolchain) — skips if already present
# ----------------------------------------------------------------------------
log_step "Checking uv..."

if command -v uv &>/dev/null; then
    UV_BIN=$(which uv)
    log_skip "uv"
else
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if [[ -f "$HOME/.cargo/bin/uv" ]]; then
        cp "$HOME/.cargo/bin/uv" /usr/local/bin/uv
        chmod +x /usr/local/bin/uv
    elif [[ -f "$HOME/.local/bin/uv" ]]; then
        cp "$HOME/.local/bin/uv" /usr/local/bin/uv
        chmod +x /usr/local/bin/uv
    fi
    if ! command -v uv &>/dev/null; then
        log_fatal "uv installation failed.\n   Try manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    UV_BIN=$(which uv)
    log_success "uv installed: $(${UV_BIN} --version)"
fi

log_info "uv binary: ${UV_BIN}"

# ----------------------------------------------------------------------------
# 6. Python ${PYTHON_VERSION} — skips if already installed via uv
# ----------------------------------------------------------------------------
log_step "Checking Python ${PYTHON_VERSION}..."

if ${UV_BIN} python find "${PYTHON_VERSION}" &>/dev/null 2>&1 || ${UV_BIN} python find 3.14.0 &>/dev/null 2>&1; then
    log_skip "Python ${PYTHON_VERSION}"
else
    log_info "Installing Python ${PYTHON_VERSION} via uv..."
    if ! ${UV_BIN} python install "${PYTHON_VERSION}" 2>/dev/null; then
        log_warn "uv python install ${PYTHON_VERSION} failed. Trying '3.14.0' explicitly..."
        if ! ${UV_BIN} python install 3.14.0 2>/dev/null; then
            log_fatal "Could not install Python ${PYTHON_VERSION} via uv.\n   uv may not yet provide a prebuilt ${PYTHON_VERSION} for ${ARCH}.\n   Options:\n     1. Change 'requires-python' in apps/backend/pyproject.toml to '>=3.11' and retry.\n     2. Build Python ${PYTHON_VERSION} from source (takes ~30 min on a Pi 4)."
            exit 1
        fi
    fi
    log_success "Python ${PYTHON_VERSION} installed"
fi

# ----------------------------------------------------------------------------
# 7. Clone or verify repository
# ----------------------------------------------------------------------------
if [[ "$USING_LOCAL_REPO" -eq 0 ]]; then
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        log_skip "Repository clone"
    else
        log_step "Cloning repository..."
        if [[ -z "$REPO_URL" ]]; then
            log_fatal "REPO_URL is empty and this script is not inside the repo.\n   Please set REPO_URL at the top of this script, e.g.:\n   REPO_URL=\"https://github.com/yourname/ideahacks-2026.git\""
            exit 1
        fi
        rm -rf "$INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
        chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
        log_success "Repository cloned to ${INSTALL_DIR}"
    fi
else
    log_step "Using existing repository at ${INSTALL_DIR}"
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
fi

# Verify expected structure exists
if [[ ! -f "${INSTALL_DIR}/apps/backend/main.py" ]]; then
    log_fatal "The repository at ${INSTALL_DIR} does not contain apps/backend/main.py.\n   Make sure you cloned the correct repo."
    exit 1
fi
if [[ ! -f "${INSTALL_DIR}/apps/frontend/package.json" ]]; then
    log_fatal "The repository at ${INSTALL_DIR} does not contain apps/frontend/package.json.\n   Make sure you cloned the correct repo."
    exit 1
fi

# ----------------------------------------------------------------------------
# 8. JavaScript dependencies & frontend build
# ----------------------------------------------------------------------------
log_step "Installing JavaScript dependencies..."
cd "$INSTALL_DIR"
su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}' && pnpm install"
log_success "JavaScript dependencies installed"

log_step "Building frontend for production..."
su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}' && pnpm build"

# Verify the dist folder was produced
if [[ ! -d "${INSTALL_DIR}/apps/frontend/dist" ]]; then
    log_fatal "Frontend build did not produce apps/frontend/dist.\n   Check the build output above for TypeScript or Vite errors."
    exit 1
fi
log_success "Frontend built successfully"

# ----------------------------------------------------------------------------
# 9. Python backend dependencies
# ----------------------------------------------------------------------------
log_step "Installing Python backend dependencies (uv sync)..."
cd "${INSTALL_DIR}/apps/backend"
su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}/apps/backend' && ${UV_BIN} sync"
log_success "Python dependencies installed"

# Quick sanity check that Flask imports work
if ! su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}/apps/backend' && ${UV_BIN} run python -c 'import flask, flask_sock, usb1, requests; print(\"OK\")'" 2>/dev/null; then
    log_warn "Sanity import check failed. The service may still start, but watch the logs."
fi

# ----------------------------------------------------------------------------
# 10. Patch Flask to serve built frontend from frontend/dist
# ----------------------------------------------------------------------------
log_step "Configuring Flask to serve production frontend..."
BACKEND_MAIN="${INSTALL_DIR}/apps/backend/main.py"

log_info "Current FRONTEND_DIR in main.py:"
grep -n 'FRONTEND_DIR = Path' "$BACKEND_MAIN" | head -n 1 || true

if grep -q 'FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"$' "$BACKEND_MAIN"; then
    sed -i 's|FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"|FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"|' "$BACKEND_MAIN"
    log_success "Patched FRONTEND_DIR → frontend/dist"
elif grep -q 'FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"' "$BACKEND_MAIN"; then
    log_success "FRONTEND_DIR already points to frontend/dist"
else
    log_warn "Could not auto-patch FRONTEND_DIR. Please edit ${BACKEND_MAIN} manually"
    log_info "  and ensure FRONTEND_DIR points to the 'frontend/dist' folder."
fi

# ----------------------------------------------------------------------------
# 11. Create systemd service (skips if already exists with same content)
# ----------------------------------------------------------------------------
log_step "Checking systemd service '${SERVICE_NAME}'..."

CALIBRE_DIR=$(dirname "$(which calibre-debug)")
NODE_DIR=$(dirname "$(which node)")
SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SERVICE_PATH="${CALIBRE_DIR}:${UV_BIN%/*}:${NODE_DIR}:${SYSTEM_PATH}"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NEW_SERVICE_HASH=$(cat <<EOF | sha256sum | awk '{print $1}'
[Unit]
Description=IdeaHacks Bookshelf Kiosk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}/apps/backend
Environment="PATH=${SERVICE_PATH}"
Environment="PYTHONUNBUFFERED=1"
Environment="HOME=${SERVICE_HOME}"
ExecStart=${UV_BIN} run python main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
)

if [[ -f "$SERVICE_FILE" ]]; then
    OLD_SERVICE_HASH=$(sha256sum "$SERVICE_FILE" | awk '{print $1}')
    if [[ "$OLD_SERVICE_HASH" == "$NEW_SERVICE_HASH" ]]; then
        log_skip "Systemd service"
    else
        log_info "Updating systemd service..."
        cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=IdeaHacks Bookshelf Kiosk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}/apps/backend
Environment="PATH=${SERVICE_PATH}"
Environment="PYTHONUNBUFFERED=1"
Environment="HOME=${SERVICE_HOME}"
ExecStart=${UV_BIN} run python main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME"
        log_success "Systemd service updated"
    fi
else
    log_info "Creating systemd service..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=IdeaHacks Bookshelf Kiosk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}/apps/backend
Environment="PATH=${SERVICE_PATH}"
Environment="PYTHONUNBUFFERED=1"
Environment="HOME=${SERVICE_HOME}"
ExecStart=${UV_BIN} run python main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    log_success "Systemd service installed and enabled"
fi

# ----------------------------------------------------------------------------
# 12. USB / e-reader permissions
# ----------------------------------------------------------------------------
log_step "Setting up USB permissions for e-reader detection..."

usermod -aG plugdev "$SERVICE_USER" 2>/dev/null || true

UDEV_FILE="/etc/udev/rules.d/99-ereader.rules"
if [[ -f "$UDEV_FILE" ]]; then
    log_skip "USB udev rules"
else
    cat > "$UDEV_FILE" <<'UDEV'
# IdeaHacks — USB permissions for common e-readers
# Kobo
SUBSYSTEM=="usb", ATTR{idVendor}=="2237", GROUP="plugdev", MODE="0666"
# Amazon Kindle
SUBSYSTEM=="usb", ATTR{idVendor}=="1949", GROUP="plugdev", MODE="0666"
# Sony Reader
SUBSYSTEM=="usb", ATTR{idVendor}=="054c", GROUP="plugdev", MODE="0666"
# PocketBook
SUBSYSTEM=="usb", ATTR{idVendor}=="1f3a", GROUP="plugdev", MODE="0666"
# Bookeen
SUBSYSTEM=="usb", ATTR{idVendor}=="15c0", GROUP="plugdev", MODE="0666"
# Tolino / TrekStor
SUBSYSTEM=="usb", ATTR{idVendor}=="1f85", GROUP="plugdev", MODE="0666"
# Generic fallback for libusb hotplug
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", GROUP="plugdev", MODE="0664"
UDEV
    udevadm control --reload-rules
    udevadm trigger
    log_success "USB udev rules installed"
fi

# ----------------------------------------------------------------------------
# 13. Hostname & optional network tweaks
# ----------------------------------------------------------------------------
log_step "Checking hostname..."
if [[ "$(hostname)" != "ideahacks-kiosk" ]]; then
    hostnamectl set-hostname ideahacks-kiosk 2>/dev/null || true
    log_info "Hostname set to 'ideahacks-kiosk' (mDNS: ideahacks-kiosk.local)"
else
    log_skip "Hostname"
fi

# ----------------------------------------------------------------------------
# 14. Start service
# ----------------------------------------------------------------------------
log_step "Starting kiosk service..."
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    log_skip "Service already running"
else
    systemctl start "$SERVICE_NAME" || {
        log_warn "Service failed to start immediately. This is sometimes normal on first install."
        log_info "Checking service status..."
        systemctl status "$SERVICE_NAME" --no-pager || true
    }

    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log_success "Service is running!"
    else
        log_warn "Service is not active yet. It may need a reboot, or check:"
        log_info "  sudo journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
    fi
fi

# ----------------------------------------------------------------------------
# 15. Summary
# ----------------------------------------------------------------------------
PI_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -z "$PI_IP" ]]; then
    PI_IP="<your-pi-ip>"
fi

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║         IdeaHacks Kiosk installation complete!             ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Project directory:${NC}  ${INSTALL_DIR}"
echo -e "  ${BOLD}Service user:${NC}       ${SERVICE_USER}"
echo -e "  ${BOLD}Service name:${NC}       ${SERVICE_NAME}"
echo -e "  ${BOLD}Web UI URL:${NC}         http://${PI_IP}:5005"
echo -e "  ${BOLD}mDNS (Bonjour):${NC}     http://ideahacks-kiosk.local:5005"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo -e "    Start service:     ${CYAN}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "    Stop service:      ${CYAN}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo -e "    View logs:         ${CYAN}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "    Restart service:   ${CYAN}sudo systemctl restart ${SERVICE_NAME}${NC}"
echo -e "    Check status:      ${CYAN}sudo systemctl status ${SERVICE_NAME}${NC}"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo -e "    1. Open ${YELLOW}http://${PI_IP}:5005${NC} on your Android tablet."
echo -e "    2. Dock a Kobo / Kindle via USB — the kiosk should detect it."
echo -e "    3. If USB is not detected, try rebooting the Pi once: ${CYAN}sudo reboot${NC}"
echo ""
echo -e "  ${BOLD}If something goes wrong:${NC}"
echo -e "    • Check service logs:   ${CYAN}sudo journalctl -u ${SERVICE_NAME} -n 100 --no-pager${NC}"
echo -e "    • Verify Calibre:       ${CYAN}calibre-debug --version${NC}"
echo -e "    • Verify Python:        ${CYAN}cd ${INSTALL_DIR}/apps/backend && uv run python --version${NC}"
echo -e "    • List USB devices:     ${CYAN}lsusb${NC}"
echo -e "    • Test backend manually:${CYAN}cd ${INSTALL_DIR}/apps/backend && uv run python main.py${NC}"
echo ""
echo -e "  ${YELLOW}Note:${NC} Group changes (USB plugdev) may require a logout/login"
echo -e "        or a reboot to take full effect."
echo ""
