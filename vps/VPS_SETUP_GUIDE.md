# LinkedPilot v2 — VPS AI Server Setup Guide

## Your VPS Info

| Item | Value |
|------|-------|
| OS | AlmaLinux 8.10 |
| IP | 50.6.202.231 |
| RAM | 3.6 GB (2.4 GB free) |
| Disk | 99 GB (79 GB free) |
| Nginx | Running (ports 80, 443, 4568) |
| PostgreSQL | Running (127.0.0.1:5432) |
| PM2/Node | Running (port 3000) |
| Python | 3.6.8 (need 3.11) |
| Firewall | Disabled |

## What We Install (Kya lagayenge)

| Service | Location | Port |
|---------|----------|------|
| LinkedPilot AI | `/var/www/linkedpilot-ai/` | 8000 (internal) |
| Nginx config | `/etc/nginx/conf.d/linkedpilot.conf` | 8443 (SSL) |
| Ollama | system-wide | 11434 (internal) |
| Phi-3 Mini model | `~/.ollama/models/` | — |

## What We Do NOT Touch

```
/var/www/email1/               ← untouched
/var/www/email_verifier/       ← untouched
/var/www/emailverifierphp/     ← untouched
/var/www/misservicesinc.com/   ← untouched
/etc/nginx/conf.d/emailverifier.conf    ← untouched
/etc/nginx/conf.d/misservicesinc.conf   ← untouched
PostgreSQL on :5432            ← untouched
PM2/Node on :3000              ← untouched
firewalld                      ← stays disabled
```

---

## Step-by-Step Commands

### Step 1: Upload files to VPS

From your **laptop** (project root):

```bash
scp -r vps/ root@50.6.202.231:/root/linkedpilot-setup/
```

### Step 2: SSH into VPS and run setup

```bash
ssh root@50.6.202.231
cd /root/linkedpilot-setup
chmod +x setup_vps.sh
bash setup_vps.sh
```

This will:
- Install Python 3.11 (via dnf)
- Install Ollama + download phi3:mini (~2.3 GB)
- Create `/var/www/linkedpilot-ai/` with venv
- Generate self-signed SSL cert
- Add Nginx config on port 8443
- Install systemd service

### Step 3: Generate API key

```bash
python3.11 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output (64-char hex string).

### Step 4: Set API key on VPS

```bash
nano /var/www/linkedpilot-ai/.env
```

Paste:
```
VPS_API_KEY=<your-64-char-key>
```

### Step 5: Set your laptop IP in Nginx

```bash
# Find laptop IP (run on laptop)
curl -s ifconfig.me

# Edit on VPS
nano /etc/nginx/conf.d/linkedpilot.conf
```

Change:
```
allow CHANGE_TO_LAPTOP_IP;
```
To:
```
allow YOUR.LAPTOP.IP.HERE;
```

Then:
```bash
nginx -t && systemctl reload nginx
```

### Step 6: Start the AI server

```bash
systemctl start linkedpilot-ai
systemctl status linkedpilot-ai
```

### Step 7: Test

```bash
# From VPS
curl -k https://localhost:8443/health

# From laptop
curl -k https://50.6.202.231:8443/health
```

Expected response:
```json
{"status":"ok","model":"phi3:mini","uptime_seconds":5.2}
```

### Step 8: Set API key on Dashboard (laptop)

Edit `dashboard/.env`:
```
VPS_AI_URL=https://50.6.202.231:8443/api/suggest-comment
VPS_API_KEY=<same-64-char-key>
VPS_HEALTH_URL=https://50.6.202.231:8443/health
VPS_SSL_VERIFY=false
```

Then go to Dashboard Settings page → "Test Connection" → should show green.

---

## VPS Folder Structure After Setup

```
/var/www/
├── email1/                  ← existing (untouched)
├── email_verifier/          ← existing (untouched)
├── emailverifierphp/        ← existing (untouched)
├── misservicesinc.com/      ← existing (untouched)
└── linkedpilot-ai/          ← NEW
    ├── ai_server.py
    ├── .env
    └── venv/
        └── bin/uvicorn

/etc/nginx/conf.d/
├── emailverifier.conf       ← existing (untouched)
├── misservicesinc.conf      ← existing (untouched)
└── linkedpilot.conf         ← NEW (port 8443 only)

/etc/systemd/system/
└── linkedpilot-ai.service   ← NEW
```

---

## Troubleshooting

### Service not starting
```bash
journalctl -u linkedpilot-ai -n 50 --no-pager
systemctl status ollama
```

### Ollama not responding
```bash
systemctl restart ollama
ollama list
curl http://localhost:11434/api/generate -d '{"model":"phi3:mini","prompt":"hello","stream":false}'
```

### Nginx 403 (IP not whitelisted)
```bash
# Check current IP
curl -s ifconfig.me
# Update
nano /etc/nginx/conf.d/linkedpilot.conf
nginx -t && systemctl reload nginx
```

### HMAC auth fails (401)
```bash
# Keys must match exactly
cat /var/www/linkedpilot-ai/.env     # VPS key
# vs dashboard/.env                   # laptop key
```

---

## Useful Commands

```bash
# Manage services
systemctl start|stop|restart linkedpilot-ai
systemctl start|stop|restart ollama

# Logs
journalctl -u linkedpilot-ai -f
journalctl -u ollama -f

# Update ai_server.py
scp vps/ai_server.py root@50.6.202.231:/var/www/linkedpilot-ai/
ssh root@50.6.202.231 "chown linkedpilot:linkedpilot /var/www/linkedpilot-ai/ai_server.py && systemctl restart linkedpilot-ai"

# Check Ollama models
ollama list
ollama pull phi3:mini
```
