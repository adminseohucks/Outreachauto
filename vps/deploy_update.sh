#!/usr/bin/env bash
# -----------------------------------------------------------------------
# LinkedPilot VPS — Quick Deploy Script
# Updates ai_server.py + Nginx config on VPS and restarts services
#
# Usage:  bash vps/deploy_update.sh
# -----------------------------------------------------------------------
set -euo pipefail

VPS_IP="50.6.202.231"
VPS_USER="root"
APP_DIR="/var/www/linkedpilot-ai"
NGINX_CONF="/etc/nginx/conf.d/linkedpilot.conf"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== LinkedPilot VPS Deploy ==="
echo "VPS: ${VPS_USER}@${VPS_IP}"
echo ""

# 1. Upload ai_server.py
echo "[1/4] Uploading ai_server.py ..."
scp "${SCRIPT_DIR}/ai_server.py" "${VPS_USER}@${VPS_IP}:${APP_DIR}/ai_server.py"

# 2. Upload Nginx config
echo "[2/4] Uploading nginx_linkedpilot.conf ..."
scp "${SCRIPT_DIR}/nginx_linkedpilot.conf" "${VPS_USER}@${VPS_IP}:${NGINX_CONF}"

# 3. Fix ownership + restart services
echo "[3/4] Fixing permissions and restarting services ..."
ssh "${VPS_USER}@${VPS_IP}" << 'REMOTE'
chown linkedpilot:linkedpilot /var/www/linkedpilot-ai/ai_server.py
nginx -t && systemctl reload nginx
systemctl restart linkedpilot-ai
echo "Services restarted."
REMOTE

# 4. Health check
echo "[4/4] Checking VPS health ..."
sleep 2
curl -sk "https://${VPS_IP}:8443/health" | python3 -m json.tool 2>/dev/null || echo "Health check failed — check VPS logs."

echo ""
echo "=== Deploy Complete ==="
