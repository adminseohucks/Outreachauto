#!/usr/bin/env bash
# ============================================================================
# LinkedPilot v2 - VPS Setup Script for AlmaLinux 8
#
# YOUR VPS:
#   OS       : AlmaLinux 8.10
#   RAM      : 3.6 GB (Ollama ke liye enough)
#   Nginx    : Already running (80, 443, 4568) — we only ADD a config
#   Postgres : Running on 127.0.0.1:5432 — NOT touched
#   PM2/Node : Running on :3000 — NOT touched
#   Firewall : Disabled — we do NOT enable it (would break existing services)
#   Python   : 3.6.8 only — we install 3.11
#   Ollama   : Not installed — we install it
#
# App Location: /var/www/linkedpilot-ai/
# Port        : 8443 (free)
#
# Usage: sudo bash setup_vps.sh
# ============================================================================

set -euo pipefail

APP_DIR="/var/www/linkedpilot-ai"
APP_USER="linkedpilot"
SSL_DIR="/etc/nginx/ssl"
VENV_DIR="${APP_DIR}/venv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { echo -e "\n\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m    $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
err()   { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || err "This script must be run as root (use sudo)."
}

# ---------------------------------------------------------------------------
# 1. System packages (Python 3.11 only — Nginx already installed)
# ---------------------------------------------------------------------------

install_system_packages() {
    info "Installing system packages..."

    # Enable EPEL and PowerTools for Python 3.11
    if ! dnf repolist enabled | grep -q epel; then
        dnf install -y epel-release
    fi
    dnf config-manager --set-enabled powertools 2>/dev/null \
        || dnf config-manager --set-enabled crb 2>/dev/null \
        || true

    # Python 3.11
    if ! command -v python3.11 &>/dev/null; then
        info "Installing Python 3.11..."
        dnf install -y python3.11 python3.11-pip python3.11-devel
    else
        ok "Python 3.11 already installed."
    fi

    # Verify Nginx is already running (we don't install it)
    if command -v nginx &>/dev/null; then
        ok "Nginx already installed — will NOT reinstall."
    else
        err "Nginx not found! This script expects Nginx to be already running."
    fi

    ok "System packages ready."
}

# ---------------------------------------------------------------------------
# 2. Ollama + Phi-3 model
# ---------------------------------------------------------------------------

install_ollama() {
    info "Setting up Ollama..."

    if ! command -v ollama &>/dev/null; then
        info "Installing Ollama..."
        curl -fsSL https://ollama.com/install.sh | sh
    else
        ok "Ollama already installed."
    fi

    # Ensure the Ollama service is running
    systemctl enable --now ollama

    # Pull model (idempotent — Ollama skips if already present)
    info "Pulling phi3:mini model (this may take a while ~2.3 GB)..."
    ollama pull phi3:mini

    ok "Ollama + phi3:mini ready."
}

# ---------------------------------------------------------------------------
# 3. Application user and directory (/var/www/linkedpilot-ai/)
# ---------------------------------------------------------------------------

setup_app_dir() {
    info "Setting up application directory at ${APP_DIR}..."

    # Create service user (no login shell)
    if ! id "$APP_USER" &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
        ok "Created user: $APP_USER"
    else
        ok "User $APP_USER already exists."
    fi

    mkdir -p "$APP_DIR"
    ok "Application directory: $APP_DIR"
}

# ---------------------------------------------------------------------------
# 4. Python virtual environment + dependencies
# ---------------------------------------------------------------------------

setup_python_venv() {
    info "Setting up Python virtual environment..."

    if [[ ! -d "$VENV_DIR" ]]; then
        python3.11 -m venv "$VENV_DIR"
    else
        ok "Venv already exists."
    fi

    "$VENV_DIR/bin/pip" install --upgrade pip

    "$VENV_DIR/bin/pip" install \
        fastapi \
        'uvicorn[standard]' \
        httpx \
        python-dotenv \
        pydantic

    ok "Python dependencies installed."
}

# ---------------------------------------------------------------------------
# 5. Copy application files
# ---------------------------------------------------------------------------

copy_app_files() {
    info "Copying application files to ${APP_DIR}..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    cp "$SCRIPT_DIR/ai_server.py" "$APP_DIR/ai_server.py"

    # Create .env from template if it does not already exist
    if [[ ! -f "$APP_DIR/.env" ]]; then
        cp "$SCRIPT_DIR/.env.example" "$APP_DIR/.env"
        warn ".env created from template — you MUST set VPS_API_KEY before starting."
    else
        ok ".env already exists (not overwritten)."
    fi

    chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
    chmod 600 "$APP_DIR/.env"

    ok "Application files in place at $APP_DIR"
}

# ---------------------------------------------------------------------------
# 6. Self-signed SSL certificate for port 8443
# ---------------------------------------------------------------------------

setup_ssl() {
    info "Setting up self-signed SSL certificate..."

    mkdir -p "$SSL_DIR"

    if [[ -f "$SSL_DIR/linkedpilot.crt" && -f "$SSL_DIR/linkedpilot.key" ]]; then
        ok "SSL certificate already exists."
        return
    fi

    openssl req -x509 -nodes -days 3650 \
        -newkey rsa:2048 \
        -keyout "$SSL_DIR/linkedpilot.key" \
        -out "$SSL_DIR/linkedpilot.crt" \
        -subj "/CN=linkedpilot-ai/O=LinkedPilot/C=IN"

    chmod 600 "$SSL_DIR/linkedpilot.key"
    chmod 644 "$SSL_DIR/linkedpilot.crt"

    ok "Self-signed certificate created at $SSL_DIR/"
}

# ---------------------------------------------------------------------------
# 7. Nginx — ADD config (do NOT touch existing configs)
# ---------------------------------------------------------------------------

setup_nginx() {
    info "Adding LinkedPilot Nginx config..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Show existing configs for awareness
    info "Existing Nginx configs (will NOT be modified):"
    ls -1 /etc/nginx/conf.d/*.conf 2>/dev/null || true

    # Only add our config
    cp "$SCRIPT_DIR/nginx_linkedpilot.conf" /etc/nginx/conf.d/linkedpilot.conf
    ok "Added /etc/nginx/conf.d/linkedpilot.conf (port 8443)"

    # Test config — if it fails, remove our file and abort
    if ! nginx -t 2>&1; then
        rm -f /etc/nginx/conf.d/linkedpilot.conf
        err "Nginx config test FAILED! Removed linkedpilot.conf. Fix and retry."
    fi

    systemctl reload nginx
    ok "Nginx reloaded — port 8443 active. Existing sites untouched."
}

# ---------------------------------------------------------------------------
# 8. Systemd service
# ---------------------------------------------------------------------------

setup_systemd() {
    info "Setting up systemd service..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    cp "$SCRIPT_DIR/linkedpilot-ai.service" /etc/systemd/system/linkedpilot-ai.service
    systemctl daemon-reload
    systemctl enable linkedpilot-ai

    ok "Systemd service installed (not started yet — set API key first)."
}

# ---------------------------------------------------------------------------
# Main (NO firewall step — firewalld is disabled on this VPS)
# ---------------------------------------------------------------------------

main() {
    echo "=============================================="
    echo "  LinkedPilot v2 — VPS Setup (AlmaLinux 8)"
    echo "  Location: /var/www/linkedpilot-ai/"
    echo "  Port:     8443"
    echo "=============================================="

    require_root

    install_system_packages
    install_ollama
    setup_app_dir
    setup_python_venv
    copy_app_files
    setup_ssl
    setup_nginx
    setup_systemd

    echo ""
    echo "=============================================="
    echo "  Setup Complete!"
    echo "=============================================="
    echo ""
    echo "  YOUR VPS FOLDER STRUCTURE:"
    echo "    /var/www/linkedpilot-ai/"
    echo "    ├── ai_server.py"
    echo "    ├── .env            ← API key set karo"
    echo "    └── venv/"
    echo ""
    echo "  EXISTING SERVICES — UNTOUCHED:"
    echo "    /var/www/email1/"
    echo "    /var/www/email_verifier/"
    echo "    /var/www/emailverifierphp/"
    echo "    /var/www/misservicesinc.com/"
    echo ""
    echo "  NEXT STEPS:"
    echo ""
    echo "  1. API key generate + set karo:"
    echo "       python3.11 -c \"import secrets; print(secrets.token_hex(32))\""
    echo "       nano /var/www/linkedpilot-ai/.env"
    echo ""
    echo "  2. Nginx IP whitelist update karo:"
    echo "       nano /etc/nginx/conf.d/linkedpilot.conf"
    echo "       # CHANGE_TO_LAPTOP_IP → apna public IP dalo"
    echo "       systemctl reload nginx"
    echo ""
    echo "  3. Start karo:"
    echo "       systemctl start linkedpilot-ai"
    echo "       systemctl status linkedpilot-ai"
    echo ""
    echo "  4. Test karo:"
    echo "       curl -k https://localhost:8443/health"
    echo ""
    echo "=============================================="
}

main "$@"
