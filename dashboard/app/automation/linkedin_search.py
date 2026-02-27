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
# Hardcoded geo URN mapping for common countries/regions.
# LinkedIn's typeahead API is unreliable (404s), so we use this as
# primary fallback for country-level searches.
# Source: LinkedIn geo URN IDs (verified against LinkedIn search URLs).
# ---------------------------------------------------------------------------
GEO_URN_MAP: dict[str, str] = {
    # Countries
    "united kingdom": "101165590",
    "uk": "101165590",
    "great britain": "101165590",
    "england": "101165590",
    "united states": "103644278",
    "usa": "103644278",
    "us": "103644278",
    "america": "103644278",
    "india": "102713980",
    "japan": "101355337",
    "germany": "101282230",
    "deutschland": "101282230",
    "france": "105015875",
    "canada": "101174742",
    "australia": "101452733",
    "brazil": "106057199",
    "china": "102890883",
    "italy": "103350119",
    "spain": "105646813",
    "mexico": "103323778",
    "netherlands": "102890719",
    "holland": "102890719",
    "switzerland": "106693272",
    "sweden": "105117694",
    "south korea": "105149562",
    "korea": "105149562",
    "singapore": "102454443",
    "ireland": "104738515",
    "belgium": "100565514",
    "austria": "103883259",
    "norway": "103819153",
    "denmark": "104514075",
    "finland": "100456013",
    "poland": "105072130",
    "portugal": "100364837",
    "russia": "101728296",
    "turkey": "102105699",
    "israel": "101620260",
    "south africa": "104035573",
    "uae": "104305776",
    "united arab emirates": "104305776",
    "dubai": "104305776",
    "saudi arabia": "100459316",
    "new zealand": "105490917",
    "indonesia": "102478259",
    "malaysia": "106808692",
    "philippines": "103121230",
    "thailand": "105146118",
    "vietnam": "104195383",
    "pakistan": "101022442",
    "bangladesh": "106871199",
    "nigeria": "105365761",
    "egypt": "106155005",
    "argentina": "100446943",
    "colombia": "100876405",
    "chile": "104621616",
    "peru": "102927786",
    "czech republic": "104508036",
    "czechia": "104508036",
    "romania": "106670623",
    "hungary": "100288700",
    "ukraine": "102264497",
    "greece": "104677530",
    "taiwan": "104187078",
    "hong kong": "103291313",
    "scotland": "100501126",
    "wales": "104325837",
    # Major cities
    "london": "90009496",
    "new york": "105080838",
    "san francisco": "102277331",
    "los angeles": "102448103",
    "chicago": "103112676",
    "toronto": "100025096",
    "sydney": "101028004",
    "melbourne": "100260623",
    "mumbai": "106164952",
    "bangalore": "105214831",
    "bengaluru": "105214831",
    "delhi": "116753883",
    "new delhi": "116753883",
    "hyderabad": "105556991",
    "chennai": "106340041",
    "pune": "114806696",
    "dubai city": "104305776",
    "berlin": "103035651",
    "paris": "105528734",
    "tokyo": "101838753",
    "shanghai": "103873152",
    "beijing": "103873993",
    "sao paulo": "106478078",
    "amsterdam": "102011674",
    "dublin": "105178154",
    "zurich": "101318387",
    "munich": "100851997",
    "barcelona": "101985149",
    "madrid": "105383571",
    "rome": "101231427",
    "milan": "103028454",
    "stockholm": "106686728",
    "oslo": "100645073",
    "copenhagen": "104681028",
    "helsinki": "116496683",
    "warsaw": "101851067",
    "lisbon": "104898705",
    "brussels": "101797929",
    "vienna": "100850888",
    "vancouver": "103366113",
    "montreal": "104649306",
    "seattle": "104116203",
    "boston": "102380872",
    "austin": "104472866",
    "dallas": "103020188",
    "houston": "103743442",
    "atlanta": "106057766",
    "denver": "100882797",
    "miami": "101300203",
    "washington dc": "104383534",
    "manchester": "100694774",
    "birmingham": "100694231",
    "edinburgh": "100540382",
    "leeds": "103814662",
    "glasgow": "104292185",
    "bristol": "103039432",
    "liverpool": "107421250",
    "cambridge": "101861859",
    "oxford": "106104061",
}


def _lookup_geo_urn_local(location_text: str) -> str | None:
    """Look up geo URN from hardcoded mapping. Returns full URN or None."""
    if not location_text:
        return None
    key = location_text.strip().lower()
    geo_id = GEO_URN_MAP.get(key)
    if geo_id:
        return f"urn:li:geo:{geo_id}"
    # Try partial match for common patterns like "London, UK" → "london"
    for known_key, gid in GEO_URN_MAP.items():
        if key.startswith(known_key) or known_key.startswith(key):
            return f"urn:li:geo:{gid}"
    return None

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

    // Helper: extract geo URN from any plausible field
    function findGeoUrn(obj) {
        if (!obj || typeof obj !== 'object') return '';
        const candidates = [
            obj.targetUrn, obj.entityUrn, obj.objectUrn, obj.trackingUrn,
            obj.hitInfo?.id, obj.hitInfo?.entityUrn, obj.hitInfo?.targetUrn,
        ];
        for (const c of candidates) {
            if (typeof c === 'string' && c.includes('geo:')) return c;
        }
        return '';
    }

    // Try multiple typeahead endpoints (LinkedIn changes these periodically)
    const urls = [
        'https://www.linkedin.com/voyager/api/typeahead/hitsV2?' +
            new URLSearchParams({ keywords: locationText, origin: 'GLOBAL_SEARCH_HEADER', q: 'type', type: 'GEO', count: '10' }).toString(),
        'https://www.linkedin.com/voyager/api/graphql?variables=(query:' + encodeURIComponent(locationText) +
            ',type:GEO,count:10)&queryId=voyagerSearchDashTypeaheadByGlobalTypeahead.bcc3b0a84a2a75b7e5c67c0808245e61',
    ];

    const debugInfo = [];

    for (const url of urls) {
        try {
            const resp = await fetch(url, {
                headers: {
                    'csrf-token': csrfToken,
                    'x-restli-protocol-version': '2.0.0',
                },
                credentials: 'include',
            });
            if (!resp.ok) {
                debugInfo.push({ url: url.substring(0, 80), status: resp.status });
                continue;
            }
            const data = await resp.json();
            const topKeys = Object.keys(data || {});

            // 1. Check included array (normalized format)
            const included = data?.included || [];
            for (const el of included) {
                const urn = findGeoUrn(el);
                if (urn) return { geoUrn: urn };
            }

            // 2. Check top-level elements
            const elements = data?.elements || [];
            for (const el of elements) {
                const urn = findGeoUrn(el);
                if (urn) return { geoUrn: urn };
            }

            // 3. Check data.elements (GraphQL wrapper)
            const dataElements = data?.data?.elements || [];
            for (const el of dataElements) {
                const urn = findGeoUrn(el);
                if (urn) return { geoUrn: urn };
            }

            // 4. Check nested GraphQL typeahead response
            const typeahead = data?.data?.searchDashTypeaheadByGlobalTypeahead || data?.data?.typeaheadByGlobalTypeahead || {};
            const taElements = typeahead?.elements || [];
            for (const el of taElements) {
                const urn = findGeoUrn(el);
                if (urn) return { geoUrn: urn };
            }

            // 5. Deep scan: recursively search for any geo URN in the response
            function deepFindGeo(obj, depth) {
                if (depth > 4 || !obj) return '';
                if (typeof obj === 'string') return obj.includes('urn:li:geo:') ? obj : '';
                if (Array.isArray(obj)) {
                    for (const item of obj) {
                        const r = deepFindGeo(item, depth + 1);
                        if (r) return r;
                    }
                } else if (typeof obj === 'object') {
                    for (const val of Object.values(obj)) {
                        const r = deepFindGeo(val, depth + 1);
                        if (r) return r;
                    }
                }
                return '';
            }
            const deepUrn = deepFindGeo(data, 0);
            if (deepUrn) return { geoUrn: deepUrn };

            // Collect debug info for this endpoint
            const sampleEl = elements[0] || dataElements[0] || taElements[0] || included[0];
            debugInfo.push({
                url: url.substring(0, 80),
                topKeys,
                elementsCount: elements.length,
                dataElementsCount: dataElements.length,
                taElementsCount: taElements.length,
                includedCount: included.length,
                sampleKeys: sampleEl ? Object.keys(sampleEl) : [],
            });
        } catch (e) {
            debugInfo.push({ url: url.substring(0, 80), error: e.message });
            continue;
        }
    }

    return { error: 'No geo URN found for: ' + locationText, debug: debugInfo };
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

        if (!resp.ok) {
            let body = '';
            try { body = await resp.text(); } catch(e) {}
            return { error: 'API returned ' + resp.status + ' ' + resp.statusText, debugUrl: url.substring(0, 300), debugBody: body.substring(0, 500) };
        }
        const data = await resp.json();
        return data;
    } catch (e) {
        return { error: e.message, debugUrl: url.substring(0, 300) };
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


def _get_type(item: dict) -> str:
    """Get the type string from an item, checking both _type and $type keys."""
    return item.get("_type", "") or item.get("$type", "")


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
    if not isinstance(data_section, dict):
        data_section = {}

    # Try multiple possible keys for the clusters data
    clusters = (
        data_section.get("searchDashClustersByAll")
        or data_section.get("searchClustersByAll")
        or data_section.get("searchDashTypeaheadByGlobalTypeahead")
        or {}
    )

    if not clusters:
        # Log actual keys for debugging
        data_keys = list(data_section.keys())[:15] if data_section else []
        logger.warning(
            "No searchDashClustersByAll in response. data keys: %s, top keys: %s",
            data_keys,
            list(raw_data.keys())[:10],
        )
        # Fallback: try the 'included' format (normalized response)
        return _parse_included_format(raw_data)

    cluster_type = _get_type(clusters)
    if cluster_type and "CollectionResponse" not in cluster_type:
        logger.warning("Unexpected cluster type: %s", cluster_type)

    for cluster_element in clusters.get("elements", []):
        c_type = _get_type(cluster_element)
        if c_type and "SearchClusterViewModel" not in c_type:
            continue

        for search_item in cluster_element.get("items", []):
            s_type = _get_type(search_item)
            if s_type and "SearchItem" not in s_type:
                continue

            entity = search_item.get("item", {}).get("entityResult", {})
            if not entity or not isinstance(entity, dict):
                continue

            e_type = _get_type(entity)
            if e_type and "EntityResultViewModel" not in e_type:
                continue

            profile = _extract_from_entity_result(entity)
            if profile and profile.get("profile_url"):
                if not any(l["profile_url"] == profile["profile_url"] for l in leads):
                    leads.append(profile)

    if not leads:
        logger.info(
            "Denormalized parse found 0 leads, trying included fallback (included count: %d)",
            len(raw_data.get("included", [])),
        )
        return _parse_included_format(raw_data)

    return leads


def _parse_included_format(raw_data: dict) -> list[dict]:
    """Fallback parser for the normalized 'included' array response format.

    In normalized responses, entities are flattened into the 'included' array
    with URN references. We scan for EntityResultViewModel and MiniProfile types.
    """
    leads = []
    included = raw_data.get("included", [])

    if not included:
        return leads

    # Log types found in included for debugging
    type_counts: dict[str, int] = {}
    for item in included:
        recipe = _get_type(item)
        if recipe:
            short = recipe.rsplit(".", 1)[-1] if "." in recipe else recipe
            type_counts[short] = type_counts.get(short, 0) + 1

    if type_counts:
        logger.info("Included array types: %s", type_counts)

    for item in included:
        recipe = _get_type(item)
        profile = None

        if "EntityResultViewModel" in recipe or "EntityResult" in recipe:
            profile = _extract_from_entity_result(item)
        elif "MiniProfile" in recipe:
            profile = _extract_from_mini_profile(item)

        if profile and profile.get("profile_url"):
            if not any(l["profile_url"] == profile["profile_url"] for l in leads):
                leads.append(profile)

    logger.info("Included format parser found %d leads from %d items", len(leads), len(included))
    return leads


def _safe_text(value) -> str:
    """Extract text from a field that may be a dict with 'text' key or a string."""
    if isinstance(value, dict):
        return value.get("text", "") or ""
    if isinstance(value, str):
        return value
    return ""


def _extract_from_entity_result(item: dict, entities: dict | None = None) -> Optional[dict]:
    """Extract lead from EntityResultViewModel."""
    full_name = _safe_text(item.get("title", ""))
    headline = _safe_text(item.get("primarySubtitle", ""))
    location = _safe_text(item.get("secondarySubtitle", ""))

    # Get profile URL from navigationUrl
    nav_url = item.get("navigationUrl", "")
    profile_url = ""
    if isinstance(nav_url, str) and "/in/" in nav_url:
        profile_url = nav_url.split("?")[0]
        if not profile_url.startswith("https://"):
            profile_url = "https://www.linkedin.com" + profile_url

    # Fallback: try entityUrn to build URL
    if not profile_url:
        urn = item.get("entityUrn", "")
        if isinstance(urn, str) and "fsd_profile" in urn:
            # urn:li:fsd_profile:ACoAAXXXXXX — use member URN-based URL
            member_id = urn.rsplit(":", 1)[-1] if ":" in urn else ""
            if member_id:
                profile_url = f"https://www.linkedin.com/in/{member_id}"

    # Another fallback: try tracking info for publicIdentifier
    tracking = item.get("entityCustomTrackingInfo", {})
    if not profile_url and isinstance(tracking, dict):
        member_distance = tracking.get("memberDistance", "")
        # Some results include a publicIdentifier in the navigationUrl
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
    """Resolve a location name to a LinkedIn geoUrn.

    Strategy: 1) hardcoded mapping (instant), 2) LinkedIn typeahead API.
    """
    if not location_text or not location_text.strip():
        return None

    # 1. Try hardcoded mapping first (instant, no API call)
    local_urn = _lookup_geo_urn_local(location_text)
    if local_urn:
        logger.info("Resolved '%s' -> %s (from local mapping)", location_text, local_urn)
        return local_urn

    # 2. Try LinkedIn typeahead API
    try:
        result = await page.evaluate(GEO_LOOKUP_JS, location_text.strip())
        if result and "geoUrn" in result:
            logger.info("Resolved '%s' -> %s (from API)", location_text, result["geoUrn"])
            return result["geoUrn"]
        else:
            error = result.get("error", "Unknown") if result else "No response"
            debug = result.get("debug", []) if result else []
            logger.warning("Geo lookup failed for '%s': %s", location_text, error)
            if debug:
                logger.warning("Geo lookup debug info: %s", debug)
            return None
    except Exception as exc:
        logger.warning("Geo lookup exception for '%s': %s", location_text, exc)
        return None


async def search_people(
    page: Page,
    keywords: str,
    max_results: int = 100,
    location: str = "",
    geo_urn: str = "",
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
    location : Location text (fallback if geo_urn not provided)
    geo_urn : Pre-resolved LinkedIn geoUrn (from autocomplete)
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

    # Use pre-resolved geoUrn from autocomplete, or resolve from text
    if not geo_urn and location:
        geo_urn_resolved = await _resolve_geo_urn(page, location)
        if geo_urn_resolved:
            geo_urn = geo_urn_resolved
        else:
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
            debug_url = raw.get("debugUrl", "")
            debug_body = raw.get("debugBody", "")
            logger.error("Search failed at start=%d: %s", start, error_msg)
            if debug_url:
                logger.error("Request URL: %s", debug_url)
            if debug_body:
                logger.error("Response body: %s", debug_body)
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
