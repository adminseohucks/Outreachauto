"""
OpenOutreach CRM Database Reader (READ ONLY).

Reads leads from OpenOutreach's crm.db. If the database does not exist,
generates mock data with ~50 sample Indian leads for development.
"""

import random
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from app.config import CRM_DB_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock data pools
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh",
    "Ayaan", "Krishna", "Ishaan", "Rohan", "Rahul", "Amit", "Vikram",
    "Suresh", "Ananya", "Diya", "Saanvi", "Priya", "Meera", "Neha",
    "Kavya", "Isha", "Riya", "Pooja", "Shreya", "Tanvi", "Divya",
    "Nisha", "Deepika",
]

LAST_NAMES = [
    "Sharma", "Verma", "Patel", "Gupta", "Singh", "Kumar", "Reddy",
    "Nair", "Iyer", "Joshi", "Mehta", "Agarwal", "Chatterjee", "Bose",
    "Pillai", "Rao", "Desai", "Kulkarni", "Mishra", "Banerjee",
    "Srinivasan", "Tiwari", "Pandey", "Saxena", "Kapoor",
]

COMPANIES = [
    "Infosys", "TCS", "Wipro", "HCL Technologies", "Tech Mahindra",
    "Zoho", "Freshworks", "Razorpay", "CRED", "Zerodha",
    "PhonePe", "Flipkart", "Swiggy", "Zomato", "Ola",
    "Paytm", "Dream11", "Byju's", "Unacademy", "Meesho",
    "Reliance Jio", "Mahindra Group", "Tata Digital", "Mindtree", "Mphasis",
]

HEADLINES = [
    "Software Engineer at {company}",
    "Senior Developer at {company}",
    "Product Manager at {company}",
    "Engineering Manager at {company}",
    "Full Stack Developer at {company}",
    "Data Scientist at {company}",
    "VP of Engineering at {company}",
    "CTO at {company}",
    "Founder & CEO at {company}",
    "Head of Growth at {company}",
    "DevOps Engineer at {company}",
    "Machine Learning Engineer at {company}",
    "Technical Lead at {company}",
    "Backend Engineer at {company}",
    "Frontend Developer at {company}",
]

LOCATIONS = [
    "Bengaluru, Karnataka, India",
    "Mumbai, Maharashtra, India",
    "Hyderabad, Telangana, India",
    "Pune, Maharashtra, India",
    "Chennai, Tamil Nadu, India",
    "Delhi, India",
    "Gurugram, Haryana, India",
    "Noida, Uttar Pradesh, India",
    "Kolkata, West Bengal, India",
    "Ahmedabad, Gujarat, India",
    "Kochi, Kerala, India",
    "Jaipur, Rajasthan, India",
]

STATUSES = ["discovered", "enriched", "qualified", "connected", "replied"]


# ---------------------------------------------------------------------------
# Mock DB creation
# ---------------------------------------------------------------------------

async def ensure_mock_crm_db() -> None:
    """Create a mock crm.db with ~50 sample Indian leads if it doesn't exist."""
    import os

    if os.path.exists(CRM_DB_PATH):
        logger.info("crm.db already exists at %s -- skipping mock generation.", CRM_DB_PATH)
        return

    logger.info("crm.db not found -- generating mock data at %s", CRM_DB_PATH)

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(CRM_DB_PATH) or ".", exist_ok=True)

    async with aiosqlite.connect(CRM_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                headline TEXT,
                company TEXT,
                location TEXT,
                profile_url TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                created_at TEXT NOT NULL
            )
        """)

        leads = []
        now = datetime.utcnow()

        for i in range(50):
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            full = f"{first} {last}"
            company = random.choice(COMPANIES)
            headline = random.choice(HEADLINES).format(company=company)
            location = random.choice(LOCATIONS)
            status = random.choices(
                STATUSES, weights=[30, 25, 20, 15, 10], k=1
            )[0]
            profile_url = f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-{random.randint(1000, 9999)}"
            created_at = (now - timedelta(days=random.randint(0, 60))).isoformat()

            leads.append((
                full, first, last, headline, company,
                location, profile_url, status, created_at,
            ))

        await db.executemany(
            """
            INSERT INTO leads
                (full_name, first_name, last_name, headline, company,
                 location, profile_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            leads,
        )
        await db.commit()

    logger.info("Mock crm.db created with %d leads.", len(leads))


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

async def get_leads(
    filters: Optional[dict] = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """
    Read leads from crm.db with optional filtering and pagination.

    Supported filter keys:
        title, company, location, status, date_from, date_to, search

    Returns:
        {leads: [...], total: int, page: int, per_page: int, pages: int}
    """
    filters = filters or {}
    conditions: list[str] = []
    params: list = []

    if filters.get("title"):
        conditions.append("headline LIKE ?")
        params.append(f"%{filters['title']}%")

    if filters.get("company"):
        conditions.append("company LIKE ?")
        params.append(f"%{filters['company']}%")

    if filters.get("location"):
        conditions.append("location LIKE ?")
        params.append(f"%{filters['location']}%")

    if filters.get("status"):
        conditions.append("status = ?")
        params.append(filters["status"])

    if filters.get("date_from"):
        conditions.append("created_at >= ?")
        params.append(filters["date_from"])

    if filters.get("date_to"):
        conditions.append("created_at <= ?")
        params.append(filters["date_to"])

    if filters.get("search"):
        search_term = f"%{filters['search']}%"
        conditions.append(
            "(full_name LIKE ? OR headline LIKE ? OR company LIKE ? OR location LIKE ?)"
        )
        params.extend([search_term, search_term, search_term, search_term])

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    async with aiosqlite.connect(CRM_DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total count
        count_sql = f"SELECT COUNT(*) as cnt FROM leads {where_clause}"
        async with db.execute(count_sql, params) as cursor:
            row = await cursor.fetchone()
            total = row["cnt"] if row else 0

        # Paginated results
        offset = (page - 1) * per_page
        data_sql = (
            f"SELECT * FROM leads {where_clause} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        async with db.execute(data_sql, params + [per_page, offset]) as cursor:
            rows = await cursor.fetchall()
            leads = [dict(row) for row in rows]

    pages = max(1, -(-total // per_page))  # ceil division

    return {
        "leads": leads,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
    }


async def get_lead_by_id(lead_id: int) -> Optional[dict]:
    """Return a single lead by its ID, or None if not found."""
    async with aiosqlite.connect(CRM_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_lead_stats() -> dict:
    """Return aggregate lead statistics: total, connected, qualified counts."""
    async with aiosqlite.connect(CRM_DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Single query instead of 3 separate ones
        async with db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'connected' THEN 1 ELSE 0 END) AS connected,
                SUM(CASE WHEN status = 'qualified' THEN 1 ELSE 0 END) AS qualified
            FROM leads
            """
        ) as cursor:
            row = await cursor.fetchone()

    return {
        "total": row["total"] if row else 0,
        "connected": row["connected"] if row else 0,
        "qualified": row["qualified"] if row else 0,
    }
