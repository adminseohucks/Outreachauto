"""
LinkedIn People Search via Voyager API.

Uses the sender's authenticated Playwright session to call LinkedIn's
internal search API. Returns up to 1000 structured results per search.
"""

import logging
import json
from typing import Optional

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# LinkedIn Voyager API endpoints
SEARCH_URL = "https://www.linkedin.com/voyager/api/search/dash/clusters"
PROFILE_URL = "https://www.linkedin.com/voyager/api/identity/profiles/{vanity_name}"

# JavaScript that runs inside the browser to call the Voyager API
SEARCH_JS = """
async ({ keywords, start, count }) => {
    const csrfToken = document.cookie
        .split('; ')
        .find(c => c.startsWith('JSESSIONID='))
        ?.split('=')[1]
        ?.replace(/"/g, '') || '';

    if (!csrfToken) return { error: 'No CSRF token — not logged in?' };

    const params = new URLSearchParams({
        decorationId: 'com.linkedin.voyager.dash.deco.search.SearchClusterCollection-174',
        origin: 'GLOBAL_SEARCH_HEADER',
        q: 'all',
        start: String(start),
        count: String(count),
    });

    // Build the query string
    let queryParts = [`keywords:${keywords}`, 'resultType:(PEOPLE)'];
    params.set('query', '(' + queryParts.join(',') + ')');

    try {
        const resp = await fetch(
            'https://www.linkedin.com/voyager/api/search/dash/clusters?' + params.toString(),
            {
                headers: {
                    'csrf-token': csrfToken,
                    'accept': 'application/vnd.linkedin.normalized+json+2.1',
                    'x-restli-protocol-version': '2.0.0',
                },
                credentials: 'include',
            }
        );

        if (!resp.ok) return { error: 'API returned ' + resp.status };
        const data = await resp.json();
        return data;
    } catch (e) {
        return { error: e.message };
    }
}
"""

# JavaScript to fetch a single profile via Voyager API
PROFILE_JS = """
async (vanityName) => {
    const csrfToken = document.cookie
        .split('; ')
        .find(c => c.startsWith('JSESSIONID='))
        ?.split('=')[1]
        ?.replace(/"/g, '') || '';

    if (!csrfToken) return { error: 'No CSRF token' };

    try {
        const resp = await fetch(
            'https://www.linkedin.com/voyager/api/identity/profiles/' + vanityName,
            {
                headers: {
                    'csrf-token': csrfToken,
                    'accept': 'application/vnd.linkedin.normalized+json+2.1',
                },
                credentials: 'include',
            }
        );
        if (!resp.ok) return { error: 'API returned ' + resp.status };
        return await resp.json();
    } catch (e) {
        return { error: e.message };
    }
}
"""


def _parse_search_results(raw_data: dict) -> list[dict]:
    """Parse Voyager API response into clean lead dicts."""
    leads = []

    if "error" in raw_data:
        logger.error("Search API error: %s", raw_data["error"])
        return leads

    # The results are nested in the 'included' array
    included = raw_data.get("included", [])

    # Build a lookup of entity URNs to their data
    entities = {}
    for item in included:
        urn = item.get("entityUrn", item.get("$id", ""))
        entities[urn] = item

    # Find person results
    for item in included:
        recipe = item.get("$type", "")

        # Look for search result items
        if "SearchResult" not in recipe and "MiniProfile" not in recipe:
            continue

        # Try to extract from different response formats
        profile = None
        if "com.linkedin.voyager.dash.search.EntityResultViewModel" in recipe:
            profile = _extract_from_entity_result(item, entities)
        elif "com.linkedin.voyager.identity.shared.MiniProfile" in recipe:
            profile = _extract_from_mini_profile(item)

        if profile and profile.get("profile_url"):
            # Deduplicate by profile_url
            if not any(l["profile_url"] == profile["profile_url"] for l in leads):
                leads.append(profile)

    return leads


def _extract_from_entity_result(item: dict, entities: dict) -> Optional[dict]:
    """Extract lead from EntityResultViewModel."""
    title_data = item.get("title", {})
    full_name = title_data.get("text", "") if isinstance(title_data, dict) else str(title_data)

    summary_data = item.get("primarySubtitle", {})
    headline = summary_data.get("text", "") if isinstance(summary_data, dict) else str(summary_data)

    location_data = item.get("secondarySubtitle", {})
    location = location_data.get("text", "") if isinstance(location_data, dict) else str(location_data)

    # Get profile URL from navigationUrl
    nav_url = item.get("navigationUrl", "")
    profile_url = ""
    if "/in/" in nav_url:
        # Clean up the URL
        profile_url = nav_url.split("?")[0]
        if not profile_url.startswith("https://"):
            profile_url = "https://www.linkedin.com" + profile_url

    if not full_name or not profile_url:
        return None

    first_name = full_name.split()[0] if full_name else ""

    return {
        "full_name": full_name,
        "first_name": first_name,
        "headline": headline,
        "company": _extract_company(headline),
        "location": location,
        "profile_url": profile_url,
    }


def _extract_from_mini_profile(item: dict) -> Optional[dict]:
    """Extract lead from MiniProfile."""
    first_name = item.get("firstName", "")
    last_name = item.get("lastName", "")
    full_name = f"{first_name} {last_name}".strip()
    vanity_name = item.get("publicIdentifier", "")
    headline = item.get("occupation", "")

    if not vanity_name:
        return None

    profile_url = f"https://www.linkedin.com/in/{vanity_name}"

    return {
        "full_name": full_name,
        "first_name": first_name,
        "headline": headline,
        "company": _extract_company(headline),
        "location": "",
        "profile_url": profile_url,
    }


def _extract_company(headline: str) -> str:
    """Try to extract company name from headline like 'Engineer at Google'."""
    if not headline:
        return ""
    for sep in [" at ", " @ ", " - ", " | "]:
        if sep in headline:
            return headline.split(sep)[-1].strip()
    return ""


async def search_people(
    page: Page,
    keywords: str,
    max_results: int = 100,
) -> list[dict]:
    """
    Search LinkedIn for people matching keywords.

    Uses the Voyager API through the sender's authenticated browser session.
    Returns up to max_results leads as list of dicts.
    """
    all_leads = []
    batch_size = 49  # LinkedIn max per request
    start = 0

    # Make sure we're on LinkedIn first
    current_url = page.url
    if "linkedin.com" not in current_url:
        await page.goto("https://www.linkedin.com/feed/", timeout=30000)
        await page.wait_for_timeout(2000)

    while start < max_results:
        count = min(batch_size, max_results - start)

        logger.info("Searching LinkedIn: keywords='%s' start=%d count=%d", keywords, start, count)

        raw = await page.evaluate(
            SEARCH_JS,
            {"keywords": keywords, "start": start, "count": count},
        )

        if not raw or "error" in raw:
            error_msg = raw.get("error", "Unknown error") if raw else "No response"
            logger.error("Search failed at start=%d: %s", start, error_msg)
            break

        leads = _parse_search_results(raw)
        if not leads:
            logger.info("No more results at start=%d", start)
            break

        all_leads.extend(leads)
        logger.info("Got %d leads (total so far: %d)", len(leads), len(all_leads))

        start += batch_size

        # Small delay between pages to avoid rate limiting
        await page.wait_for_timeout(1500)

    # Deduplicate by profile_url
    seen = set()
    unique = []
    for lead in all_leads:
        if lead["profile_url"] not in seen:
            seen.add(lead["profile_url"])
            unique.append(lead)

    logger.info("Search complete: %d unique leads for '%s'", len(unique), keywords)
    return unique


async def enrich_profile(page: Page, profile_url: str) -> dict:
    """
    Enrich a lead profile by fetching full details from LinkedIn.

    Returns dict with updated fields: headline, location, about, experience, etc.
    """
    # Extract vanity name from URL
    vanity_name = ""
    if "/in/" in profile_url:
        vanity_name = profile_url.rstrip("/").split("/in/")[-1].split("?")[0]

    if not vanity_name:
        return {"error": "Invalid profile URL"}

    # Make sure we're on LinkedIn
    current_url = page.url
    if "linkedin.com" not in current_url:
        await page.goto("https://www.linkedin.com/feed/", timeout=30000)
        await page.wait_for_timeout(2000)

    raw = await page.evaluate(PROFILE_JS, vanity_name)

    if not raw or "error" in raw:
        error_msg = raw.get("error", "Unknown") if raw else "No response"
        return {"error": error_msg}

    # Parse profile data
    profile = {}
    if isinstance(raw, dict):
        profile["first_name"] = raw.get("firstName", "")
        profile["last_name"] = raw.get("lastName", "")
        profile["full_name"] = f"{profile['first_name']} {profile['last_name']}".strip()
        profile["headline"] = raw.get("headline", "")
        profile["location"] = raw.get("locationName", raw.get("geoLocationName", ""))
        profile["industry"] = raw.get("industryName", "")
        profile["summary"] = raw.get("summary", "")
        profile["profile_url"] = profile_url
        profile["company"] = _extract_company(profile["headline"])

    return profile
