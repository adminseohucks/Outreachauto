"""
CSV Import / Export handler for LinkedPilot v2.

Exports lead lists to CSV files and imports CSV uploads into custom lists.
"""

import csv
import io
import os
import logging
from datetime import datetime
from typing import List

import aiofiles

from app.config import EXPORTS_DIR
from app.database import get_lp_db

logger = logging.getLogger(__name__)


async def export_leads_csv(leads: List[dict], filename: str) -> str:
    """
    Write a list of lead dicts to a CSV file in EXPORTS_DIR.

    Args:
        leads: List of lead dictionaries.
        filename: Desired file name (e.g. "leads_export.csv").

    Returns:
        Absolute path to the written CSV file.
    """
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    file_path = os.path.join(EXPORTS_DIR, filename)

    if not leads:
        # Write an empty file with headers only
        headers = [
            "id", "full_name", "first_name", "last_name",
            "headline", "company", "location", "profile_url",
            "status", "created_at",
        ]
    else:
        headers = list(leads[0].keys())

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)

    async with aiofiles.open(file_path, mode="w", encoding="utf-8", newline="") as f:
        await f.write(output.getvalue())

    logger.info("Exported %d leads to %s", len(leads), file_path)
    return file_path


async def import_csv_to_list(file_content: bytes, list_id: int) -> dict:
    """
    Parse CSV bytes and insert leads into custom_list_leads.

    Required CSV column: profile_url
    Optional columns: full_name, headline, company, location

    Duplicates (same profile_url + list_id) are skipped.
    Source is set to 'csv_upload'.

    Args:
        file_content: Raw bytes of the uploaded CSV file.
        list_id: ID of the custom list to insert leads into.

    Returns:
        {imported: int, skipped: int, errors: list[str]}
    """
    db = await get_lp_db()

    imported = 0
    skipped = 0
    errors: List[str] = []

    try:
        text = file_content.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        try:
            text = file_content.decode("latin-1")
        except UnicodeDecodeError:
            return {"imported": 0, "skipped": 0, "errors": ["Unable to decode CSV file."]}

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames or "profile_url" not in reader.fieldnames:
        return {
            "imported": 0,
            "skipped": 0,
            "errors": ["CSV must contain a 'profile_url' column."],
        }

    now = datetime.utcnow().isoformat()

    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        profile_url = (row.get("profile_url") or "").strip()
        if not profile_url:
            errors.append(f"Row {row_num}: missing profile_url -- skipped.")
            skipped += 1
            continue

        # Check for duplicate
        cursor = await db.execute(
            "SELECT id FROM custom_list_leads WHERE list_id = ? AND profile_url = ?",
            (list_id, profile_url),
        )
        existing = await cursor.fetchone()
        if existing:
            skipped += 1
            continue

        full_name = (row.get("full_name") or "").strip()
        headline = (row.get("headline") or "").strip()
        company = (row.get("company") or "").strip()
        location = (row.get("location") or "").strip()

        try:
            await db.execute(
                """
                INSERT INTO custom_list_leads
                    (list_id, profile_url, full_name, headline, company, location, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'csv_upload', ?)
                """,
                (list_id, profile_url, full_name, headline, company, location, now),
            )
            imported += 1
        except Exception as exc:
            errors.append(f"Row {row_num}: {exc}")
            skipped += 1

    await db.commit()
    logger.info(
        "CSV import to list %d: imported=%d, skipped=%d, errors=%d",
        list_id, imported, skipped, len(errors),
    )

    return {"imported": imported, "skipped": skipped, "errors": errors}
