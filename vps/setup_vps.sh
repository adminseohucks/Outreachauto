#!/usr/bin/env bash
# ============================================================================
# LinkedPilot v2 - VPS Setup Script for AlmaLinux 8
# Installs Python 3.11, Ollama + Phi-3, Nginx, fail2ban, and configures
# the AI comment server as a systemd service.
#
# Usage: sudo bash setup_vps.sh
# ============================================================================

set -euo pipefail

APP_DIR="/opt/linkedpilot-ai"
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
# 1. System packages
# ---------------------------------------------------------------------------

install_system_packages() {
    info "Installing system packages..."

    # Enable EPEL and PowerTools/CRB for extra packages
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

    # Nginx
    if ! command -v nginx &>/dev/null; then
        info "Installing Nginx..."
        dnf install -y nginx
    else
        ok "Nginx already installed."
    fi

    # fail2ban
    if ! command -v fail2ban-server &>/dev/null; then
        info "Installing fail2ban..."
        dnf install -y fail2ban
        systemctl enable --now fail2ban
    else
        ok "fail2ban already installed."
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

    # Pull model (idempotent - Ollama skips if already present)
    info "Pulling phi3:mini model (this may take a while)..."
    ollama pull phi3:mini

    ok "Ollama + phi3:mini ready."
}

# ---------------------------------------------------------------------------
# 3. Application user and directory
# ---------------------------------------------------------------------------

setup_app_dir() {
    info "Setting up application directory..."

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
        uvicorn[standard] \
        httpx \
        python-dotenv \
        pydantic

    ok "Python dependencies installed."
}

# ---------------------------------------------------------------------------
# 5. Copy application files
# ---------------------------------------------------------------------------

copy_app_files() {
    info "Copying application files..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    cp "$SCRIPT_DIR/ai_server.py" "$APP_DIR/ai_server.py"

    # Create .env from template if it does not already exist
    if [[ ! -f "$APP_DIR/.env" ]]; then
        cp "$SCRIPT_DIR/.env.example" "$APP_DIR/.env"
        warn ".env created from template - you MUST set VPS_API_KEY before starting."
    else
        ok ".env already exists (not overwritten)."
    fi

    chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
    chmod 600 "$APP_DIR/.env"

    ok "Application files in place."
}

# ---------------------------------------------------------------------------
# 6. Self-signed SSL certificate
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
        -subj "/CN=linkedpilot-ai/O=LinkedPilot/C=US"

    chmod 600 "$SSL_DIR/linkedpilot.key"
    chmod 644 "$SSL_DIR/linkedpilot.crt"

    ok "Self-signed certificate created at $SSL_DIR/"
}

# ---------------------------------------------------------------------------
# 7. Nginx configuration
# ---------------------------------------------------------------------------

setup_nginx() {
    info "Configuring Nginx..."

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    cp "$SCRIPT_DIR/nginx_linkedpilot.conf" /etc/nginx/conf.d/linkedpilot.conf

    # Remove default server block if it exists, to avoid port conflicts
    if [[ -f /etc/nginx/conf.d/default.conf ]]; then
        mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.bak
        warn "Renamed default.conf to default.conf.bak"
    fi

    nginx -t || err "Nginx configuration test failed!"
    systemctl enable --now nginx
    systemctl reload nginx

    ok "Nginx configured and running."
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

    ok "Systemd service installed (not started yet - set API key first)."
}

# ---------------------------------------------------------------------------
# 9. Firewall
# ---------------------------------------------------------------------------

setup_firewall() {
    info "Configuring firewall..."

    if ! command -v firewall-cmd &>/dev/null; then
        warn "firewalld not found, skipping firewall setup."
        return
    fi

    systemctl enable --now firewalld

    # Only allow SSH (22) and our API port (8443)
    firewall-cmd --permanent --add-service=ssh         2>/dev/null || true
    firewall-cmd --permanent --add-port=8443/tcp       2>/dev/null || true

    # Remove common default services we do not need
    firewall-cmd --permanent --remove-service=dhcpv6-client 2>/dev/null || true
    firewall-cmd --permanent --remove-service=cockpit       2>/dev/null || true

    firewall-cmd --reload

    ok "Firewall configured: ports 22 (SSH) and 8443 (API) open."
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    echo "=============================================="
    echo "  LinkedPilot v2 - VPS Setup (AlmaLinux 8)"
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
    setup_firewall

    echo ""
    echo "=============================================="
    echo "  Setup Complete!"
    echo "=============================================="
    echo ""
    echo "  NEXT STEPS:"
    echo ""
    echo "  1. Set your API key:"
    echo "       nano /opt/linkedpilot-ai/.env"
    echo "       # Change VPS_API_KEY=changeme_to_a_random_64char_secret"
    echo ""
    echo "  2. Update the Nginx IP whitelist:"
    echo "       nano /etc/nginx/conf.d/linkedpilot.conf"
    echo "       # Replace CHANGE_TO_LAPTOP_IP with your laptop's public IP"
    echo "       systemctl reload nginx"
    echo ""
    echo "  3. Start the AI server:"
    echo "       systemctl start linkedpilot-ai"
    echo "       systemctl status linkedpilot-ai"
    echo ""
    echo "  4. Test the health endpoint:"
    echo "       curl -k https://localhost:8443/health"
    echo ""
    echo "=============================================="
}

main "$@"
