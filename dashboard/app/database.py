"""LinkedPilot v2 — Database connections and schema init."""

import logging
import aiosqlite
from pathlib import Path
from app.config import LINKEDPILOT_DB_PATH, CRM_DB_PATH

logger = logging.getLogger(__name__)

_lp_db: aiosqlite.Connection | None = None
_crm_db: aiosqlite.Connection | None = None

SCHEMA_SQL = """
-- Senders (up to 4 LinkedIn accounts)
CREATE TABLE IF NOT EXISTS senders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    linkedin_email  TEXT NOT NULL UNIQUE,
    profile_url     TEXT DEFAULT '',
    browser_profile TEXT NOT NULL UNIQUE,
    status          TEXT DEFAULT 'active' CHECK(status IN ('active','paused','disabled')),
    daily_like_limit    INTEGER DEFAULT 100,
    daily_comment_limit INTEGER DEFAULT 50,
    daily_connect_limit INTEGER DEFAULT 25,
    weekly_like_limit   INTEGER DEFAULT 300,
    weekly_comment_limit INTEGER DEFAULT 200,
    weekly_connect_limit INTEGER DEFAULT 100,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Company pages linked to senders (for liking/commenting as a company page)
CREATE TABLE IF NOT EXISTS company_pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id   INTEGER NOT NULL REFERENCES senders(id) ON DELETE CASCADE,
    page_name   TEXT NOT NULL,
    page_url    TEXT NOT NULL,
    is_active   INTEGER DEFAULT 1,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(sender_id, page_url)
);

-- Global contact registry (cross-sender cooldown)
CREATE TABLE IF NOT EXISTS global_contact_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_url     TEXT NOT NULL,
    sender_id       INTEGER NOT NULL REFERENCES senders(id),
    action_type     TEXT NOT NULL CHECK(action_type IN ('like','comment','connect')),
    acted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    cooldown_until  DATETIME NOT NULL,
    UNIQUE(profile_url, sender_id, action_type)
);

-- Named lists
CREATE TABLE IF NOT EXISTS custom_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    source      TEXT DEFAULT 'manual',
    description TEXT DEFAULT '',
    lead_count  INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Leads in custom lists
CREATE TABLE IF NOT EXISTS custom_list_leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id         INTEGER NOT NULL REFERENCES custom_lists(id) ON DELETE CASCADE,
    full_name       TEXT NOT NULL,
    first_name      TEXT DEFAULT '',
    headline        TEXT DEFAULT '',
    company         TEXT DEFAULT '',
    location        TEXT DEFAULT '',
    profile_url     TEXT NOT NULL,
    source          TEXT DEFAULT 'openoutreach',
    openoutreach_lead_id  INTEGER DEFAULT NULL,
    is_liked        INTEGER DEFAULT 0,
    is_commented    INTEGER DEFAULT 0,
    last_action_at  DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(list_id, profile_url)
);

-- Predefined comments
CREATE TABLE IF NOT EXISTS predefined_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    is_active   INTEGER DEFAULT 1,
    usage_count INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Campaigns
CREATE TABLE IF NOT EXISTS campaigns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    list_id         INTEGER NOT NULL REFERENCES custom_lists(id),
    sender_id       INTEGER REFERENCES senders(id),
    company_page_id INTEGER DEFAULT NULL REFERENCES company_pages(id),
    campaign_type   TEXT NOT NULL CHECK(campaign_type IN ('like','comment','connect')),
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

-- Action queue
CREATE TABLE IF NOT EXISTS action_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id     INTEGER REFERENCES campaigns(id),
    lead_id         INTEGER NOT NULL REFERENCES custom_list_leads(id),
    sender_id       INTEGER REFERENCES senders(id),
    company_page_id INTEGER DEFAULT NULL REFERENCES company_pages(id),
    action_type     TEXT NOT NULL CHECK(action_type IN ('like','comment','connect')),
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','scheduled','running','done','failed','skipped')),
    comment_text    TEXT DEFAULT NULL,
    connect_note    TEXT DEFAULT NULL,
    scheduled_at    DATETIME,
    completed_at    DATETIME,
    error_message   TEXT DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Connection notes (predefined templates with {first_name} placeholder)
CREATE TABLE IF NOT EXISTS connection_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    is_active   INTEGER DEFAULT 1,
    usage_count INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Activity log
CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    sender_id   INTEGER,
    sender_name TEXT DEFAULT '',
    lead_name   TEXT DEFAULT '',
    lead_url    TEXT DEFAULT '',
    campaign_id INTEGER,
    status      TEXT NOT NULL,
    details     TEXT DEFAULT '',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Daily counters (per sender)
CREATE TABLE IF NOT EXISTS daily_counters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        DATE NOT NULL,
    sender_id   INTEGER NOT NULL REFERENCES senders(id),
    action_type TEXT NOT NULL,
    count       INTEGER DEFAULT 0,
    UNIQUE(date, sender_id, action_type)
);

-- Settings
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_gcr_profile ON global_contact_registry(profile_url);
CREATE INDEX IF NOT EXISTS idx_gcr_cooldown ON global_contact_registry(cooldown_until);
CREATE INDEX IF NOT EXISTS idx_aq_status ON action_queue(status);
CREATE INDEX IF NOT EXISTS idx_aq_sender ON action_queue(sender_id, status);
CREATE INDEX IF NOT EXISTS idx_al_created ON activity_log(created_at);
CREATE INDEX IF NOT EXISTS idx_dc_date ON daily_counters(date, sender_id);
CREATE INDEX IF NOT EXISTS idx_aq_campaign ON action_queue(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_campaigns_sender ON campaigns(sender_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_list ON campaigns(list_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_cll_list ON custom_list_leads(list_id);
CREATE INDEX IF NOT EXISTS idx_cp_sender ON company_pages(sender_id);
CREATE INDEX IF NOT EXISTS idx_al_id ON activity_log(id);
"""


async def _safe_add_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    """Add a column to an existing table if it doesn't already exist."""
    try:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in await cursor.fetchall()]
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            logger.info("Added column %s.%s", table, column)
    except Exception as exc:
        logger.debug("Column %s.%s may already exist: %s", table, column, exc)


async def _recreate_table_with_new_check(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    old_values: str,
    new_values: str,
) -> None:
    """Recreate a table to update a CHECK constraint (SQLite limitation).

    Only runs if the current CHECK constraint doesn't include the new values.
    """
    try:
        # Check current table SQL to see if migration is needed
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = await cursor.fetchone()
        if not row:
            return
        create_sql = row[0] if isinstance(row, dict) else row[0]
        # If the new values are already in the CHECK, skip
        if new_values in create_sql:
            return

        logger.info("Migrating table %s: adding %s to CHECK(%s)", table, new_values, column)

        # Get column info
        cursor = await db.execute(f"PRAGMA table_info({table})")
        cols = [(r[1], r[2], r[3], r[4]) for r in await cursor.fetchall()]
        col_names = [c[0] for c in cols]
        col_list = ", ".join(col_names)

        # Build new CREATE TABLE with updated check
        new_create = create_sql.replace(old_values, new_values)
        tmp_table = f"_{table}_new"
        new_create = new_create.replace(f"CREATE TABLE {table}", f"CREATE TABLE {tmp_table}", 1)
        # Also handle quoted/IF NOT EXISTS variants
        new_create = new_create.replace(f'CREATE TABLE IF NOT EXISTS {table}', f'CREATE TABLE {tmp_table}')

        await db.execute(new_create)
        await db.execute(f"INSERT INTO {tmp_table} ({col_list}) SELECT {col_list} FROM {table}")
        await db.execute(f"DROP TABLE {table}")
        await db.execute(f"ALTER TABLE {tmp_table} RENAME TO {table}")
        logger.info("Table %s migrated successfully", table)
    except Exception as exc:
        logger.error("Migration of %s failed: %s", table, exc)


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run migrations for existing databases that need new columns/tables."""
    # company_page_id on campaigns table
    await _safe_add_column(db, "campaigns", "company_page_id",
                           "INTEGER DEFAULT NULL REFERENCES company_pages(id)")
    # company_page_id on action_queue table
    await _safe_add_column(db, "action_queue", "company_page_id",
                           "INTEGER DEFAULT NULL REFERENCES company_pages(id)")

    # --- v2.1: Add 'connect' to campaign_type and action_type ---
    await _recreate_table_with_new_check(
        db, "campaigns", "campaign_type",
        "('like','comment')", "('like','comment','connect')",
    )
    await _recreate_table_with_new_check(
        db, "action_queue", "action_type",
        "('like','comment')", "('like','comment','connect')",
    )
    # connect_note column on action_queue
    await _safe_add_column(db, "action_queue", "connect_note", "TEXT DEFAULT NULL")

    # --- v2.1: Add connect limit columns to senders ---
    await _safe_add_column(db, "senders", "daily_connect_limit", "INTEGER DEFAULT 25")
    await _safe_add_column(db, "senders", "weekly_connect_limit", "INTEGER DEFAULT 100")

    # --- v2.1: connection_notes table (created via SCHEMA_SQL for fresh DBs) ---
    await db.execute("""
        CREATE TABLE IF NOT EXISTS connection_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            text        TEXT NOT NULL,
            is_active   INTEGER DEFAULT 1,
            usage_count INTEGER DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.commit()


async def get_lp_db() -> aiosqlite.Connection:
    """Get LinkedPilot database connection (read/write)."""
    global _lp_db
    if _lp_db is None:
        Path(LINKEDPILOT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _lp_db = await aiosqlite.connect(LINKEDPILOT_DB_PATH)
        _lp_db.row_factory = aiosqlite.Row
        await _lp_db.execute("PRAGMA journal_mode=WAL")
        await _lp_db.execute("PRAGMA synchronous=NORMAL")
        await _lp_db.execute("PRAGMA cache_size=-8000")
        await _lp_db.execute("PRAGMA temp_store=MEMORY")
        await _lp_db.execute("PRAGMA foreign_keys=ON")
        await _lp_db.executescript(SCHEMA_SQL)
        await _run_migrations(_lp_db)
        await _lp_db.commit()
    return _lp_db


async def get_crm_db() -> aiosqlite.Connection | None:
    """Get OpenOutreach CRM database connection (READ ONLY)."""
    global _crm_db
    if _crm_db is None:
        crm_path = Path(CRM_DB_PATH)
        if not crm_path.exists():
            return None
        _crm_db = await aiosqlite.connect(f"file:{CRM_DB_PATH}?mode=ro", uri=True)
        _crm_db.row_factory = aiosqlite.Row
    return _crm_db


async def close_databases():
    """Close all database connections."""
    global _lp_db, _crm_db
    if _lp_db:
        await _lp_db.close()
        _lp_db = None
    if _crm_db:
        await _crm_db.close()
        _crm_db = None
