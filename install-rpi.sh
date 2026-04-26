#!/bin/bash
#
# ============================================================================
# IdeaHacks 2026 — Raspberry Pi 4 Kiosk Installation Script
# ============================================================================
# This script installs everything needed to run the Ideahacks bookshelf kiosk
# on a Raspberry Pi 4 from a fresh Raspberry Pi OS (Debian Bookworm) image.
#
# WHAT IT DOES:
#   1. Updates system packages and installs build dependencies
#   2. Installs Calibre e-book management (required by the backend)
#   3. Installs Node.js 22 + pnpm (required by the frontend build)
#   4. Installs uv (modern Python package manager)
#   5. Installs Python 3.14 (required by backend pyproject.toml)
#   6. Clones or uses the existing ideahacks-2026 repository
#   7. Builds the frontend for production
#   8. Installs Python backend dependencies
#   9. Patches Flask to serve the built frontend from frontend/dist
#  10. Creates a systemd service so the kiosk starts on boot
#  11. Sets up USB permissions for e-reader hotplug detection
#
# USAGE:
#   1. Flash Raspberry Pi OS (64-bit) to your SD card, boot, and enable SSH.
#   2. Copy this script to the Pi:
#        scp install-rpi.sh pi@raspberrypi.local:~/
#   3. SSH in and run it:
#        ssh pi@raspberrypi.local
#        sudo bash install-rpi.sh
#   4. When finished, open http://<pi-ip>:5005 on your tablet.
#
# TROUBLESHOOTING:
#   - If the script fails, it will tell you exactly which step failed.
#   - Check logs after install:  sudo journalctl -u ideahacks -f
#   - Verify Calibre:             calibre-debug --version
#   - Verify Python:              cd apps/backend && uv run python --version
#   - Check USB devices:          lsusb
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
  5. For Calibre issues, try:  sudo apt install -y calibre calibre-bin
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
        # Fall back to common Pi usernames
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
# 1. System update & base dependencies
# ----------------------------------------------------------------------------
log_step "Updating system packages (this may take a few minutes)..."
apt-get update
apt-get upgrade -y

log_step "Installing system dependencies..."
apt-get install -y \
    git curl wget ca-certificates gnupg \
    build-essential pkg-config \
    libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev \
    libncursesw5-dev xz-utils tk-dev \
    libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    libjpeg-dev libpng-dev libfreetype6-dev \
    libusb-1.0-0-dev libusb-1.0-0 \
    libmtp-dev libudisks2-dev \
    systemd || {
        log_error "apt install failed. Trying with --fix-missing..."
        apt-get install -f -y
        apt-get install -y git curl wget build-essential libusb-1.0-0-dev libusb-1.0-0 systemd
    }
log_success "System dependencies installed"

# ----------------------------------------------------------------------------
# 2. Calibre  (official installer — newer, better e-reader support)
# ----------------------------------------------------------------------------
log_step "Installing Calibre (official installer)..."

if ! command -v calibre-debug &>/dev/null; then
    wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | sh /dev/stdin install_dir=/opt/calibre isolated=y
    ln -sf /opt/calibre/calibre-debug /usr/local/bin/calibre-debug || true
    ln -sf /opt/calibre/calibredb     /usr/local/bin/calibredb     || true
    ln -sf /opt/calibre/ebook-convert /usr/local/bin/ebook-convert || true
fi

# Fallback to distro package only if the official installer didn't land
if ! command -v calibre-debug &>/dev/null; then
    log_warn "Official installer failed. Falling back to distribution Calibre..."
    apt-get install -y calibre || true
fi

if ! command -v calibre-debug &>/dev/null; then
    log_fatal "Calibre installation failed. The backend cannot work without calibre-debug.\n   Try manually: sudo apt update && sudo apt install -y calibre"
    exit 1
fi

CALIBRE_VERSION=$(calibre-debug --version 2>/dev/null || echo "unknown")
log_success "Calibre installed: ${CALIBRE_VERSION}"
log_info "calibre-debug path: $(which calibre-debug)"
log_info "calibredb   path: $(which calibredb)"

# ----------------------------------------------------------------------------
# 3. Node.js & pnpm
# ----------------------------------------------------------------------------
log_step "Installing Node.js ${NODE_MAJOR}..."
if ! command -v node &>/dev/null || [[ "$(node -v | cut -d'v' -f2 | cut -d'.' -f1)" != "$NODE_MAJOR" ]]; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
    apt-get install -y nodejs
fi
log_success "Node.js: $(node -v)"

log_step "Installing pnpm..."
PNPM_HOME="${SERVICE_HOME}/.local/share/pnpm"
export PNPM_HOME
export PATH="${PNPM_HOME}:$PATH"

if ! command -v pnpm &>/dev/null; then
    # Install pnpm for the service user
    su - "$SERVICE_USER" -c 'curl -fsSL https://get.pnpm.io/install.sh | ENV="$HOME/.bashrc" sh -'
    # Ensure it is in PATH for the remainder of this script
    if [[ -f "${SERVICE_HOME}/.bashrc" ]]; then
        grep -q 'export PNPM_HOME=' "${SERVICE_HOME}/.bashrc" || true
    fi
fi

if ! command -v pnpm &>/dev/null; then
    # Fallback: install globally via npm
    npm install -g pnpm
fi

if ! command -v pnpm &>/dev/null; then
    log_fatal "pnpm installation failed.\n   Try manually: curl -fsSL https://get.pnpm.io/install.sh | sh -"
    exit 1
fi
log_success "pnpm: $(pnpm -v)"

# ----------------------------------------------------------------------------
# 4. uv (Python toolchain)
# ----------------------------------------------------------------------------
log_step "Installing uv (Python toolchain)..."
UV_BIN=""
if command -v uv &>/dev/null; then
    UV_BIN=$(which uv)
else
    # Install into a temporary location then move system-wide so everyone can use it
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if [[ -f "$HOME/.cargo/bin/uv" ]]; then
        cp "$HOME/.cargo/bin/uv" /usr/local/bin/uv
        chmod +x /usr/local/bin/uv
    elif [[ -f "$HOME/.local/bin/uv" ]]; then
        cp "$HOME/.local/bin/uv" /usr/local/bin/uv
        chmod +x /usr/local/bin/uv
    fi
fi

if ! command -v uv &>/dev/null; then
    log_fatal "uv installation failed.\n   Try manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

UV_BIN=$(which uv)
log_success "uv: $(${UV_BIN} --version)"
log_info "uv binary: ${UV_BIN}"

# ----------------------------------------------------------------------------
# 5. Python ${PYTHON_VERSION}
# ----------------------------------------------------------------------------
log_step "Installing Python ${PYTHON_VERSION} via uv..."
if ! ${UV_BIN} python install "${PYTHON_VERSION}" 2>/dev/null; then
    log_warn "uv python install ${PYTHON_VERSION} failed. Trying '3.14.0' explicitly..."
    if ! ${UV_BIN} python install 3.14.0 2>/dev/null; then
        log_fatal "Could not install Python ${PYTHON_VERSION} via uv.\n   uv may not yet provide a prebuilt ${PYTHON_VERSION} for ${ARCH}.\n   Options:\n     1. Change 'requires-python' in apps/backend/pyproject.toml to '>=3.11' and retry.\n     2. Build Python ${PYTHON_VERSION} from source (takes ~30 min on a Pi 4)."
        exit 1
    fi
fi
log_success "Python ${PYTHON_VERSION} ready"

# ----------------------------------------------------------------------------
# 6. Clone or verify repository
# ----------------------------------------------------------------------------
if [[ "$USING_LOCAL_REPO" -eq 0 ]]; then
    log_step "Cloning repository..."
    if [[ -z "$REPO_URL" ]]; then
        log_fatal "REPO_URL is empty and this script is not inside the repo.\n   Please set REPO_URL at the top of this script, e.g.:\n   REPO_URL=\"https://github.com/yourname/ideahacks-2026.git\""
        exit 1
    fi
    rm -rf "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$INSTALL_DIR"
    log_success "Repository cloned to ${INSTALL_DIR}"
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
# 7. JavaScript dependencies & frontend build
# ----------------------------------------------------------------------------
log_step "Installing JavaScript dependencies..."
cd "$INSTALL_DIR"
su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}' && '${PNPM_HOME}/pnpm' install" || \
    su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}' && pnpm install"
log_success "JavaScript dependencies installed"

log_step "Building frontend for production..."
su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}' && '${PNPM_HOME}/pnpm' build" || \
    su - "$SERVICE_USER" -c "cd '${INSTALL_DIR}' && pnpm build"

# Verify the dist folder was produced
if [[ ! -d "${INSTALL_DIR}/apps/frontend/dist" ]]; then
    log_fatal "Frontend build did not produce apps/frontend/dist.\n   Check the build output above for TypeScript or Vite errors."
    exit 1
fi
log_success "Frontend built successfully"

# ----------------------------------------------------------------------------
# 8. Python backend dependencies
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
# 9. Patch Flask to serve built frontend from frontend/dist
# ----------------------------------------------------------------------------
log_step "Configuring Flask to serve production frontend..."
BACKEND_MAIN="${INSTALL_DIR}/apps/backend/main.py"

# Show the user what the original line looks like
log_info "Original FRONTEND_DIR in main.py:"
grep -n 'FRONTEND_DIR = Path' "$BACKEND_MAIN" | head -n 1 || true

if grep -q 'FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"' "$BACKEND_MAIN"; then
    sed -i 's|FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"|FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"|' "$BACKEND_MAIN"
    log_success "Patched FRONTEND_DIR → frontend/dist"
elif grep -q 'FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"' "$BACKEND_MAIN"; then
    log_success "FRONTEND_DIR already points to frontend/dist"
else
    log_warn "Could not auto-patch FRONTEND_DIR. Please edit ${BACKEND_MAIN} manually"
    log_info "  and ensure FRONTEND_DIR points to the 'frontend/dist' folder."
fi

# ----------------------------------------------------------------------------
# 10. Create systemd service
# ----------------------------------------------------------------------------
log_step "Creating systemd service '${SERVICE_NAME}'..."

# Build a PATH that includes everything the backend needs
CALIBRE_DIR=$(dirname "$(which calibre-debug)")
NODE_DIR=$(dirname "$(which node)")
SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SERVICE_PATH="${CALIBRE_DIR}:${UV_BIN%/*}:${NODE_DIR}:${PNPM_HOME}:${SYSTEM_PATH}"

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
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
# If you want to store the Calibre library somewhere else (e.g. external SSD):
# Environment="IDEAHACKS_CALIBRE_LIBRARY=/mnt/bookshelf/calibre-library"
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

# ----------------------------------------------------------------------------
# 11. USB / e-reader permissions
# ----------------------------------------------------------------------------
log_step "Setting up USB permissions for e-reader detection..."

# Add user to plugdev group for libusb access
usermod -aG plugdev "$SERVICE_USER" 2>/dev/null || true

# Create udev rules for common e-reader vendors
UDEV_FILE="/etc/udev/rules.d/99-ereader.rules"
if [[ ! -f "$UDEV_FILE" ]]; then
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
else
    log_info "USB udev rules already exist at ${UDEV_FILE}"
fi

# ----------------------------------------------------------------------------
# 12. Hostname & optional network tweaks
# ----------------------------------------------------------------------------
log_step "Setting hostname..."
if [[ "$(hostname)" != "ideahacks-kiosk" ]]; then
    hostnamectl set-hostname ideahacks-kiosk 2>/dev/null || true
    log_info "Hostname set to 'ideahacks-kiosk' (mDNS: ideahacks-kiosk.local)"
fi

# ----------------------------------------------------------------------------
# 13. Start service
# ----------------------------------------------------------------------------
log_step "Starting kiosk service..."
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

# ----------------------------------------------------------------------------
# 14. Summary
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
