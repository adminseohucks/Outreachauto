# LinkedPilot v2 — VPS AI Server Setup Guide

## Overview

The VPS AI server runs **Ollama + Phi-3 Mini** to intelligently select the best
predefined comment for each LinkedIn post. When the VPS is unreachable, the
dashboard falls back to random comment selection automatically.

### Architecture

```
Laptop (Dashboard)                    VPS (AlmaLinux 8)
┌─────────────────┐    HTTPS:8443    ┌──────────────────────────┐
│  Scheduler      │ ───────────────→ │  Nginx (SSL + IP filter) │
│  ai_comment.py  │  HMAC-SHA256     │     ↓ proxy :8000        │
│                 │ ←─────────────── │  FastAPI  ai_server.py   │
└─────────────────┘                  │     ↓ localhost:11434     │
                                     │  Ollama  phi3:mini       │
                                     └──────────────────────────┘
```

**Security layers:** HMAC-SHA256 auth → IP whitelist → SSL → rate limiting → fail2ban

---

## Prerequisites

| Item | Requirement |
|------|-------------|
| VPS | AlmaLinux 8 / CentOS 8 / Rocky Linux 8 (2+ GB RAM, 10+ GB disk) |
| Access | Root SSH access |
| Laptop IP | Your public IP (`curl -s ifconfig.me`) |

> **RAM Note:** Phi-3 Mini needs ~2 GB. If VPS has only 1 GB, the model will
> swap heavily. Consider a VPS with 2-4 GB RAM.

---

## Step 1: Upload VPS Files to Server

From your laptop (in the project root):

```bash
# Upload the entire vps/ folder to your server
scp -r vps/ root@YOUR_VPS_IP:/root/linkedpilot-setup/
```

---

## Step 2: Run the Setup Script

SSH into your VPS and run:

```bash
ssh root@YOUR_VPS_IP
cd /root/linkedpilot-setup
chmod +x setup_vps.sh
sudo bash setup_vps.sh
```

This installs:
- Python 3.11 + venv
- Ollama + phi3:mini model (~2.3 GB download)
- Nginx (reverse proxy on port 8443 with SSL)
- fail2ban (brute-force protection)
- Self-signed SSL certificate (10-year validity)
- Systemd service (`linkedpilot-ai`)
- Firewall rules (ports 22 + 8443 only)

---

## Step 3: Generate & Set API Key

```bash
# Generate a strong 64-char random key
python3.11 -c "import secrets; print(secrets.token_hex(32))"
```

**Copy that key**, then set it in TWO places:

### 3a. On the VPS

```bash
nano /opt/linkedpilot-ai/.env
```

```
VPS_API_KEY=<paste-your-64-char-key-here>
```

### 3b. On the Dashboard (laptop)

```bash
nano dashboard/.env
```

```
VPS_AI_URL=https://YOUR_VPS_IP:8443/api/suggest-comment
VPS_API_KEY=<paste-same-64-char-key-here>
VPS_HEALTH_URL=https://YOUR_VPS_IP:8443/health
VPS_SSL_VERIFY=false
```

> Both keys MUST match exactly. HMAC authentication will fail otherwise.

---

## Step 4: Set Your Laptop IP in Nginx

```bash
# Find your laptop's public IP
curl -s ifconfig.me

# Edit Nginx config on VPS
nano /etc/nginx/conf.d/linkedpilot.conf
```

Find this line:
```
allow CHANGE_TO_LAPTOP_IP;
```

Replace with your actual IP:
```
allow 103.XX.XX.XX;
```

Then reload:
```bash
nginx -t && systemctl reload nginx
```

---

## Step 5: Start the AI Server

```bash
systemctl start linkedpilot-ai
systemctl status linkedpilot-ai
```

Check logs:
```bash
journalctl -u linkedpilot-ai -f
```

---

## Step 6: Verify Everything Works

### From the VPS itself:
```bash
curl -k https://localhost:8443/health
```

Expected:
```json
{"status":"ok","model":"phi3:mini","uptime_seconds":42.5}
```

### From your laptop:
```bash
curl -k https://YOUR_VPS_IP:8443/health
```

### From the Dashboard UI:
1. Go to **Settings** page
2. Click **Test Connection**
3. Should show green status with latency

---

## Troubleshooting

### Connection refused / timeout

```bash
# Check if services are running
systemctl status linkedpilot-ai
systemctl status nginx
systemctl status ollama

# Check firewall
firewall-cmd --list-ports
# Should show: 8443/tcp

# Check Nginx is listening
ss -tlnp | grep 8443
```

### HMAC auth fails (401 errors)

```bash
# Verify keys match
cat /opt/linkedpilot-ai/.env       # VPS key
cat dashboard/.env                  # Dashboard key (must match)

# Check time sync (timestamp tolerance is 300 seconds)
date -u                            # VPS time
# vs your laptop's UTC time
```

### Ollama not responding

```bash
# Check Ollama service
systemctl status ollama
ollama list                        # Should show phi3:mini

# Test Ollama directly
curl http://localhost:11434/api/generate -d '{
  "model": "phi3:mini",
  "prompt": "Say hello",
  "stream": false
}'
```

### Nginx 403 Forbidden (IP not whitelisted)

```bash
# Check your current IP
curl -s ifconfig.me

# Update whitelist
nano /etc/nginx/conf.d/linkedpilot.conf
# Update the "allow" line
systemctl reload nginx
```

---

## Useful Commands

```bash
# Service management
systemctl start|stop|restart linkedpilot-ai
systemctl start|stop|restart ollama
systemctl start|stop|restart nginx

# View logs
journalctl -u linkedpilot-ai --since "1 hour ago"
journalctl -u ollama -f

# Check Ollama models
ollama list
ollama pull phi3:mini             # Re-pull if needed

# Disk usage
du -sh /usr/share/ollama          # Ollama models directory

# Update API key
nano /opt/linkedpilot-ai/.env
systemctl restart linkedpilot-ai
```

---

## How AI Comment Selection Works

When a comment campaign runs:

1. **Scheduler** picks up a comment action from the queue
2. **Browser** navigates to the lead's profile and extracts the latest post text
3. **AI Client** sends post text + all active predefined comments to VPS
4. **VPS AI** (Phi-3) analyzes the post and picks the most relevant comment
5. **Fallback:** If VPS is unreachable, a random predefined comment is used
6. **Browser** types the selected comment with human-like keystrokes

The entire flow is automated — no manual intervention needed after campaign start.

---

## Updating the AI Server

When you update `ai_server.py`:

```bash
# From laptop
scp vps/ai_server.py root@YOUR_VPS_IP:/opt/linkedpilot-ai/ai_server.py

# On VPS
chown linkedpilot:linkedpilot /opt/linkedpilot-ai/ai_server.py
systemctl restart linkedpilot-ai
```
