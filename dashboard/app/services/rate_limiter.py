"""
Rate Limiter for LinkedPilot v2.

Checks daily and weekly action limits per sender, enforces work-hour
windows (IST), manages ramp-up for new senders, and checks cooldown
periods via the global contact registry.
"""

import logging
from datetime import datetime, timedelta, timezone

from app.database import get_lp_db
from app.config import (
    DAILY_LIKE_LIMIT,
    DAILY_COMMENT_LIMIT,
    DAILY_CONNECT_LIMIT,
    WEEKLY_LIKE_LIMIT,
    WEEKLY_COMMENT_LIMIT,
    WEEKLY_CONNECT_LIMIT,
    WORK_HOUR_START,
    WORK_HOUR_END,
    RAMP_UP_WEEKS,
    RAMP_UP_PERCENTAGE,
    COOLDOWN_HOURS,
)

logger = logging.getLogger(__name__)

# IST is UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Day abbreviation to weekday number (Monday=0)
_DAY_ABBR_TO_NUM = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


async def _get_db_setting(key: str, default: str = "") -> str:
    """Read a single setting from the database settings table."""
    try:
        db = await get_lp_db()
        cursor = await db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row:
            val = row["value"] if isinstance(row, dict) else row[0]
            return val if val else default
        return default
    except Exception:
        return default


async def _daily_limit_for(action_type: str) -> int:
    """Return the base daily limit for a given action type (DB first, then config)."""
    if action_type == "like":
        val = await _get_db_setting("daily_like_limit", "")
        return int(val) if val else DAILY_LIKE_LIMIT
    elif action_type == "comment":
        val = await _get_db_setting("daily_comment_limit", "")
        return int(val) if val else DAILY_COMMENT_LIMIT
    elif action_type == "connect":
        val = await _get_db_setting("daily_connect_limit", "")
        return int(val) if val else DAILY_CONNECT_LIMIT
    return 0


async def _weekly_limit_for(action_type: str) -> int:
    """Return the base weekly limit for a given action type (DB first, then config)."""
    if action_type == "like":
        val = await _get_db_setting("weekly_like_limit", "")
        return int(val) if val else WEEKLY_LIKE_LIMIT
    elif action_type == "comment":
        val = await _get_db_setting("weekly_comment_limit", "")
        return int(val) if val else WEEKLY_COMMENT_LIMIT
    elif action_type == "connect":
        val = await _get_db_setting("weekly_connect_limit", "")
        return int(val) if val else WEEKLY_CONNECT_LIMIT
    return 0


async def _get_ramp_up_factor(sender_id: int) -> float:
    """
    Return a multiplier (0.0 -- 1.0) based on sender age.

    If the sender was created within RAMP_UP_WEEKS, the limits are scaled
    down by RAMP_UP_PERCENTAGE / 100.
    """
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT created_at FROM senders WHERE id = ?", (sender_id,)
    )
    sender = await cursor.fetchone()

    if not sender:
        return 1.0

    created_at_str = sender["created_at"] if isinstance(sender, dict) else sender[0]
    try:
        created_at = datetime.fromisoformat(str(created_at_str))
    except (ValueError, TypeError):
        return 1.0

    ramp_up_cutoff = datetime.utcnow() - timedelta(weeks=RAMP_UP_WEEKS)
    if created_at > ramp_up_cutoff:
        # Sender is still in ramp-up period
        return RAMP_UP_PERCENTAGE / 100.0

    return 1.0


async def _ensure_counter_table() -> None:
    """Create daily_counters table if it doesn't exist."""
    db = await get_lp_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(date, sender_id, action_type)
        )
    """)
    await db.commit()


async def _get_daily_count(sender_id: int, action_type: str) -> int:
    """Return today's action count for a sender."""
    db = await get_lp_db()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    cursor = await db.execute(
        "SELECT count FROM daily_counters WHERE date = ? AND sender_id = ? AND action_type = ?",
        (today, sender_id, action_type),
    )
    row = await cursor.fetchone()
    if row:
        return row["count"] if isinstance(row, dict) else row[0]
    return 0


async def _get_weekly_count(sender_id: int, action_type: str) -> int:
    """Return this week's (Monday--Sunday) action count for a sender."""
    db = await get_lp_db()
    now_ist = datetime.now(IST)
    # Monday of current week
    monday = (now_ist - timedelta(days=now_ist.weekday())).strftime("%Y-%m-%d")
    cursor = await db.execute(
        """
        SELECT COALESCE(SUM(count), 0) as total
        FROM daily_counters
        WHERE sender_id = ? AND action_type = ? AND date >= ?
        """,
        (sender_id, action_type, monday),
    )
    row = await cursor.fetchone()
    if row:
        return row["total"] if isinstance(row, dict) else row[0]
    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def check_limit(sender_id: int, action_type: str) -> dict:
    """
    Check whether a sender is allowed to perform an action right now.

    Returns:
        {
            allowed: bool,
            daily_used: int,
            daily_limit: int,
            weekly_used: int,
            weekly_limit: int,
            reason: str,
        }
    """
    await _ensure_counter_table()

    ramp_factor = await _get_ramp_up_factor(sender_id)

    daily_limit = int((await _daily_limit_for(action_type)) * ramp_factor)
    weekly_limit = int((await _weekly_limit_for(action_type)) * ramp_factor)

    daily_used = await _get_daily_count(sender_id, action_type)
    weekly_used = await _get_weekly_count(sender_id, action_type)

    reason = ""
    allowed = True

    if daily_used >= daily_limit:
        allowed = False
        reason = f"Daily {action_type} limit reached ({daily_used}/{daily_limit})"
    elif weekly_used >= weekly_limit:
        allowed = False
        reason = f"Weekly {action_type} limit reached ({weekly_used}/{weekly_limit})"

    if ramp_factor < 1.0 and not reason:
        reason = f"Ramp-up active ({int(ramp_factor * 100)}% of full limits)"

    return {
        "allowed": allowed,
        "daily_used": daily_used,
        "daily_limit": daily_limit,
        "weekly_used": weekly_used,
        "weekly_limit": weekly_limit,
        "reason": reason,
    }


async def increment_counter(sender_id: int, action_type: str) -> None:
    """Increment the daily counter for a sender/action pair."""
    await _ensure_counter_table()

    db = await get_lp_db()
    today = datetime.now(IST).strftime("%Y-%m-%d")

    await db.execute(
        """
        INSERT INTO daily_counters (date, sender_id, action_type, count)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(date, sender_id, action_type)
        DO UPDATE SET count = count + 1
        """,
        (today, sender_id, action_type),
    )
    await db.commit()
    logger.debug(
        "Incremented %s counter for sender %s on %s", action_type, sender_id, today
    )


async def get_all_counters(sender_id: int) -> dict:
    """Return today's like, comment, and connect counts for a sender."""
    await _ensure_counter_table()

    like_count = await _get_daily_count(sender_id, "like")
    comment_count = await _get_daily_count(sender_id, "comment")
    connect_count = await _get_daily_count(sender_id, "connect")

    return {
        "likes": like_count,
        "comments": comment_count,
        "connects": connect_count,
    }


async def check_work_hours() -> bool:
    """Return True if current IST time is within configured work hours and work days."""
    now_ist = datetime.now(IST)

    # Check work hours (DB setting overrides config)
    start_str = await _get_db_setting("work_hour_start", "")
    end_str = await _get_db_setting("work_hour_end", "")
    hour_start = int(start_str) if start_str else WORK_HOUR_START
    hour_end = int(end_str) if end_str else WORK_HOUR_END

    current_hour = now_ist.hour + now_ist.minute / 60.0
    if not (hour_start <= current_hour < hour_end):
        return False

    # Check work days (DB setting: comma-separated day abbreviations)
    days_str = await _get_db_setting("work_days", "")
    if days_str:
        allowed_days = {_DAY_ABBR_TO_NUM.get(d.strip().lower(), -1) for d in days_str.split(",")}
        if now_ist.weekday() not in allowed_days:
            return False

    return True


async def check_cooldown(profile_url: str, sender_id: int) -> bool:
    """
    Check the global contact registry for cooldown.

    Returns True if the contact is ON cooldown (i.e., should NOT be
    contacted right now) -- any sender touched it in the last 3 days.
    """
    db = await get_lp_db()
    cooldown_since = (
        datetime.utcnow() - timedelta(hours=COOLDOWN_HOURS)
    ).isoformat()

    cursor = await db.execute(
        """
        SELECT COUNT(*) as cnt
        FROM global_contact_registry
        WHERE profile_url = ? AND acted_at >= ?
        """,
        (profile_url, cooldown_since),
    )
    row = await cursor.fetchone()
    count = row["cnt"] if isinstance(row, dict) else row[0] if row else 0
    return count > 0
