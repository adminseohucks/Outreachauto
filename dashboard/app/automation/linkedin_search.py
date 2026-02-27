"""
LinkedIn People Search via Voyager GraphQL API.

Uses the sender's authenticated Playwright session to call LinkedIn's
internal GraphQL search API.  Returns up to 1000 structured results.

Endpoint and query format based on linkedin-api v2.3.1 (the most popular
open-source Python wrapper for LinkedIn's Voyager API).

Supports filters: location (geoUrn), network degree, company size.
"""

import logging
from typing import Optional

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# queryId from linkedin-api v2.3.1 — this is the hash LinkedIn uses
# to identify the "search clusters" GraphQL query.
QUERY_ID = "voyagerSearchDashClusters.b0928897b71bd00a5a7291755dcd64f0"

# ---------------------------------------------------------------------------
# JavaScript to lookup geo URN for a location name (e.g. "Mumbai" → urn)
# ---------------------------------------------------------------------------
GEO_LOOKUP_JS = """
async (locationText) => {
    const csrfToken = document.cookie
        .split('; ')
        .find(c => c.startsWith('JSESSIONID='))
        ?.split('=')[1]
        ?.replace(/"/g, '') || '';
    if (!csrfToken) return { error: 'No CSRF token — not logged in?' };

    try {
        const resp = await fetch(
            'https://www.linkedin.com/voyager/api/typeahead/hitsV2?' +
            new URLSearchParams({
                keywords: locationText,
                origin: 'GLOBAL_SEARCH_HEADER',
                q: 'type',
                type: 'GEO',
            }).toString(),
            {
                headers: {
                    'csrf-token': csrfToken,
                    'accept': 'application/vnd.linkedin.normalized+json+2.1',
                },
                credentials: 'include',
            }
        );
        if (!resp.ok) return { error: 'Geo lookup returned ' + resp.status };
        const data = await resp.json();

        // Extract geo URN from included array
        const included = data?.included || [];
        for (const el of included) {
            const urn = el?.entityUrn || el?.targetUrn || '';
            if (urn.includes('geo:')) return { geoUrn: urn };
        }

        // Fallback: check data.data.elements
        const elements = data?.data?.elements || [];
        for (const el of elements) {
            const urn = el?.targetUrn || el?.entityUrn || '';
            if (urn.includes('geo:')) return { geoUrn: urn };
        }

        return { error: 'No geo URN found for: ' + locationText };
    } catch (e) {
        return { error: e.message };
    }
}
"""

# ---------------------------------------------------------------------------
# JavaScript to perform Voyager GraphQL people search with filters
# Matches the format used by linkedin-api v2.3.1 (verified working)
# ---------------------------------------------------------------------------
SEARCH_JS = """
async ({ keywords, start, count, filtersList }) => {
    const csrfToken = document.cookie
        .split('; ')
        .find(c => c.startsWith('JSESSIONID='))
        ?.split('=')[1]
        ?.replace(/"/g, '') || '';

    if (!csrfToken) return { error: 'No CSRF token — not logged in? Please open LinkedIn and login first.' };

    // Build the variables portion matching linkedin-api format
    // Format: (start:0,origin:GLOBAL_SEARCH_HEADER,query:(keywords:CEO,flagshipSearchIntent:SEARCH_SRP,queryParameters:List((key:resultType,value:List(PEOPLE))),includeFiltersInResponse:false))
    const keywordsPart = keywords ? 'keywords:' + encodeURIComponent(keywords) + ',' : '';
    const filtersStr = filtersList || '(key:resultType,value:List(PEOPLE))';

    const variables = '(start:' + start + ',origin:GLOBAL_SEARCH_HEADER,' +
        'query:(' + keywordsPart +
        'flagshipSearchIntent:SEARCH_SRP,' +
        'queryParameters:List(' + filtersStr + '),' +
        'includeFiltersInResponse:false))';

    const queryId = 'voyagerSearchDashClusters.b0928897b71bd00a5a7291755dcd64f0';
    const url = 'https://www.linkedin.com/voyager/api/graphql?variables=' + variables + '&queryId=' + queryId;

    try {
        const resp = await fetch(url, {
            headers: {
                'csrf-token': csrfToken,
                'accept': 'application/vnd.linkedin.normalized+json+2.1',
                'x-restli-protocol-version': '2.0.0',
            },
            credentials: 'include',
        });

        if (!resp.ok) return { error: 'API returned ' + resp.status + ' ' + resp.statusText };
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


def _build_filters_list(
    geo_urn: str | None = None,
    network: list[str] | None = None,
    company_size: list[str] | None = None,
) -> str:
    """Build the LinkedIn queryParameters filter string.

    Format: (key:resultType,value:List(PEOPLE)),(key:network,value:List(F | S))
    Separator between values is ' | ' (with spaces) to match linkedin-api.
    """
    filters = ["(key:resultType,value:List(PEOPLE))"]
    if geo_urn:
        filters.append(f"(key:geoUrn,value:List({geo_urn}))")
    if network:
        values = " | ".join(network)
        filters.append(f"(key:network,value:List({values}))")
    if company_size:
        values = " | ".join(company_size)
        filters.append(f"(key:companySize,value:List({values}))")
    return ",".join(filters)


def _parse_search_results(raw_data: dict) -> list[dict]:
    """Parse GraphQL Voyager search response into clean lead dicts.

    Response format (from /graphql endpoint):
        data.searchDashClustersByAll.elements[].items[].item.entityResult
    Each entityResult has: title, primarySubtitle, secondarySubtitle, navigationUrl
    """
    leads = []

    if "error" in raw_data:
        logger.error("Search API error: %s", raw_data["error"])
        return leads

    # --- Parse GraphQL response format ---
    data_section = raw_data.get("data", {})
    clusters = data_section.get("searchDashClustersByAll", {})

    if not clusters:
        logger.warning(
            "No searchDashClustersByAll in response. Top keys: %s",
            list(raw_data.keys())[:10],
        )
        # Fallback: try the old 'included' format
        return _parse_included_format(raw_data)

    cluster_type = clusters.get("_type", "")
    if cluster_type and "CollectionResponse" not in cluster_type:
        logger.warning("Unexpected cluster type: %s", cluster_type)

    for cluster_element in clusters.get("elements", []):
        c_type = cluster_element.get("_type", "")
        if "SearchClusterViewModel" not in c_type:
            continue

        for search_item in cluster_element.get("items", []):
            s_type = search_item.get("_type", "")
            if "SearchItem" not in s_type:
                continue

            entity = search_item.get("item", {}).get("entityResult", {})
            if not entity:
                continue

            e_type = entity.get("_type", "")
            if "EntityResultViewModel" not in e_type:
                continue

            profile = _extract_from_entity_result(entity)
            if profile and profile.get("profile_url"):
                if not any(l["profile_url"] == profile["profile_url"] for l in leads):
                    leads.append(profile)

    return leads


def _parse_included_format(raw_data: dict) -> list[dict]:
    """Fallback parser for the older 'included' array response format."""
    leads = []
    included = raw_data.get("included", [])

    if not included:
        return leads

    for item in included:
        recipe = item.get("$type", "")
        profile = None

        if "EntityResultViewModel" in recipe or "EntityResult" in recipe:
            profile = _extract_from_entity_result(item)
        elif "MiniProfile" in recipe:
            profile = _extract_from_mini_profile(item)

        if profile and profile.get("profile_url"):
            if not any(l["profile_url"] == profile["profile_url"] for l in leads):
                leads.append(profile)

    return leads


def _extract_from_entity_result(item: dict, entities: dict | None = None) -> Optional[dict]:
    """Extract lead from EntityResultViewModel."""
    title_data = item.get("title", {})
    full_name = title_data.get("text", "") if isinstance(title_data, dict) else str(title_data)

    summary_data = item.get("primarySubtitle", {})
    headline = summary_data.get("text", "") if isinstance(summary_data, dict) else str(summary_data)

    location_data = item.get("secondarySubtitle", {})
    location = location_data.get("text", "") if isinstance(location_data, dict) else str(location_data)

    # Get profile URL from navigationUrl or entityUrn
    nav_url = item.get("navigationUrl", "")
    profile_url = ""
    if "/in/" in nav_url:
        profile_url = nav_url.split("?")[0]
        if not profile_url.startswith("https://"):
            profile_url = "https://www.linkedin.com" + profile_url

    if not profile_url:
        # Try to extract from entityUrn
        urn = item.get("entityUrn", "")
        if urn:
            # urn format: urn:li:fsd_profile:ACoAAXXXX
            parts = urn.split(":")
            if len(parts) >= 4:
                member_id = parts[-1]
                # We need the vanity name, not the member ID
                # Skip if we can't get a proper URL
                pass

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


async def _resolve_geo_urn(page: Page, location_text: str) -> Optional[str]:
    """Resolve a location name to a LinkedIn geoUrn using typeahead API."""
    if not location_text or not location_text.strip():
        return None

    try:
        result = await page.evaluate(GEO_LOOKUP_JS, location_text.strip())
        if result and "geoUrn" in result:
            logger.info("Resolved '%s' -> %s", location_text, result["geoUrn"])
            return result["geoUrn"]
        else:
            error = result.get("error", "Unknown") if result else "No response"
            logger.warning("Geo lookup failed for '%s': %s", location_text, error)
            return None
    except Exception as exc:
        logger.warning("Geo lookup exception for '%s': %s", location_text, exc)
        return None


async def search_people(
    page: Page,
    keywords: str,
    max_results: int = 100,
    location: str = "",
    network: list[str] | None = None,
    company_size: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    """
    Search LinkedIn for people matching keywords and filters.

    Uses the Voyager GraphQL API through the sender's authenticated browser.

    Parameters
    ----------
    page : Playwright Page (must be logged into LinkedIn)
    keywords : Search keywords
    max_results : Max number of results (capped at 999)
    location : Location text (e.g. "Mumbai", "India") — resolved to geoUrn
    network : Connection degree filter ["F"=1st, "S"=2nd, "O"=3rd+]
    company_size : Company size codes ["B"=1-10 .. "I"=10001+]

    Returns
    -------
    tuple of (leads_list, error_message_or_None)
    """
    all_leads = []
    batch_size = 49  # LinkedIn max per request
    start = 0
    error_msg = None

    # Make sure we're on LinkedIn first
    current_url = page.url
    if "linkedin.com" not in current_url:
        try:
            await page.goto("https://www.linkedin.com/feed/", timeout=30000)
            await page.wait_for_timeout(3000)
        except Exception as exc:
            return [], f"Could not navigate to LinkedIn: {exc}"

    # Resolve location to geoUrn
    geo_urn = None
    if location:
        geo_urn = await _resolve_geo_urn(page, location)
        if not geo_urn:
            # Fallback: append location to keywords
            keywords = f"{keywords} {location}"
            logger.info("Geo lookup failed, appending location to keywords: '%s'", keywords)

    # Build filters string in LinkedIn format
    filters_list = _build_filters_list(
        geo_urn=geo_urn,
        network=network,
        company_size=company_size,
    )

    while start < max_results:
        count = min(batch_size, max_results - start)

        logger.info(
            "Searching LinkedIn: keywords='%s' start=%d count=%d filters=%s",
            keywords, start, count, filters_list,
        )

        try:
            raw = await page.evaluate(
                SEARCH_JS,
                {"keywords": keywords, "start": start, "count": count, "filtersList": filters_list},
            )
        except Exception as exc:
            error_msg = f"Browser error: {exc}"
            logger.error("page.evaluate failed: %s", exc)
            break

        if not raw:
            error_msg = "No response from LinkedIn API"
            logger.error(error_msg)
            break

        if "error" in raw:
            error_msg = raw["error"]
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
    return unique, error_msg


async def enrich_profile(page: Page, profile_url: str) -> dict:
    """
    Enrich a lead profile by fetching full details from LinkedIn.

    Returns dict with updated fields: headline, location, about, experience, etc.
    """
    vanity_name = ""
    if "/in/" in profile_url:
        vanity_name = profile_url.rstrip("/").split("/in/")[-1].split("?")[0]

    if not vanity_name:
        return {"error": "Invalid profile URL"}

    current_url = page.url
    if "linkedin.com" not in current_url:
        await page.goto("https://www.linkedin.com/feed/", timeout=30000)
        await page.wait_for_timeout(2000)

    raw = await page.evaluate(PROFILE_JS, vanity_name)

    if not raw or "error" in raw:
        error_msg = raw.get("error", "Unknown") if raw else "No response"
        return {"error": error_msg}

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
