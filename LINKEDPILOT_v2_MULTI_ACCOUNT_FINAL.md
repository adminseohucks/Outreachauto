# LinkedPilot v2 — MULTI-ACCOUNT ADDON SPEC (FINAL)
# Append this to LINKEDPILOT_v2_SPEC.md

---

## 19. MULTI-ACCOUNT (4 SENDERS) SPECIFICATION

### Overview

LinkedPilot supports 4 LinkedIn accounts (called "Senders") on the SAME laptop.
Each sender has its own browser profile, cookies, and session.
ALL 4 senders can do EVERYTHING: Connect + Like + Comment (via Dashboard AND OpenOutreach).

**KEY RULES:**
1. User chooses per campaign: use 1 sender OR multiple senders (up to 4)
2. When ANY sender performs ANY action on a profile → that profile is BLOCKED for ALL other senders for 3 DAYS
3. After 3 days cooldown → profile unlocks → other senders can interact
4. Only 1 sender active at a time on same laptop (same IP safety)

### How 4 Accounts Work Technically:

```
Same Laptop, Same Chrome, but 4 SEPARATE browser profiles:

data/browser_profiles/
├── sender_1/          ← Account 1's cookies, localStorage, session
│   ├── cookies.json
│   └── playwright_profile/
├── sender_2/          ← Account 2's cookies, localStorage, session
│   ├── cookies.json
│   └── playwright_profile/
├── sender_3/          ← Account 3's cookies, localStorage, session
│   ├── cookies.json
│   └── playwright_profile/
└── sender_4/          ← Account 4's cookies, localStorage, session
    ├── cookies.json
    └── playwright_profile/

When Campaign runs with Sender 2:
→ Playwright launches Chrome with sender_2's profile
→ LinkedIn sees Sender 2 logged in
→ All actions happen as Sender 2
→ After campaign pauses, Chrome closes

When Campaign runs with Sender 3:
→ Playwright launches Chrome with sender_3's profile
→ LinkedIn sees Sender 3 logged in
→ Completely separate session
```

### IMPORTANT SAFETY RULE:

```
❌ NEVER run 2 senders simultaneously on same laptop!

Why: Same IP + 2 LinkedIn accounts active = LinkedIn detects & bans both

✅ CORRECT: Run one sender at a time
   9:00 AM  - 11:10 AM → Sender 1 campaigns
   11:05 AM - 1:00 PM  → Sender 2 campaigns
   2:00 PM  - 4:00 PM  → Sender 3 campaigns
   4:05 PM  - 6:00 PM  → Sender 4 campaigns

The scheduler handles this automatically:
- Queue system runs campaigns one sender at a time
- 5-minute gap between switching senders
- Closes previous sender's browser completely before opening next
```

---

## 20. 3-DAY COOLDOWN SYSTEM (DUPLICATE PROTECTION)

### The Rule:

```
When Sender X performs ANY action (connect/like/comment) on Profile Y:
→ Profile Y is BLOCKED for Sender 1, 2, 3, 4 (including Sender X) for 3 DAYS
→ After 3 days, Profile Y UNLOCKS for all senders
→ Any sender can now interact with Profile Y again

EXCEPTION: Sender X (who did the original action) can still do OTHER actions 
on Profile Y immediately. Only OTHER senders are blocked for 3 days.

Example Timeline:
─────────────────────────────────────────────────────
Day 1 (Monday):
  Sender 1 sends CONNECT to Priya Sharma
  → Priya is now:
     Sender 1: Can still LIKE and COMMENT on Priya (same sender, different action)
     Sender 2: BLOCKED until Thursday ❌
     Sender 3: BLOCKED until Thursday ❌  
     Sender 4: BLOCKED until Thursday ❌

Day 1 (Monday, 2 hours later):
  Sender 1 LIKES Priya's post (allowed — same sender, different action)
  → Cooldown RESETS to 3 days from NOW
  → Priya blocked for Sender 2,3,4 until Thursday (from this new action)

Day 4 (Thursday):
  3 days passed since last action on Priya
  → Priya UNLOCKED for ALL senders ✅
  → Sender 2 can now connect/like/comment on Priya
─────────────────────────────────────────────────────
```

### Database — Global Contact Registry:

```sql
-- Tracks EVERY interaction across ALL senders
-- This is the CORE table for 3-day cooldown protection

CREATE TABLE IF NOT EXISTS global_contact_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_url     TEXT NOT NULL,              -- LinkedIn profile URL (unique per person)
    full_name       TEXT DEFAULT '',
    
    -- Last action tracking (for cooldown calculation)
    last_action_type      TEXT,                 -- 'connect', 'like', 'comment'
    last_action_sender_id INTEGER REFERENCES senders(id),
    last_action_at        DATETIME,             -- Cooldown starts from THIS timestamp
    
    -- Action history (who did what)
    connected_by_sender_id    INTEGER REFERENCES senders(id),
    connected_at              DATETIME,
    
    liked_by_sender_id        INTEGER REFERENCES senders(id),
    liked_at                  DATETIME,
    
    commented_by_sender_id    INTEGER REFERENCES senders(id),
    commented_at              DATETIME,
    comment_text              TEXT DEFAULT '',
    
    -- Cooldown
    cooldown_expires_at   DATETIME,             -- last_action_at + 3 days
    
    first_contacted_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(profile_url)
);

CREATE INDEX IF NOT EXISTS idx_gcr_url ON global_contact_registry(profile_url);
CREATE INDEX IF NOT EXISTS idx_gcr_cooldown ON global_contact_registry(cooldown_expires_at);
```

### Cooldown Check Logic:

```python
COOLDOWN_DAYS = 3  # Configurable in settings

async def can_perform_action(profile_url: str, action_type: str, sender_id: int) -> dict:
    """
    Check if this sender can perform this action on this profile.
    
    Returns:
        {
            'allowed': True/False,
            'reason': 'ok' / 'cooldown_active' / 'same_action_exists',
            'blocked_until': datetime or None,
            'blocked_by_sender': sender_name or None
        }
    """
    
    registry = await db.fetch_one(
        "SELECT * FROM global_contact_registry WHERE profile_url = ?",
        (profile_url,)
    )
    
    if registry is None:
        # Never contacted → Safe to proceed
        return {'allowed': True, 'reason': 'ok'}
    
    now = datetime.utcnow()
    cooldown_expires = registry['cooldown_expires_at']
    last_sender = registry['last_action_sender_id']
    
    # CASE 1: Same sender → allowed for DIFFERENT action types
    if last_sender == sender_id:
        # Check if this exact action already done
        if action_type == 'connect' and registry['connected_by_sender_id'] == sender_id:
            return {'allowed': False, 'reason': 'already_done_by_you'}
        if action_type == 'like' and registry['liked_by_sender_id'] == sender_id:
            return {'allowed': False, 'reason': 'already_done_by_you'}
        if action_type == 'comment' and registry['commented_by_sender_id'] == sender_id:
            return {'allowed': False, 'reason': 'already_done_by_you'}
        # Different action by same sender → allowed
        return {'allowed': True, 'reason': 'ok'}
    
    # CASE 2: Different sender → check cooldown
    if cooldown_expires and now < cooldown_expires:
        # Still in cooldown period
        return {
            'allowed': False,
            'reason': 'cooldown_active',
            'blocked_until': cooldown_expires,
            'blocked_by_sender': last_sender
        }
    
    # CASE 3: Different sender, cooldown expired → allowed
    return {'allowed': True, 'reason': 'ok'}


async def register_action(profile_url: str, full_name: str, action_type: str, sender_id: int):
    """Record action and set/reset 3-day cooldown."""
    
    cooldown_expires = datetime.utcnow() + timedelta(days=COOLDOWN_DAYS)
    
    # Build dynamic column updates based on action type
    action_column = f"{action_type}d_by_sender_id" if action_type == 'connect' else f"{action_type}_by_sender_id"  
    # connect → connected_by_sender_id, like → liked_by_sender_id, comment → commented_by_sender_id
    
    if action_type == 'connect':
        col_sender = 'connected_by_sender_id'
        col_at = 'connected_at'
    elif action_type == 'like':
        col_sender = 'liked_by_sender_id'
        col_at = 'liked_at'
    elif action_type == 'comment':
        col_sender = 'commented_by_sender_id'
        col_at = 'commented_at'
    
    await db.execute(f"""
        INSERT INTO global_contact_registry 
            (profile_url, full_name, last_action_type, last_action_sender_id, 
             last_action_at, cooldown_expires_at, {col_sender}, {col_at})
        VALUES (?, ?, ?, ?, datetime('now'), ?, ?, datetime('now'))
        ON CONFLICT(profile_url) DO UPDATE SET
            last_action_type = ?,
            last_action_sender_id = ?,
            last_action_at = datetime('now'),
            cooldown_expires_at = ?,
            {col_sender} = ?,
            {col_at} = datetime('now')
    """, (profile_url, full_name, action_type, sender_id, cooldown_expires,
          sender_id, action_type, sender_id, cooldown_expires, sender_id))
```

### Example Scenario with 3-Day Cooldown:

```
List: "Mumbai CEOs" — 500 leads

MONDAY:
  Campaign 1: "Connect Mumbai CEOs" 
    Senders: [Sender 1, Sender 2]  (user selected 2 senders)
    
    Sender 1 slot (9 AM - 11 AM):
      → Connects to Lead 1, Lead 2, Lead 3... Lead 75
      → Each lead now BLOCKED for Sender 2,3,4 until Thursday
    
    Sender 2 slot (11:05 AM - 1 PM):
      → Tries Lead 1 → BLOCKED (Sender 1 did it today) → SKIP
      → Tries Lead 2 → BLOCKED → SKIP
      → Starts from Lead 76, Lead 77... Lead 150
      → Each lead now BLOCKED for Sender 1,3,4 until Thursday

TUESDAY:
  Campaign 2: "Like Mumbai CEOs"
    Senders: [Sender 3]  (user selected 1 sender)
    
    Sender 3 slot:
      → Tries Lead 1 → BLOCKED until Thursday (Sender 1 connected Monday) → SKIP
      → Tries Lead 76 → BLOCKED until Thursday (Sender 2 connected Monday) → SKIP
      → Starts from Lead 151, Lead 152... Lead 251
      → Each lead BLOCKED until Friday

THURSDAY:
  Lead 1 through Lead 75 UNLOCKED! (3 days since Monday)
  
  Campaign 3: "Comment Mumbai CEOs"
    Senders: [Sender 4]
    
    Sender 4 slot:
      → Lead 1 → UNLOCKED ✅ → Comments on post
      → Lead 2 → UNLOCKED ✅ → Comments on post
      → ...
```

---

## 21. MULTI-SENDER CAMPAIGN CREATION

### Campaign UI — Sender Selection:

```
┌──────────────────────────────────────────────────────────────┐
│  New Campaign                                                 │
│                                                               │
│  Campaign Name: [Mumbai CEOs - Connect Round 1        ]       │
│                                                               │
│  Select List:   [Mumbai CEOs (500 leads) ▼]                   │
│                                                               │
│  Campaign Type: ○ Connect  ● Like Posts  ○ Comment on Posts   │
│                                                               │
│  ★ Select Senders: (choose 1 or more)                         │
│  ┌───────────────────────────────────────────────────────┐    │
│  │ ☑ Sender 1: Rahul     (30/100 likes today)  🟢 Active│    │
│  │ ☑ Sender 2: Business  (0/100 likes today)   🟢 Active│    │
│  │ ☐ Sender 3: Team 1    (60/100 likes today)  🟢 Active│    │
│  │ ☐ Sender 4: Team 2    (⚠️ Login expired)    🔴 Error │    │
│  └───────────────────────────────────────────────────────┘    │
│  Selected: 2 senders                                          │
│                                                               │
│  📊 Lead Distribution Preview:                                │
│  ┌───────────────────────────────────────────────────────┐    │
│  │ Total leads in list:           500                     │    │
│  │ Currently in cooldown:          73  (blocked < 3 days) │    │
│  │ Available leads:               427                     │    │
│  │                                                        │    │
│  │ Distribution across 2 senders:                         │    │
│  │   Sender 1 (Rahul):    ~214 leads                     │    │
│  │   Sender 2 (Business): ~213 leads                     │    │
│  │                                                        │    │
│  │ Estimated days to complete: ~3 days                    │    │
│  │ (at 100 likes/day/sender)                              │    │
│  └───────────────────────────────────────────────────────┘    │
│                                                               │
│  [Start Campaign]  [Save as Draft]                            │
└──────────────────────────────────────────────────────────────┘
```

### Multi-Sender Campaign Logic:

```python
class MultiSenderCampaign:
    """
    When user selects multiple senders for a campaign:
    1. Get all leads from selected list
    2. Remove leads in cooldown (blocked < 3 days)
    3. Distribute remaining leads equally among selected senders
    4. Each sender gets their chunk
    5. Senders run ONE AT A TIME (scheduler handles time slots)
    """
    
    async def distribute_leads(self, campaign_id: int):
        campaign = await get_campaign(campaign_id)
        list_leads = await get_list_leads(campaign.list_id)
        senders = campaign.selected_senders  # e.g., [sender_1, sender_2]
        
        # Filter out leads in cooldown
        available_leads = []
        for lead in list_leads:
            check = await can_perform_action(
                lead.profile_url, 
                campaign.action_type,
                sender_id=None  # Check if blocked for ANY other sender
            )
            if check['allowed']:
                available_leads.append(lead)
        
        # Distribute equally
        # Shuffle to avoid patterns
        random.shuffle(available_leads)
        
        chunks = {}
        chunk_size = len(available_leads) // len(senders)
        for i, sender in enumerate(senders):
            start = i * chunk_size
            end = start + chunk_size if i < len(senders) - 1 else len(available_leads)
            chunks[sender.id] = available_leads[start:end]
        
        # Queue actions for each sender
        for sender_id, leads in chunks.items():
            for lead in leads:
                await queue_action(
                    campaign_id=campaign_id,
                    sender_id=sender_id,
                    profile_url=lead.profile_url,
                    action_type=campaign.action_type,
                    status='pending'
                )
        
        return chunks
```

### Lead Distribution Strategies:

```
When multiple senders are selected, leads are split:

Strategy: EQUAL SPLIT (default)
─────────────────────────────
427 available leads ÷ 2 senders = ~214 each

Sender 1 gets: Lead 1, Lead 3, Lead 5, Lead 7...  (shuffled)
Sender 2 gets: Lead 2, Lead 4, Lead 6, Lead 8...  (shuffled)

Each sender processes their chunk during their time slot.
After processing, each lead gets 3-day cooldown.
```

---

## 22. DATABASE — COMPLETE SCHEMA (UPDATED)

### Senders Table:

```sql
CREATE TABLE IF NOT EXISTS senders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,                  -- Display name: "Rahul's Account"
    email       TEXT NOT NULL UNIQUE,           -- LinkedIn email
    status      TEXT DEFAULT 'active'           -- 'active', 'paused', 'login_expired'
                CHECK(status IN ('active','paused','login_expired')),
    profile_dir TEXT NOT NULL,                  -- 'data/browser_profiles/sender_1'
    
    -- Per-sender limits (customizable)
    daily_connect_limit   INTEGER DEFAULT 150,
    daily_like_limit      INTEGER DEFAULT 100,
    weekly_like_limit     INTEGER DEFAULT 300,
    daily_comment_limit   INTEGER DEFAULT 50,
    weekly_comment_limit  INTEGER DEFAULT 200,
    
    last_active_at  DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Campaigns Table (updated):

```sql
CREATE TABLE IF NOT EXISTS campaigns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    list_id      INTEGER NOT NULL REFERENCES custom_lists(id),
    action_type  TEXT NOT NULL CHECK(action_type IN ('connect','like','comment')),
    
    -- Multi-sender support
    sender_ids   TEXT NOT NULL,               -- JSON array: "[1,2]" or "[3]"
    
    status       TEXT DEFAULT 'draft'
                 CHECK(status IN ('draft','running','paused','completed','cancelled')),
    
    -- Progress tracking
    total_leads        INTEGER DEFAULT 0,
    leads_in_cooldown  INTEGER DEFAULT 0,     -- Skipped due to cooldown
    leads_processed    INTEGER DEFAULT 0,
    leads_succeeded    INTEGER DEFAULT 0,
    leads_failed       INTEGER DEFAULT 0,
    leads_skipped      INTEGER DEFAULT 0,     -- Cooldown skips during execution
    
    started_at    DATETIME,
    completed_at  DATETIME,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Action Queue (updated):

```sql
CREATE TABLE IF NOT EXISTS action_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER REFERENCES campaigns(id),
    sender_id    INTEGER NOT NULL REFERENCES senders(id),
    profile_url  TEXT NOT NULL,
    full_name    TEXT DEFAULT '',
    action_type  TEXT NOT NULL CHECK(action_type IN ('connect','like','comment')),
    
    status       TEXT DEFAULT 'pending'
                 CHECK(status IN ('pending','running','completed','failed','skipped_cooldown')),
    
    comment_text TEXT DEFAULT '',              -- For comment actions
    error_msg    TEXT DEFAULT '',
    
    scheduled_at  DATETIME,
    executed_at   DATETIME,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Daily Counters (per sender):

```sql
CREATE TABLE IF NOT EXISTS daily_counters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        DATE NOT NULL,
    action_type TEXT NOT NULL,
    sender_id   INTEGER NOT NULL REFERENCES senders(id),
    count       INTEGER DEFAULT 0,
    UNIQUE(date, action_type, sender_id)
);
```

### Settings (updated with cooldown):

```sql
-- Add to settings table:
-- key: 'cooldown_days', value: '3'        -- Default 3 days, user can change
-- key: 'max_senders', value: '4'          -- Max senders allowed
-- key: 'sender_gap_minutes', value: '5'   -- Gap between sender switches
```

---

## 23. DASHBOARD UI CHANGES

### Sender Management Page: `/senders`

```
┌──────────────────────────────────────────────────────┐
│  Sender Management                    [+ Add Sender] │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ 🟢 Sender 1: Rahul's Account                    │ │
│  │    Email: rahul@gmail.com                        │ │
│  │    Status: Active | Last active: 2 hours ago     │ │
│  │    Today: 45/150 connects, 30/100 likes, 10/50 c│ │
│  │    [Re-login] [Pause] [Edit Limits]              │ │
│  └─────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────┐ │
│  │ 🟢 Sender 2: Business Account                   │ │
│  │    Email: business@company.com                   │ │
│  │    Status: Active | Last active: 5 hours ago     │ │
│  │    Today: 0/150 connects, 60/100 likes, 0/50 c  │ │
│  │    [Re-login] [Pause] [Edit Limits]              │ │
│  └─────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────┐ │
│  │ 🟡 Sender 3: Team Member 1                      │ │
│  │    Email: team1@company.com                      │ │
│  │    Status: Login Expired ⚠️  (Re-login needed)    │ │
│  │    [Re-login] [Edit Limits]                      │ │
│  └─────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────┐ │
│  │ 🟢 Sender 4: Team Member 2                      │ │
│  │    Email: team2@company.com                      │ │
│  │    Status: Active | Last active: 1 hour ago      │ │
│  │    Today: 20/150 connects, 0/100 likes, 5/50 c  │ │
│  │    [Re-login] [Pause] [Edit Limits]              │ │
│  └─────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### Lead Browser — Shows cooldown status:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Lead Browser                                                               │
│                                                                             │
│  Name           │ Title          │ Last Action     │ By       │ Cooldown    │
│  ──────────────────────────────────────────────────────────────────────────│
│  Rahul Sharma   │ Marketing Mgr │ Connected 1d ago│ Sender 1 │ 🔴 2d left  │
│  Priya Singh    │ Sales Dir     │ Liked 4d ago    │ Sender 2 │ 🟢 Available│
│  Amit Patel     │ CEO           │ —               │ —        │ 🟢 Available│
│  Neha Gupta     │ CTO           │ Commented 2d ago│ Sender 3 │ 🔴 1d left  │
│  Vikram Joshi   │ VP Sales      │ Connected 3d ago│ Sender 1 │ 🟢 Available│
└─────────────────────────────────────────────────────────────────────────────┘

Filter: [Cooldown Status ▼] → All / Available Only / In Cooldown
```

### Dashboard Home — Per-Sender Stats:

```
┌──────────────────────────────────────────────────────────────┐
│  LinkedPilot Dashboard                    Wed, 25 Feb 2026   │
│                                                               │
│  📊 Today's Activity (All Senders)                            │
│  ┌──────────┬──────────┬──────────┬──────────┐               │
│  │          │ Connects │ Likes    │ Comments │               │
│  ├──────────┼──────────┼──────────┼──────────┤               │
│  │ Sender 1 │ 45/150   │ 30/100   │ 10/50   │               │
│  │ Sender 2 │ 80/150   │ 60/100   │ 25/50   │               │
│  │ Sender 3 │ ⚠️ Login │ ⚠️ Login │ ⚠️ Login│               │
│  │ Sender 4 │ 20/150   │ 0/100    │ 5/50    │               │
│  ├──────────┼──────────┼──────────┼──────────┤               │
│  │ TOTAL    │ 145/600  │ 90/400   │ 40/200  │               │
│  └──────────┴──────────┴──────────┴──────────┘               │
│                                                               │
│  🔴 Leads in Cooldown: 247 / 1500 total                      │
│  🟢 Leads Available: 1253                                     │
│                                                               │
│  📋 Active Campaigns:                                         │
│  • Mumbai CEOs Connect [Sender 1, 2] — 60% complete          │
│  • Delhi Leads Like [Sender 4] — 25% complete                │
└──────────────────────────────────────────────────────────────┘
```

---

## 24. OPENOUTREACH — 4 INSTANCES

### All 4 accounts use OpenOutreach for search/connect/message:

```
openoutreach/
├── instance_1/                  
│   ├── accounts.secrets.yaml    # Account 1 LinkedIn credentials
│   ├── search_configs/          # Account 1 search YAML files
│   └── assets/data/crm.db      # Account 1 scraped leads
│
├── instance_2/                  
│   ├── accounts.secrets.yaml    # Account 2 LinkedIn credentials
│   ├── search_configs/
│   └── assets/data/crm.db      # Account 2 scraped leads
│
├── instance_3/                  
│   ├── accounts.secrets.yaml    # Account 3 LinkedIn credentials
│   ├── search_configs/
│   └── assets/data/crm.db      # Account 3 scraped leads
│
└── instance_4/                  
    ├── accounts.secrets.yaml    # Account 4 LinkedIn credentials
    ├── search_configs/
    └── assets/data/crm.db      # Account 4 scraped leads

Docker containers (1 per account, run one at a time):
├── docker run ... -v ./instance_1:/app openoutreach  (port 8001)
├── docker run ... -v ./instance_2:/app openoutreach  (port 8002)
├── docker run ... -v ./instance_3:/app openoutreach  (port 8003)
└── docker run ... -v ./instance_4:/app openoutreach  (port 8004)

SAFETY: Only 1 container active at a time!
Dashboard manages start/stop of containers per sender time slot.
```

### Dashboard reads all 4 crm.db files:

```python
# openoutreach_reader.py — Updated for multi-instance

OPENOUTREACH_DBS = {
    1: 'openoutreach/instance_1/assets/data/crm.db',
    2: 'openoutreach/instance_2/assets/data/crm.db',
    3: 'openoutreach/instance_3/assets/data/crm.db',
    4: 'openoutreach/instance_4/assets/data/crm.db',
}

async def get_all_leads():
    """Read leads from ALL 4 OpenOutreach databases."""
    all_leads = []
    for sender_id, db_path in OPENOUTREACH_DBS.items():
        if os.path.exists(db_path):
            leads = await read_oo_leads(db_path)
            for lead in leads:
                lead['source_sender_id'] = sender_id
            all_leads.extend(leads)
    
    # Deduplicate by profile_url (same person found by multiple senders)
    seen = {}
    unique_leads = []
    for lead in all_leads:
        if lead['profile_url'] not in seen:
            seen[lead['profile_url']] = True
            unique_leads.append(lead)
    
    return unique_leads
```

---

## 25. SCHEDULER — TIME SLOT MANAGEMENT

```python
"""
4 Senders, 9 AM - 6 PM (9 hours), one at a time:

AUTO SCHEDULE:
├── Sender 1: 9:00 AM  - 11:10 AM  (2h 10m)
├── GAP:      11:10 AM - 11:15 AM  (5 min browser switch)
├── Sender 2: 11:15 AM - 1:25 PM   (2h 10m)
├── GAP:      1:25 PM  - 1:30 PM   (5 min)
├── Sender 3: 1:30 PM  - 3:40 PM   (2h 10m)
├── GAP:      3:40 PM  - 3:45 PM   (5 min)
└── Sender 4: 3:45 PM  - 5:55 PM   (2h 10m)

SMART SCHEDULING:
- If Sender 3 has no active campaigns → skip, give time to Sender 4
- If Sender 2 login expired → skip, redistribute time
- User can manually customize time slots in Settings

During each sender's time slot:
1. Close any existing browser
2. Wait 5 min gap
3. Launch Playwright with this sender's browser profile
4. Run all queued actions for this sender (from all active campaigns)
5. When time slot ends → close browser → next sender
"""

class SenderScheduler:
    
    def calculate_time_slots(self, work_start_hour=9, work_end_hour=18):
        """Calculate time slots for active senders only."""
        
        active_senders = await get_senders(status='active')
        active_with_work = [s for s in active_senders if await has_pending_actions(s.id)]
        
        if not active_with_work:
            return []
        
        total_minutes = (work_end_hour - work_start_hour) * 60  # 540 min
        gap_minutes = 5
        total_gaps = len(active_with_work) - 1
        available = total_minutes - (total_gaps * gap_minutes)
        per_sender = available // len(active_with_work)
        
        slots = []
        current = work_start_hour * 60
        
        for i, sender in enumerate(active_with_work):
            slots.append({
                'sender_id': sender.id,
                'sender_name': sender.name,
                'start': minutes_to_time(current),
                'end': minutes_to_time(current + per_sender),
            })
            current += per_sender + gap_minutes
        
        return slots
```

---

## 26. UPDATED FOLDER STRUCTURE

```
linkedpilot/
├── openoutreach/
│   ├── instance_1/                    # Account 1 (OpenOutreach)
│   │   ├── accounts.secrets.yaml
│   │   ├── search_configs/
│   │   └── assets/data/crm.db
│   ├── instance_2/                    # Account 2
│   ├── instance_3/                    # Account 3
│   └── instance_4/                    # Account 4
│
├── dashboard/
│   ├── app/
│   │   ├── main.py                    # FastAPI entry point
│   │   ├── routers/
│   │   │   ├── dashboard.py           # Home page with per-sender stats
│   │   │   ├── leads.py              # Lead browser with cooldown column
│   │   │   ├── lists.py              # Named lists
│   │   │   ├── campaigns.py          # Multi-sender campaign management
│   │   │   ├── comments.py           # Predefined comments
│   │   │   ├── senders.py            # 🆕 Sender management (4 accounts)
│   │   │   ├── settings.py           # Settings + cooldown config
│   │   │   └── logs.py               # Activity log with sender filter
│   │   ├── automation/
│   │   │   ├── browser.py            # Playwright multi-profile launcher
│   │   │   ├── linkedin_like.py      # Like engine
│   │   │   ├── linkedin_comment.py   # Comment engine
│   │   │   ├── linkedin_connect.py   # Connect engine (for dashboard campaigns)
│   │   │   └── human_delay.py        # Human-like timing
│   │   ├── services/
│   │   │   ├── ai_comment.py         # VPS AI client (HMAC auth)
│   │   │   ├── rate_limiter.py       # Per-sender rate limiting
│   │   │   ├── cooldown_checker.py   # 🆕 3-day cooldown logic
│   │   │   ├── sender_scheduler.py   # 🆕 Time slot manager
│   │   │   ├── lead_distributor.py   # 🆕 Split leads across senders
│   │   │   ├── csv_handler.py        # CSV import/export
│   │   │   └── openoutreach_reader.py # Read all 4 crm.db files
│   │   ├── templates/                 # Jinja2 HTML pages
│   │   └── static/                    # CSS, JS
│   │
│   └── data/
│       ├── linkedpilot.db             # Our DB (senders, cooldown, campaigns, etc.)
│       ├── browser_profiles/
│       │   ├── sender_1/              # Account 1 Playwright profile
│       │   ├── sender_2/              # Account 2 Playwright profile
│       │   ├── sender_3/              # Account 3 Playwright profile
│       │   └── sender_4/              # Account 4 Playwright profile
│       └── exports/                   # CSV exports
│
└── vps/                                # AI server (unchanged)
    ├── ai_server.py
    ├── setup_vps.sh
    ├── nginx_linkedpilot.conf
    └── linkedpilot-ai.service
```

---

## 27. UPDATED BUILD PHASES

```
Phase 1:  Project structure + databases (include senders table, global_contact_registry)
Phase 2:  Sender management (add/edit/re-login senders)
Phase 3:  OpenOutreach multi-instance reader (read all 4 crm.db)
Phase 4:  Dashboard UI shell (with per-sender stats)
Phase 5:  Lead Browser (with cooldown status column)
Phase 6:  Named Lists + CSV Import/Export
Phase 7:  Playwright multi-profile browser engine
Phase 8:  3-Day Cooldown system (checker + registry)
Phase 9:  Like Engine (with cooldown + multi-sender)
Phase 10: VPS AI Server (5-layer security)
Phase 11: Comment Engine + VPS integration
Phase 12: Campaign management (multi-sender selection + lead distribution)
Phase 13: Sender Scheduler (time slots, auto-switch)
Phase 14: Settings + Logs (with sender filter)
Phase 15: Testing & Polish
```

Estimated time: 12-15 hours with Claude Code

---

*END OF FINAL MULTI-ACCOUNT ADDON SPEC*
