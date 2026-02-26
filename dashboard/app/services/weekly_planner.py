"""
Weekly Planner for LinkedPilot v2.

Distributes a weekly action budget across remaining work days in the current
week. The distribution is randomised but roughly even so daily volumes look
natural.
"""

import logging
import random
from datetime import datetime, timedelta, timezone

from app.database import get_lp_db

logger = logging.getLogger(__name__)

# IST is UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Monday = 0 ... Friday = 4 are work days
WORK_DAYS = {0, 1, 2, 3, 4}


def _remaining_work_days_this_week() -> list[str]:
    """Return a list of date strings (YYYY-MM-DD) for remaining work days
    in the current week, *including* today if today is a work day."""
    now_ist = datetime.now(IST)
    today = now_ist.date()
    # Monday of this week
    monday = today - timedelta(days=today.weekday())

    remaining: list[str] = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        if day < today:
            continue
        if day.weekday() in WORK_DAYS:
            remaining.append(day.isoformat())
    return remaining


def _distribute_budget(total: int, buckets: int) -> list[int]:
    """Distribute *total* into *buckets* roughly-even random amounts that
    sum exactly to *total*."""
    if buckets <= 0:
        return []
    if buckets == 1:
        return [total]

    # Start with a flat base
    base = total // buckets
    remainder = total % buckets
    distribution = [base] * buckets

    # Spread the remainder randomly
    for i in random.sample(range(buckets), remainder):
        distribution[i] += 1

    # Add a bit of noise: shift units between adjacent days
    for _ in range(buckets):
        i = random.randint(0, buckets - 1)
        j = random.randint(0, buckets - 1)
        if i != j and distribution[i] > 1:
            shift = random.randint(1, max(1, distribution[i] // 3))
            distribution[i] -= shift
            distribution[j] += shift

    return distribution


async def _get_used_this_week(sender_id: int, action_type: str) -> int:
    """Sum actions already performed this week (Mon--Sun)."""
    db = await get_lp_db()
    now_ist = datetime.now(IST)
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

async def plan_week(
    sender_id: int,
    action_type: str,
    weekly_limit: int,
) -> dict[str, int]:
    """
    Distribute the remaining weekly budget across the remaining work days.

    Returns:
        A dict mapping date (YYYY-MM-DD) to the planned daily budget.
        Example: {"2026-02-26": 12, "2026-02-27": 14, ...}
    """
    used = await _get_used_this_week(sender_id, action_type)
    budget_left = max(0, weekly_limit - used)

    days = _remaining_work_days_this_week()
    if not days:
        logger.info(
            "No remaining work days this week for sender %s (%s).",
            sender_id, action_type,
        )
        return {}

    amounts = _distribute_budget(budget_left, len(days))
    plan = dict(zip(days, amounts))

    logger.debug(
        "Weekly plan for sender %s (%s): used=%d, budget_left=%d, plan=%s",
        sender_id, action_type, used, budget_left, plan,
    )
    return plan


async def get_today_budget(sender_id: int, action_type: str) -> int:
    """
    Return how many actions are allowed today based on the weekly plan.

    This generates a fresh plan each call (plans are stateless) and returns
    today's slot. If today is not a work day, returns 0.
    """
    today_str = datetime.now(IST).date().isoformat()

    # We need the weekly limit -- derive from config
    from app.config import WEEKLY_LIKE_LIMIT, WEEKLY_COMMENT_LIMIT

    if action_type == "like":
        weekly_limit = WEEKLY_LIKE_LIMIT
    elif action_type == "comment":
        weekly_limit = WEEKLY_COMMENT_LIMIT
    else:
        return 0

    plan = await plan_week(sender_id, action_type, weekly_limit)
    return plan.get(today_str, 0)
