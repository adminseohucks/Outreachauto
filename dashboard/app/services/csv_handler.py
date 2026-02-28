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


async def import_csv_to_list(
    file_content: bytes,
    list_id: int,
    column_mapping: dict | None = None,
) -> dict:
    """
    Parse CSV bytes and insert leads into custom_list_leads.

    When column_mapping is provided (from the header-matching modal), it is a
    dict mapping CSV header names to DB field names, e.g.:
        {"Person Linkedin Url": "profile_url", "Name": "full_name", ...}

    When column_mapping is None, falls back to expecting exact DB column names.

    Required: at least one CSV column must map to profile_url.
    Optional DB fields: full_name, first_name, headline, company, location.

    Duplicates (same profile_url + list_id) are skipped.
    Source is set to 'csv_upload'.

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

    if not reader.fieldnames:
        return {"imported": 0, "skipped": 0, "errors": ["CSV file is empty or has no headers."]}

    # Build a reverse lookup: db_field -> csv_header
    # If column_mapping provided: {"CSV Header": "db_field"} → invert to {"db_field": "CSV Header"}
    if column_mapping:
        reverse_map = {}  # db_field -> csv_header
        for csv_col, db_field in column_mapping.items():
            if db_field:  # skip empty (unmapped) columns
                reverse_map[db_field] = csv_col

        if "profile_url" not in reverse_map:
            return {
                "imported": 0,
                "skipped": 0,
                "errors": ["No column mapped to 'profile_url'. It is required."],
            }
    else:
        # Fallback: expect exact column names in CSV
        if "profile_url" not in reader.fieldnames:
            return {
                "imported": 0,
                "skipped": 0,
                "errors": ["CSV must contain a 'profile_url' column."],
            }
        reverse_map = {f: f for f in reader.fieldnames}

    def _get(row: dict, db_field: str) -> str:
        """Get a value from the CSV row using the column mapping."""
        csv_col = reverse_map.get(db_field, "")
        return (row.get(csv_col) or "").strip() if csv_col else ""

    now = datetime.utcnow().isoformat()

    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        profile_url = _get(row, "profile_url")
        if not profile_url:
            errors.append(f"Row {row_num}: missing profile_url — skipped.")
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

        full_name = _get(row, "full_name")
        first_name = _get(row, "first_name")
        headline = _get(row, "headline")
        company = _get(row, "company")
        location = _get(row, "location")

        # If full_name is empty but first_name exists, use first_name as full_name
        if not full_name and first_name:
            full_name = first_name

        # Fallback: if still no name, use "Unknown"
        if not full_name:
            full_name = "Unknown"

        try:
            await db.execute(
                """
                INSERT INTO custom_list_leads
                    (list_id, profile_url, full_name, first_name, headline, company, location, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'csv_upload', ?)
                """,
                (list_id, profile_url, full_name, first_name, headline, company, location, now),
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
