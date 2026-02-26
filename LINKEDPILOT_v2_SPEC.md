# LinkedPilot v2 — HYBRID BUILD SPECIFICATION
# OpenOutreach (base) + Custom Dashboard + Like/Comment Engine

> **Version:** 2.0 (FINAL)  
> **Date:** February 25, 2026  
> **Approach:** Hybrid — Use OpenOutreach for search/connect + Build custom dashboard/like/comment on top  
> **For:** Claude Code — Read this ENTIRE document before writing ANY code

---

## TABLE OF CONTENTS

1. [Project Overview (Hybrid Approach)](#1-project-overview)
2. [What OpenOutreach Handles vs What We Build](#2-responsibility-split)
3. [Architecture Diagram](#3-architecture)
4. [VPS Security — 5 Layer Protection](#4-vps-security)
5. [Laptop Setup Requirements](#5-laptop-setup)
6. [VPS Setup Requirements](#6-vps-setup)
7. [Database Strategy (Shared + Extended)](#7-database-strategy)
8. [Custom Dashboard Features](#8-custom-dashboard-features)
9. [Like Engine Specification](#9-like-engine)
10. [Comment Engine + VPS AI Specification](#10-comment-engine)
11. [CSV Import/Export Specification](#11-csv-import-export)
12. [Rate Limiting & Human Simulation](#12-rate-limiting)
13. [Project Folder Structure](#13-folder-structure)
14. [Dashboard UI Pages](#14-dashboard-ui-pages)
15. [Launcher (No .bat files)](#15-launcher)
16. [Portability & Export](#16-portability)
17. [Build Order (Phase by Phase)](#17-build-order)
18. [Critical Rules](#18-critical-rules)

---

## 1. PROJECT OVERVIEW

### What is LinkedPilot v2?

A **hybrid LinkedIn automation system** that combines:
- **OpenOutreach** (existing open-source tool) → handles search, scrape, qualify, connect, message
- **Custom Dashboard** (we build) → GUI for office team, filters, CSV export/import, named lists
- **Like Engine** (we build) → likes latest posts of leads
- **Comment Engine** (we build) → AI-selected comments on lead posts
- **VPS AI Server** (we build) → Ollama + Phi-3 for intelligent comment selection

### Golden Rule:
```
LINKEDIN ACTIONS → Always from LAPTOP (Indian IP, user's Chrome)
AI PROCESSING    → Always on VPS (USA IP, never touches LinkedIn)
COMMUNICATION    → Laptop ↔ VPS via SECURE encrypted API only
```

---

## 2. RESPONSIBILITY SPLIT

### OpenOutreach handles (ALREADY BUILT — we don't modify):

| Feature | How it works |
|---------|-------------|
| LinkedIn Search | User gives search URL in YAML → daemon scrapes all results |
| Profile Scraping | Voyager API extracts name, title, company, location |
| AI Qualification | Bayesian ML scores each lead (good/bad fit) |
| Connection Requests | Sends connect with AI-personalized messages |
| Follow-up Messages | Sends message after connection accepted |
| Rate Limiting | Configurable daily/weekly limits for connect/message |
| Stealth Browser | Playwright + stealth plugins, human-like delays |
| CRM Database | SQLite (crm.db) stores all leads permanently |
| Django Admin | localhost:8000/admin/ for OpenOutreach management |

### We Build (Custom LinkedPilot Dashboard):

| Feature | What it does |
|---------|-------------|
| **Web Dashboard** | localhost:8080, simple GUI for office team |
| **Lead Browser** | Read OpenOutreach's crm.db, show all leads with filters |
| **Filters** | Filter by: date, job title, company, status, location |
| **CSV Export** | One-click export filtered leads to CSV |
| **CSV Import** | Upload CSV to create new lead lists for like/comment |
| **Named Lists** | Create named lists like "Mumbai CEOs", "Delhi Marketers" |
| **Like Engine** | Like latest post of leads in a list (100/day, 300/week) |
| **Comment Engine** | AI-selected comment on lead posts (50/day, 200/week) |
| **Activity Logs** | Track all like/comment actions with timestamps |
| **Settings** | Configure limits, delays, VPS connection, work hours |

---

## 3. ARCHITECTURE

```
┌──────────────────────────────────────────────────────────────────┐
│  LAPTOP (Windows, Indian IP) — ALL LinkedIn actions here         │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  OPENOUTREACH (existing, running as-is)                  │   │
│  │  ├── Daemon: Search → Scrape → Qualify → Connect → Msg  │   │
│  │  ├── Playwright + Stealth (YOUR Chrome browser)          │   │
│  │  ├── Django Admin: localhost:8000/admin/                  │   │
│  │  ├── VNC viewer: localhost:5900                           │   │
│  │  └── SQLite: crm.db ◄──── SHARED DB ────┐               │   │
│  └──────────────────────────────────────────┼───────────────┘   │
│                                             │                    │
│  ┌──────────────────────────────────────────▼───────────────┐   │
│  │  LINKEDPILOT DASHBOARD (we build this)                   │   │
│  │  ├── FastAPI: localhost:8080                              │   │
│  │  ├── READS crm.db (OpenOutreach's leads data)            │   │
│  │  ├── OWN DB: linkedpilot.db (lists, comments, logs)      │   │
│  │  ├── Lead Browser + Filters + CSV Export                 │   │
│  │  ├── CSV Import → Named Lists                            │   │
│  │  ├── Like Engine (uses Playwright, YOUR Chrome)          │   │
│  │  └── Comment Engine (uses Playwright + VPS AI)           │   │
│  └────────────────────────────────┬─────────────────────────┘   │
│                                   │                              │
│  LinkedIn sees: Indian IP ✅ Real Chrome ✅                       │
└───────────────────────────────────┼──────────────────────────────┘
                                    │
                    ╔═══════════════╧═══════════════╗
                    ║  SECURE ENCRYPTED TUNNEL       ║
                    ║  5 LAYERS OF PROTECTION        ║
                    ║  (see Section 4)               ║
                    ╚═══════════════╤═══════════════╝
                                    │
┌───────────────────────────────────▼──────────────────────────────┐
│  VPS (AlmaLinux 8, USA IP) — ONLY AI processing here            │
│  4GB RAM, 2 CPU, No GPU                                         │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  LAYER 5: Nginx Reverse Proxy                            │   │
│  │  ├── SSL/TLS encryption (HTTPS port 8443)                │   │
│  │  ├── IP Whitelist (ONLY your laptop IP allowed)          │   │
│  │  ├── Rate limiting (30 requests/minute max)              │   │
│  │  └── Block all other traffic                             │   │
│  │           │                                               │   │
│  │  LAYER 4: API Authentication                              │   │
│  │  ├── X-API-Key header required                           │   │
│  │  ├── X-Request-Timestamp (reject old requests)           │   │
│  │  └── HMAC Signature verification                         │   │
│  │           │                                               │   │
│  │  LAYER 3: FastAPI AI Server                               │   │
│  │  ├── POST /api/suggest-comment                           │   │
│  │  ├── GET /api/health                                     │   │
│  │  ├── Input validation & sanitization                     │   │
│  │  └── Request logging                                     │   │
│  │           │                                               │   │
│  │  LAYER 2: Ollama AI Engine                                │   │
│  │  ├── Phi-3 Mini (3.8B) model                             │   │
│  │  ├── Listens ONLY on localhost:11434                      │   │
│  │  └── NOT exposed to internet                             │   │
│  │           │                                               │   │
│  │  LAYER 1: OS Firewall                                     │   │
│  │  ├── ONLY port 8443 open (for API)                       │   │
│  │  ├── SSH port 22 (for admin only)                        │   │
│  │  └── ALL other ports CLOSED                              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  LinkedIn NEVER touches this server ✅                           │
│  Only receives: post text → returns: best comment               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. VPS SECURITY — 5 LAYER PROTECTION

### LAYER 1: OS Firewall (firewalld)
```bash
# ONLY these ports open:
Port 8443  → API (for laptop communication)
Port 22    → SSH (for admin, key-based only)

# EVERYTHING else CLOSED
# No port 80, no port 443, no port 11434 (Ollama stays internal)
```

### LAYER 2: Ollama Isolation
```bash
# Ollama listens ONLY on localhost — NOT accessible from internet
# Even if someone hacks Nginx, they can't directly reach Ollama
# Configuration:
OLLAMA_HOST=127.0.0.1:11434
```

### LAYER 3: FastAPI Input Validation
```python
# Every request validated:
# - post_text: max 5000 chars, sanitized
# - comments: max 50 items, each max 300 chars
# - No SQL/code injection possible
# - Request body size limit: 50KB max
# - All errors logged with IP and timestamp
```

### LAYER 4: API Authentication (3 checks)
```python
# CHECK 1: API Key
# Header: X-API-Key: your-64-char-random-secret-key
# Must match exactly

# CHECK 2: Timestamp Validation
# Header: X-Request-Timestamp: 1708857600
# Reject if request is older than 5 minutes (prevents replay attacks)

# CHECK 3: HMAC Signature
# Header: X-Signature: hmac_sha256(api_key + timestamp + body)
# Server recalculates and compares — prevents tampering
```

```python
# VPS ai_server.py — Authentication middleware

import hmac
import hashlib
import time

async def verify_request(api_key: str, timestamp: str, signature: str, body: bytes):
    # Check 1: API Key
    if api_key != EXPECTED_API_KEY:
        raise HTTPException(401, "Invalid API key")
    
    # Check 2: Timestamp (reject if >5 min old)
    request_time = int(timestamp)
    if abs(time.time() - request_time) > 300:
        raise HTTPException(401, "Request expired")
    
    # Check 3: HMAC Signature
    expected_sig = hmac.new(
        EXPECTED_API_KEY.encode(),
        f"{timestamp}{body.decode()}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected_sig):
        raise HTTPException(401, "Invalid signature")
```

```python
# LAPTOP ai_comment.py — How laptop sends secure requests

import hmac
import hashlib
import time
import httpx

async def ask_ai_for_comment(post_text: str, comments: list) -> dict:
    body = json.dumps({"post_text": post_text, "comments": comments})
    timestamp = str(int(time.time()))
    
    signature = hmac.new(
        API_KEY.encode(),
        f"{timestamp}{body}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    async with httpx.AsyncClient(verify=False) as client:  # Self-signed SSL
        response = await client.post(
            VPS_AI_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": API_KEY,
                "X-Request-Timestamp": timestamp,
                "X-Signature": signature,
            },
            timeout=60.0
        )
    return response.json()
```

### LAYER 5: Nginx Reverse Proxy
```nginx
# nginx_linkedpilot.conf

# Rate limiting zone
limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;

server {
    listen 8443 ssl;
    server_name _;

    # SSL/TLS
    ssl_certificate     /etc/nginx/ssl/linkedpilot.crt;
    ssl_certificate_key /etc/nginx/ssl/linkedpilot.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # IP WHITELIST — ONLY your laptop's public IP
    # Find your IP: google "what is my ip"
    allow YOUR_LAPTOP_PUBLIC_IP;
    deny all;

    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";

    # Max request body size
    client_max_body_size 50k;

    # API endpoint
    location /api/ {
        limit_req zone=api burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }

    # Health check (no auth needed, limited info)
    location /health {
        limit_req zone=api burst=2 nodelay;
        proxy_pass http://127.0.0.1:8000/health;
    }

    # Block everything else
    location / {
        return 444;  # Drop connection silently
    }
}
```

### Bonus Security: SSH Hardening
```bash
# Disable password login — use SSH keys only
# /etc/ssh/sshd_config:
PasswordAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 3
```

### Bonus Security: Fail2Ban
```bash
# Install fail2ban to auto-block brute force attackers
dnf install fail2ban -y

# Config: Ban IP after 5 failed attempts for 1 hour
# Monitors: SSH + Nginx access logs
```

### Security Summary:
```
An attacker would need to bypass ALL 5 layers:
1. ❌ Firewall blocks all non-8443 ports
2. ❌ Even if port 8443 reached, IP whitelist blocks them
3. ❌ Even if IP spoofed, needs valid API key
4. ❌ Even if API key stolen, needs valid HMAC signature
5. ❌ Even if everything bypassed, they can only ask AI to pick comments
   (no LinkedIn access, no personal data, no damage possible)
```

---

## 5. LAPTOP SETUP REQUIREMENTS

### Install BEFORE running:
```
1. Python 3.12+  (Add to PATH during install)
2. Google Chrome  (latest)
3. Docker Desktop (for OpenOutreach — needed once for setup)
4. Git            (optional)
```

### Python packages for Custom Dashboard:
```
fastapi==0.115.0
uvicorn[standard]==0.32.0
jinja2==3.1.4
python-multipart==0.0.12
aiofiles==24.1.0
playwright==1.49.0
apscheduler==3.10.4
aiosqlite==0.20.0
httpx==0.27.2
python-dotenv==1.0.1
```

---

## 6. VPS SETUP REQUIREMENTS

### System packages (AlmaLinux 8):
```bash
dnf install epel-release -y
dnf install python3.11 python3.11-pip nginx fail2ban -y
```

### Python packages:
```
fastapi==0.115.0
uvicorn[standard]==0.32.0
httpx==0.27.2
```

### AI Model:
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull phi3:mini
```

---

## 7. DATABASE STRATEGY

### Two databases, side by side:

```
data/
├── crm.db              ← OpenOutreach's database (READ ONLY by us)
│   ├── leads           ← All scraped profiles
│   ├── contacts        ← Connected leads
│   ├── companies       ← Company info
│   └── deals           ← Deal tracking
│
└── linkedpilot.db      ← OUR database (READ/WRITE)
    ├── custom_lists             ← Named lead lists
    ├── custom_list_leads        ← Leads in custom lists (can be from crm.db OR CSV upload)
    ├── predefined_comments      ← User's 50 comment templates
    ├── like_queue               ← Like campaign actions
    ├── comment_queue            ← Comment campaign actions
    ├── campaigns                ← Campaign tracking
    ├── activity_log             ← All our actions logged
    ├── daily_counters           ← Track daily/weekly limits
    └── settings                 ← App configuration
```

### WHY two databases?
```
crm.db   → OpenOutreach owns this. We NEVER write to it.
             We only READ leads data from it for display/filter/export.
             If OpenOutreach updates its schema, our code won't break.

linkedpilot.db → We own this. Our lists, comments, campaigns, logs.
                  Fully portable. Copy this file = copy all our data.
```

### Custom Database Schema (linkedpilot.db):

```sql
-- Named lists (for organizing leads for like/comment campaigns)
CREATE TABLE IF NOT EXISTS custom_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    source      TEXT DEFAULT 'manual',  -- 'openoutreach' or 'csv_upload' or 'manual'
    description TEXT DEFAULT '',
    lead_count  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Leads in custom lists
-- These can come from OpenOutreach's crm.db OR from CSV upload
CREATE TABLE IF NOT EXISTS custom_list_leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id         INTEGER NOT NULL REFERENCES custom_lists(id) ON DELETE CASCADE,
    full_name       TEXT NOT NULL,
    first_name      TEXT DEFAULT '',
    headline        TEXT DEFAULT '',
    company         TEXT DEFAULT '',
    location        TEXT DEFAULT '',
    profile_url     TEXT NOT NULL,
    source          TEXT DEFAULT 'openoutreach',  -- 'openoutreach' or 'csv_upload'
    openoutreach_lead_id  INTEGER DEFAULT NULL,    -- Reference to crm.db lead (if from OO)
    is_liked        INTEGER DEFAULT 0,
    is_commented    INTEGER DEFAULT 0,
    last_action_at  DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(list_id, profile_url)
);

-- Predefined comments (user's 50 templates)
CREATE TABLE IF NOT EXISTS predefined_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    is_active   INTEGER DEFAULT 1,
    usage_count INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Campaign tracking
CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    list_id         INTEGER NOT NULL REFERENCES custom_lists(id),
    campaign_type   TEXT NOT NULL CHECK(campaign_type IN ('like','comment')),
    status          TEXT DEFAULT 'draft' CHECK(status IN ('draft','active','paused','completed','cancelled')),
    total_leads     INTEGER DEFAULT 0,
    processed       INTEGER DEFAULT 0,
    successful      INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    skipped         INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at      DATETIME,
    completed_at    DATETIME
);

-- Action queue for like/comment
CREATE TABLE IF NOT EXISTS action_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER REFERENCES campaigns(id),
    lead_id         INTEGER NOT NULL REFERENCES custom_list_leads(id),
    action_type     TEXT NOT NULL CHECK(action_type IN ('like','comment')),
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','scheduled','running','done','failed','skipped')),
    comment_text    TEXT DEFAULT NULL,
    scheduled_at    DATETIME,
    completed_at    DATETIME,
    error_message   TEXT DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Activity log
CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    lead_name   TEXT DEFAULT '',
    lead_url    TEXT DEFAULT '',
    campaign_id INTEGER,
    status      TEXT NOT NULL,
    details     TEXT DEFAULT '',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Daily counters for rate limiting
CREATE TABLE IF NOT EXISTS daily_counters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        DATE NOT NULL,
    action_type TEXT NOT NULL,
    count       INTEGER DEFAULT 0,
    UNIQUE(date, action_type)
);

-- Settings
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
```

---

## 8. CUSTOM DASHBOARD FEATURES

### Lead Browser (reads from OpenOutreach's crm.db)
```
Page: /leads

Shows all leads from OpenOutreach's crm.db in a nice table:
├── Columns: Name, Title, Company, Location, Status, Date Added
├── FILTERS:
│   ├── Date range (from - to)
│   ├── Job title (text search)
│   ├── Company (text search)
│   ├── Location (text search)
│   ├── Status (dropdown: Discovered, Enriched, Qualified, Connected, etc.)
│   └── Search (global text search)
├── ACTIONS:
│   ├── "Export CSV" button → downloads filtered results
│   ├── "Add to List" button → select leads → add to named list
│   └── Pagination (50 per page)
```

### Important: Claude Code needs to READ OpenOutreach's crm.db schema
```
Before building the lead browser, Claude Code MUST:
1. Open OpenOutreach's source code
2. Find the database models (likely in linkedin/db/ or similar)
3. Understand the table structure of crm.db
4. Build SQLite READ queries that match their schema
5. NEVER write to crm.db — READ ONLY
```

---

## 9. LIKE ENGINE SPECIFICATION

```
Limits: 100/day, 300/week
Delay: 4-14 min between likes (random)
Weekly distribution: random across 5 work days (Mon-Fri)
Works on: Leads in a custom list (from OpenOutreach data OR CSV upload)

Flow:
1. User selects a list → clicks "Start Like Campaign"
2. System creates campaign + queues all leads
3. For each lead (during work hours, with delays):
   a. Playwright opens lead's profile URL in Chrome (LAPTOP)
   b. Navigates to recent activity/posts
   c. Finds latest post
   d. Clicks "Like" (if not already liked)
   e. Updates lead status
   f. Logs action
   g. Waits random 4-14 minutes
4. Respects daily (100) and weekly (300) limits
```

---

## 10. COMMENT ENGINE + VPS AI

```
Limits: 50/day, 200/week
Delay: 8-22 min between comments (random)
Predefined comments: 50 texts added by user in dashboard
AI Selection: VPS picks best comment for each post

Flow:
1. User adds 50 predefined comments in /comments page
2. User selects a list → clicks "Start Comment Campaign"
3. For each lead (during work hours, with delays):
   a. Playwright opens lead's profile/posts in Chrome (LAPTOP)
   b. Extracts latest post text content
   c. LAPTOP sends SECURE request to VPS:
      POST https://VPS_IP:8443/api/suggest-comment
      Headers: X-API-Key, X-Request-Timestamp, X-Signature
      Body: {post_text, comments[50]}
   d. VPS AI (Phi-3) reads post → picks best comment → returns index
   e. Playwright types selected comment on LinkedIn (LAPTOP)
   f. Updates lead status
   g. Logs action
   h. Waits random 8-22 minutes
4. FALLBACK: If VPS unreachable → pick random comment from list
5. Respects daily (50) and weekly (200) limits
```

---

## 11. CSV IMPORT/EXPORT

### CSV Export (from OpenOutreach data):
```
User filters leads → clicks "Export CSV" → downloads file with:
full_name, first_name, last_name, headline, company, location, profile_url, status, date_added
```

### CSV Import (for like/comment campaigns):
```
User uploads CSV file → creates new named list → leads added to list
Required CSV column: profile_url (minimum)
Optional columns: full_name, headline, company, location
Duplicate detection: skip if profile_url already exists in that list
```

---

## 12. RATE LIMITING & HUMAN SIMULATION

Same as LinkedPilot v1 spec (Section 9). Key points:
- Weekly budget distributed randomly across 5 work days
- Human-like delays with warm-up, lunch break, cool-down
- Never runs outside work hours (default 9am-6pm IST)
- Never runs on weekends
- First 2 weeks: 30% of max limits (ramp-up)
- Auto-pause on 3 consecutive failures
- Typing simulation (80-150ms per character)

---

## 13. PROJECT FOLDER STRUCTURE

```
linkedpilot/
│
├── openoutreach/                    # OpenOutreach (cloned from GitHub, runs separately)
│   ├── linkedin/                    # Their automation code
│   ├── assets/                      # Their config + data
│   │   └── data/crm.db            # ◄── WE READ THIS (READ ONLY)
│   └── ...                         # Rest of their code
│
├── dashboard/                       # OUR CUSTOM CODE
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app entry point
│   │   ├── config.py               # Settings loader
│   │   ├── database.py             # SQLite connection (both DBs)
│   │   │
│   │   ├── routers/
│   │   │   ├── dashboard.py        # GET / → main stats page
│   │   │   ├── leads.py            # /leads → OpenOutreach lead browser
│   │   │   ├── lists.py            # /lists → named list management
│   │   │   ├── campaigns.py        # /campaigns → like/comment campaigns
│   │   │   ├── comments.py         # /comments → predefined comment manager
│   │   │   ├── settings_page.py    # /settings → app settings
│   │   │   └── logs.py             # /logs → activity log viewer
│   │   │
│   │   ├── automation/
│   │   │   ├── browser.py          # Playwright Chrome manager
│   │   │   ├── linkedin_like.py    # Like latest post engine
│   │   │   ├── linkedin_comment.py # Comment on post engine
│   │   │   └── human_delay.py      # Human simulation delays
│   │   │
│   │   ├── services/
│   │   │   ├── ai_comment.py       # SECURE VPS API client (HMAC + SSL)
│   │   │   ├── rate_limiter.py     # Daily/weekly limit checker
│   │   │   ├── scheduler.py        # APScheduler campaign runner
│   │   │   ├── weekly_planner.py   # Weekly budget distribution
│   │   │   ├── csv_handler.py      # CSV import/export
│   │   │   └── openoutreach_reader.py  # READ crm.db safely
│   │   │
│   │   ├── templates/              # Jinja2 HTML templates
│   │   │   ├── base.html
│   │   │   ├── dashboard.html
│   │   │   ├── leads.html
│   │   │   ├── lists.html
│   │   │   ├── campaigns.html
│   │   │   ├── comments.html
│   │   │   ├── settings.html
│   │   │   ├── logs.html
│   │   │   └── partials/
│   │   │
│   │   └── static/
│   │       ├── css/custom.css
│   │       └── js/app.js
│   │
│   ├── data/
│   │   ├── linkedpilot.db          # OUR database
│   │   ├── linkedin_cookies.json   # Browser session cookies
│   │   └── exports/                # CSV export files
│   │
│   ├── requirements.txt
│   ├── setup.py                    # First-time setup
│   ├── start.py                    # Launcher (no .bat!)
│   ├── .env.example
│   ├── .env
│   └── README.md
│
├── vps/                             # Copy to VPS separately
│   ├── ai_server.py                # FastAPI AI server with HMAC auth
│   ├── requirements.txt
│   ├── setup_vps.sh                # Complete VPS setup script
│   ├── nginx_linkedpilot.conf      # Nginx with SSL + IP whitelist
│   ├── linkedpilot-ai.service      # Systemd service
│   └── README.md
│
└── README.md                        # Master setup instructions
```

---

## 14. DASHBOARD UI PAGES

| Page | URL | Features |
|------|-----|----------|
| Dashboard | `/` | Stats: total leads (from OO), likes today, comments today, weekly progress |
| Lead Browser | `/leads` | OpenOutreach leads with filters, export CSV, add to list |
| Lists | `/lists` | Named lists, create/delete, upload CSV, lead counts |
| List Detail | `/lists/{id}` | Leads in list, start like/comment campaign |
| Campaigns | `/campaigns` | All campaigns with progress, start/pause/cancel |
| Comments | `/comments` | Add/edit 50 predefined comments |
| Settings | `/settings` | Limits, delays, VPS URL/key, work hours, test VPS connection |
| Logs | `/logs` | Activity log with date/type filters, export |

---

## 15. LAUNCHER (NO .bat FILES)

Same as LinkedPilot v1 — `start.py` and `setup.py` are pure Python.
Opens browser automatically to localhost:8080.

---

## 16. PORTABILITY

```
To move to another laptop:
1. Copy entire linkedpilot/ folder
2. On new laptop: Install Python 3.12 + Docker
3. Run: python dashboard/setup.py
4. Start OpenOutreach: docker run ... (their command)
5. Start Dashboard: python dashboard/start.py
6. Re-login to LinkedIn (cookies won't transfer)

Data preserved: ✅ All leads, lists, comments, campaigns, logs
```

---

## 17. BUILD ORDER

### Phase 1: Setup project structure + databases
### Phase 2: OpenOutreach reader (safely read crm.db)
### Phase 3: Dashboard UI shell (base template, navigation)
### Phase 4: Lead Browser (read OO data + filters + CSV export)
### Phase 5: Named Lists + CSV Import
### Phase 6: Playwright browser engine for like/comment
### Phase 7: Like Engine
### Phase 8: VPS AI Server (with 5-layer security)
### Phase 9: Comment Engine + VPS integration
### Phase 10: Campaign management (start/pause/progress)
### Phase 11: Settings + Logs
### Phase 12: Testing & Polish

**Estimated time: 8-10 hours with Claude Code**

---

## 18. CRITICAL RULES

1. **NEVER write to OpenOutreach's crm.db** — READ ONLY
2. **ALL LinkedIn actions from LAPTOP only** — never from VPS
3. **VPS only for AI** — no LinkedIn cookies, no profile URLs stored on VPS
4. **5-layer security on VPS** — firewall + IP whitelist + SSL + API key + HMAC
5. **No .bat files** — Python launchers only
6. **All paths relative** — fully portable
7. **All data in data/ folder** — easy backup and move
8. **Async everywhere** — FastAPI, Playwright, database queries
9. **Never crash on failure** — log error, skip lead, continue
10. **Rate limit check BEFORE every action** — no exceptions

---

*END OF SPECIFICATION v2.0*

*Give this document to Claude Code. It should read EVERYTHING before writing any code.*
*Start with Phase 1 and test each phase before moving to next.*
