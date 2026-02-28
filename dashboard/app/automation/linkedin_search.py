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

# queryId from linkedin-api v2.3.1 — LinkedIn rotates these hashes on redeploy.
# SEARCH_JS now tries multiple hashes + dynamic extraction, so this constant
# is kept only for reference.
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
# JavaScript to extract the current queryId for search from LinkedIn's
# JavaScript bundles.  LinkedIn rotates these hashes on redeploy, so
# hardcoded values go stale.  We sniff the live page's JS modules.
# ---------------------------------------------------------------------------
EXTRACT_QUERY_ID_JS = """
async () => {
    // Strategy 1: Intercept from LinkedIn's module system (Ember-style define/require)
    // LinkedIn registers modules with paths containing the queryId hashes.
    try {
        if (window.require && window.require.entries) {
            const entries = Object.keys(window.require.entries);
            for (const key of entries) {
                if (key.includes('voyagerSearchDashClusters')) {
                    // The key itself often IS the queryId
                    const match = key.match(/voyagerSearchDashClusters\\.([a-f0-9]{32})/);
                    if (match) return match[0]; // e.g. voyagerSearchDashClusters.abc123...
                }
            }
        }
    } catch(e) {}

    // Strategy 2: Scan all <script> tags for the hash pattern
    try {
        const scripts = document.querySelectorAll('script[src]');
        for (const script of scripts) {
            try {
                const resp = await fetch(script.src, { credentials: 'include' });
                if (!resp.ok) continue;
                const text = await resp.text();
                const match = text.match(/voyagerSearchDashClusters\\.([a-f0-9]{32})/);
                if (match) return match[0];
            } catch(e) { continue; }
        }
    } catch(e) {}

    // Strategy 3: Perform a real search via LinkedIn's UI search bar URL
    // and intercept the queryId from the network request
    try {
        const csrfToken = document.cookie
            .split('; ')
            .find(c => c.startsWith('JSESSIONID='))
            ?.split('=')[1]
            ?.replace(/"/g, '') || '';
        if (csrfToken) {
            // Try the REST endpoint (non-GraphQL) which sometimes still works
            const testUrl = 'https://www.linkedin.com/voyager/api/search/dash/clusters?' +
                'decorationId=com.linkedin.voyager.dash.deco.search.SearchClusterCollection-175' +
                '&origin=GLOBAL_SEARCH_HEADER&q=all' +
                '&query=(flagshipSearchIntent:SEARCH_SRP,' +
                'queryParameters:List((key:resultType,value:List(PEOPLE))))' +
                '&start=0&count=1';
            const resp = await fetch(testUrl, {
                headers: {
                    'csrf-token': csrfToken,
                    'accept': 'application/vnd.linkedin.normalized+json+2.1',
                    'x-restli-protocol-version': '2.0.0',
                },
                credentials: 'include',
            });
            if (resp.ok) return '__REST_ENDPOINT__';
        }
    } catch(e) {}

    return null;
}
"""

# ---------------------------------------------------------------------------
# JavaScript to perform Voyager GraphQL people search with filters.
# Self-healing: tries multiple queryId hashes + REST endpoint fallback.
# LinkedIn rotates queryId hashes on redeploy, so we try several known
# hashes and also support dynamic extraction.
# ---------------------------------------------------------------------------
SEARCH_JS = """
async ({ keywords, start, count, filtersList, dynamicQueryId, capturedTemplate, capturedHeaders }) => {
    const csrfToken = document.cookie
        .split('; ')
        .find(c => c.startsWith('JSESSIONID='))
        ?.split('=')[1]
        ?.replace(/"/g, '') || '';

    if (!csrfToken) return { error: 'No CSRF token — not logged in? Please open LinkedIn and login first.' };

    // Helper: fetch with a 15-second timeout
    async function fetchWithTimeout(url, options, timeoutMs = 15000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const resp = await fetch(url, { ...options, signal: controller.signal });
            clearTimeout(timer);
            return resp;
        } catch (e) {
            clearTimeout(timer);
            throw e;
        }
    }

    // Clean keywords
    const cleanKeywords = keywords.replace(/\\s+/g, ' ').trim();
    const filtersStr = filtersList || '(key:resultType,value:List(PEOPLE))';

    // Build headers — use captured headers from live request if available,
    // otherwise use minimal required headers
    const headers = {};
    if (capturedHeaders && typeof capturedHeaders === 'object') {
        // Copy relevant headers from captured live request
        const keepHeaders = ['csrf-token', 'accept', 'x-restli-protocol-version',
            'x-li-lang', 'x-li-page-instance', 'x-li-track', 'x-li-deco-include'];
        for (const h of keepHeaders) {
            if (capturedHeaders[h]) headers[h] = capturedHeaders[h];
        }
    }
    // Ensure minimum required headers
    if (!headers['csrf-token']) headers['csrf-token'] = csrfToken;
    if (!headers['accept']) headers['accept'] = 'application/vnd.linkedin.normalized+json+2.1';
    if (!headers['x-restli-protocol-version']) headers['x-restli-protocol-version'] = '2.0.0';

    let lastError = '';
    let attempts = 0;

    // ---------------------------------------------------------------
    // Strategy 1: If we have a captured template from LinkedIn's own
    // request, modify it with our keywords/filters and replay
    // ---------------------------------------------------------------
    if (capturedTemplate && dynamicQueryId) {
        try {
            // Replace keywords in the captured template
            let modifiedVars = capturedTemplate;
            // Replace keywords:XXX with our keywords
            modifiedVars = modifiedVars.replace(
                /keywords:[^,)]+/,
                'keywords:' + cleanKeywords
            );
            // Replace start:N with our start
            modifiedVars = modifiedVars.replace(
                /start:\\d+/,
                'start:' + start
            );
            // Replace count:N if present
            if (modifiedVars.includes('count:')) {
                modifiedVars = modifiedVars.replace(
                    /count:\\d+/,
                    'count:' + count
                );
            }
            // Replace queryParameters if we have filters
            if (filtersStr && filtersStr !== '(key:resultType,value:List(PEOPLE))') {
                modifiedVars = modifiedVars.replace(
                    /queryParameters:List\\([^)]*\\)/,
                    'queryParameters:List(' + filtersStr + ')'
                );
            }

            const url = 'https://www.linkedin.com/voyager/api/graphql?variables=' +
                modifiedVars + '&queryId=' + dynamicQueryId;
            attempts++;
            const resp = await fetchWithTimeout(url, { headers, credentials: 'include' });
            if (resp.ok) {
                const data = await resp.json();
                data._usedQueryId = dynamicQueryId;
                data._usedFormat = 'captured-template';
                return data;
            }
            lastError = 'captured-template → ' + resp.status;
        } catch(e) {
            lastError = 'captured-template → ' + (e.name === 'AbortError' ? 'timeout' : e.message);
        }
    }

    // ---------------------------------------------------------------
    // Strategy 2: Construct URL manually with multiple variable formats.
    // LinkedIn ROST variables use raw chars: (, ), :, , in URL.
    // We do NOT encodeURIComponent the variables string.
    // ---------------------------------------------------------------
    const variableFormats = [];

    // Format 1: count inside outer parens at end (2025-2026 observed format)
    variableFormats.push(
        '(start:' + start + ',origin:GLOBAL_SEARCH_HEADER,' +
        'query:(' +
        (cleanKeywords ? 'keywords:' + cleanKeywords + ',' : '') +
        'flagshipSearchIntent:SEARCH_SRP,' +
        'queryParameters:List(' + filtersStr + '),' +
        'includeFiltersInResponse:false),' +
        'count:' + count + ')'
    );

    // Format 2: count at beginning, top level
    variableFormats.push(
        '(count:' + count + ',start:' + start + ',origin:GLOBAL_SEARCH_HEADER,' +
        'query:(' +
        (cleanKeywords ? 'keywords:' + cleanKeywords + ',' : '') +
        'flagshipSearchIntent:SEARCH_SRP,' +
        'queryParameters:List(' + filtersStr + '),' +
        'includeFiltersInResponse:false))'
    );

    // Format 3: no count (legacy)
    variableFormats.push(
        '(start:' + start + ',origin:GLOBAL_SEARCH_HEADER,' +
        'query:(' +
        (cleanKeywords ? 'keywords:' + cleanKeywords + ',' : '') +
        'flagshipSearchIntent:SEARCH_SRP,' +
        'queryParameters:List(' + filtersStr + '),' +
        'includeFiltersInResponse:false))'
    );

    // queryId hashes to try
    const queryIds = [];
    if (dynamicQueryId) queryIds.push(dynamicQueryId);
    queryIds.push(
        'voyagerSearchDashClusters.b0928897b71bd00a5a7291755dcd64f0',
        'voyagerSearchDashClusters.994bf4e7d2173b92ccdb5935710c3c5d',
        'voyagerSearchDashClusters.52fec77d08aa4598c8a056ca6bce6c11',
        'voyagerSearchDashClusters.1f5ea36a42fc3319f534af1022b6dd64',
    );

    for (const qid of queryIds) {
        for (const variables of variableFormats) {
            attempts++;
            // RAW variables in URL (no encodeURIComponent — LinkedIn uses raw ROST format)
            const url = 'https://www.linkedin.com/voyager/api/graphql?variables=' +
                variables + '&queryId=' + qid;
            try {
                const resp = await fetchWithTimeout(url, { headers, credentials: 'include' });
                if (resp.ok) {
                    const data = await resp.json();
                    data._usedQueryId = qid;
                    data._usedFormat = attempts;
                    return data;
                }
                lastError = 'qid ' + qid.split('.')[1].substring(0,8) + ' fmt' + (variableFormats.indexOf(variables)+1) + ' → ' + resp.status;
            } catch (e) {
                lastError = e.name === 'AbortError' ? 'timeout' : e.message;
            }
        }
    }

    // ---------------------------------------------------------------
    // Strategy 3: REST endpoint fallback
    // ---------------------------------------------------------------
    const restQueries = [
        '(flagshipSearchIntent:SEARCH_SRP,' +
            (cleanKeywords ? 'keywords:' + cleanKeywords + ',' : '') +
            'queryParameters:List(' + filtersStr + '))',
    ];

    for (const restQuery of restQueries) {
        const decoIds = [
            'com.linkedin.voyager.dash.deco.search.SearchClusterCollection-175',
            'com.linkedin.voyager.dash.deco.search.SearchClusterCollection-174',
            'com.linkedin.voyager.dash.deco.search.SearchClusterCollection-176',
        ];
        for (const decoId of decoIds) {
            attempts++;
            try {
                const restUrl = 'https://www.linkedin.com/voyager/api/search/dash/clusters?' +
                    'decorationId=' + decoId +
                    '&origin=GLOBAL_SEARCH_HEADER&q=all' +
                    '&query=' + restQuery +
                    '&start=' + start + '&count=' + count;
                const resp = await fetchWithTimeout(restUrl, { headers, credentials: 'include' });
                if (resp.ok) {
                    const data = await resp.json();
                    data._usedEndpoint = 'REST';
                    return data;
                }
                lastError = 'REST decoId ' + decoId.slice(-3) + ' → ' + resp.status;
            } catch(e) {
                lastError += '; REST: ' + (e.name === 'AbortError' ? 'timeout' : e.message);
            }
        }
    }

    return {
        error: 'All search endpoints failed (' + attempts + ' attempts). Last: ' + lastError,
        debugInfo: 'Tried captured-template + ' + queryIds.length + ' hashes x ' + variableFormats.length + ' formats + REST'
    };
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


# ---------------------------------------------------------------------------
# Module-level cache for captured queryId, URL template, and headers.
# Reset on import (server restart).
# ---------------------------------------------------------------------------
_cached_query_id: str | None = None
_cached_variables_template: str | None = None  # Full variables string from live request
_cached_request_headers: dict | None = None  # Headers from the live request


async def capture_query_id_via_navigation(page: Page) -> str | None:
    """Capture a WORKING search request from LinkedIn's own JS.

    The key insight: instead of guessing the API format, we trigger a real
    LinkedIn search and intercept the EXACT request that LinkedIn makes.
    We then reuse that queryId + URL format for our own searches.

    Strategies (in order):
    1. Return cached queryId (if we have one from a previous call).
    2. JS extraction from current page (no navigation).
    3. Network interception: trigger a real search, capture full request.

    Returns the full queryId string or None.
    """
    global _cached_query_id, _cached_variables_template, _cached_request_headers
    if _cached_query_id:
        logger.info("Using cached queryId: %s", _cached_query_id)
        print(f"  [Search] Using cached queryId: {_cached_query_id[:40]}...")
        return _cached_query_id

    # Strategy 1: JS extraction on current page — NO navigation
    try:
        qid = await page.evaluate(EXTRACT_QUERY_ID_JS)
        if qid:
            _cached_query_id = qid
            logger.info("Extracted queryId from JS: %s", qid)
            print(f"  [Search] Extracted fresh queryId from JS: {qid[:40]}...")
            return qid
    except Exception as exc:
        logger.warning("JS queryId extraction failed: %s", exc)

    # Strategy 2: Network interception — capture FULL request (URL + headers)
    try:
        captured = {"qid": None, "full_url": None, "headers": None}

        async def _intercept(route):
            url = route.request.url
            if "voyagerSearchDashClusters" in url or "searchDash" in url:
                import re
                match = re.search(r'queryId=(voyagerSearchDashClusters\.[a-f0-9]+)', url)
                if match:
                    captured["qid"] = match.group(1)
                    captured["full_url"] = url
                    captured["headers"] = dict(route.request.headers)
            await route.continue_()

        await page.route("**/voyager/api/graphql*", _intercept)

        # Type a quick search to trigger LinkedIn's GraphQL call
        search_input = await page.query_selector('input[aria-label="Search"]')
        if not search_input:
            search_input = await page.query_selector('input[placeholder*="Search"]')
        if search_input:
            await search_input.click()
            await search_input.fill("test")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(4000)
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1000)

        await page.unroute("**/voyager/api/graphql*")

        if captured["qid"]:
            _cached_query_id = captured["qid"]
            if captured["full_url"]:
                import re
                from urllib.parse import unquote
                vmatch = re.search(r'variables=([^&]+)', captured["full_url"])
                if vmatch:
                    _cached_variables_template = unquote(vmatch.group(1))
                    logger.info("Captured live variables: %s", _cached_variables_template[:120])
                    print(f"  [Search] Captured live variables format from LinkedIn")
            if captured["headers"]:
                _cached_request_headers = captured["headers"]
                logger.info("Captured %d request headers from live search", len(captured["headers"]))
            logger.info("Captured live queryId via network intercept: %s", captured["qid"])
            print(f"  [Search] Captured live queryId: {captured['qid'][:40]}...")
            return captured["qid"]
    except Exception as exc:
        logger.warning("Network interception for queryId failed: %s", exc)
        try:
            await page.unroute("**/voyager/api/graphql*")
        except Exception:
            pass

    logger.info("All queryId extraction strategies failed, will use hardcoded hashes")
    print("  [Search] Could not extract queryId, using known hashes (this is OK)")
    return None


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

    print(f"\n  [Search] Starting search: keywords='{keywords}' max_results={max_results}")

    # Make sure we're on LinkedIn first
    current_url = page.url
    if "linkedin.com" not in current_url:
        try:
            print("  [Search] Navigating to LinkedIn...")
            await page.goto("https://www.linkedin.com/feed/", timeout=15000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"  [Search] ERROR: Could not navigate to LinkedIn: {exc}")
            return [], f"Could not navigate to LinkedIn: {exc}"

    # Use pre-resolved geoUrn from autocomplete, or resolve from text
    if not geo_urn and location:
        print(f"  [Search] Resolving location: '{location}'...")
        geo_urn_resolved = await _resolve_geo_urn(page, location)
        if geo_urn_resolved:
            geo_urn = geo_urn_resolved
        else:
            # Fallback: append location to keywords
            keywords = f"{keywords} {location}"
            print(f"  [Search] Geo lookup failed, appended to keywords: '{keywords}'")

    # Build filters string in LinkedIn format
    filters_list = _build_filters_list(
        geo_urn=geo_urn,
        network=network,
        company_size=company_size,
    )

    # Try to extract queryId from LinkedIn JS (no navigation, instant)
    dynamic_query_id = None
    try:
        dynamic_query_id = await capture_query_id_via_navigation(page)
    except Exception as exc:
        logger.warning("queryId extraction failed: %s", exc)
    if not dynamic_query_id:
        print("  [Search] Using hardcoded queryId hashes (this is normal)")

    # ---------------------------------------------------------------
    # Primary: try Voyager API approach (fast, up to 49 results/page)
    # ---------------------------------------------------------------
    api_failed = False

    while start < max_results:
        count = min(batch_size, max_results - start)
        batch_num = (start // batch_size) + 1

        print(f"  [Search] Fetching batch {batch_num} (results {start+1}-{start+count})...")
        logger.info(
            "Searching LinkedIn: keywords='%s' start=%d count=%d filters=%s",
            keywords, start, count, filters_list,
        )

        try:
            raw = await page.evaluate(
                SEARCH_JS,
                {
                    "keywords": keywords,
                    "start": start,
                    "count": count,
                    "filtersList": filters_list,
                    "dynamicQueryId": dynamic_query_id or "",
                    "capturedTemplate": _cached_variables_template or "",
                    "capturedHeaders": _cached_request_headers or {},
                },
            )
        except Exception as exc:
            error_msg = f"Browser error: {exc}"
            print(f"  [Search] ERROR in batch {batch_num}: {exc}")
            logger.error("page.evaluate failed: %s", exc)
            api_failed = True
            break

        if not raw:
            error_msg = "No response from LinkedIn API"
            print(f"  [Search] ERROR: No response from LinkedIn API")
            logger.error(error_msg)
            api_failed = True
            break

        if "error" in raw:
            error_msg = raw["error"]
            debug_info = raw.get("debugInfo", "")
            print(f"  [Search] API failed: {error_msg}")
            logger.error("Search failed at start=%d: %s", start, error_msg)
            if debug_info:
                print(f"  [Search] Debug: {debug_info}")
                logger.error("Debug: %s", debug_info)
            api_failed = True
            break

        # Log which endpoint/hash worked
        used_qid = raw.get("_usedQueryId", "")
        used_endpoint = raw.get("_usedEndpoint", "")
        if used_qid:
            logger.info("Search succeeded with queryId: %s", used_qid)
        elif used_endpoint:
            logger.info("Search succeeded with %s endpoint", used_endpoint)

        leads = _parse_search_results(raw)
        if not leads:
            print(f"  [Search] No more results after batch {batch_num}")
            logger.info("No more results at start=%d", start)
            break

        all_leads.extend(leads)
        print(f"  [Search] Batch {batch_num}: got {len(leads)} leads (total: {len(all_leads)})")
        logger.info("Got %d leads (total so far: %d)", len(leads), len(all_leads))

        start += batch_size
        if start < max_results:
            await page.wait_for_timeout(1500)

    # ---------------------------------------------------------------
    # Fallback: if API failed, scrape results from LinkedIn search page
    # This ALWAYS works because we're just loading the search URL and
    # reading the DOM — same as what a human sees.
    # ---------------------------------------------------------------
    if api_failed and not all_leads:
        print("  [Search] API failed — falling back to DOM scraping...")
        logger.info("API search failed, trying DOM scraping fallback")
        dom_leads, dom_error = await _search_via_dom_scraping(
            page, keywords, geo_urn, network, company_size, max_results
        )
        if dom_leads:
            all_leads.extend(dom_leads)
            error_msg = None  # Clear the API error since DOM worked
            print(f"  [Search] DOM scraping got {len(dom_leads)} leads!")
        elif dom_error:
            error_msg = f"API failed + DOM scraping also failed: {dom_error}"
            print(f"  [Search] DOM scraping also failed: {dom_error}")

    # Deduplicate by profile_url
    seen = set()
    unique = []
    for lead in all_leads:
        if lead["profile_url"] not in seen:
            seen.add(lead["profile_url"])
            unique.append(lead)

    print(f"  [Search] DONE: {len(unique)} unique leads for '{keywords}'\n")
    logger.info("Search complete: %d unique leads for '%s'", len(unique), keywords)
    return unique, error_msg


# ---------------------------------------------------------------------------
# DOM Scraping search — ALWAYS works because it uses LinkedIn's own search
# page (same URL format as browser address bar).
# ---------------------------------------------------------------------------

# JS to extract search results from the rendered LinkedIn search page DOM.
# Uses data-view-name attributes (LinkedIn's analytics markers) as primary
# selectors — these are stable even when CSS class names are obfuscated.
_DOM_SCRAPE_JS = """
() => {
    const results = [];
    const seenUrls = new Set();

    // ---- Find search result name links ----
    // PRIMARY: data-view-name attribute (most reliable — LinkedIn analytics marker)
    let nameLinks = Array.from(
        document.querySelectorAll('a[data-view-name="search-result-lockup-title"]')
    );
    // FALLBACK: all /in/ profile links
    if (nameLinks.length === 0) {
        nameLinks = Array.from(document.querySelectorAll('a[href*="/in/"]'));
    }

    for (const link of nameLinks) {
        try {
            const href = link.getAttribute('href') || '';
            const m = href.match(/\\/in\\/([^/?#]+)/);
            if (!m) continue;
            const slug = m[1];
            if (slug.length < 2) continue;
            const profileUrl = 'https://www.linkedin.com/in/' + slug;
            if (seenUrls.has(profileUrl)) continue;

            // ---- Find the card container ----
            // Walk up to the nearest <li> (search results are in a list)
            let card = link.closest('li') || link.closest('[data-chameleon-result-urn]');
            if (!card) {
                let el = link.parentElement;
                for (let i = 0; i < 8 && el; i++) {
                    if (el.textContent && el.textContent.trim().length > 30 &&
                        el.textContent.trim().length < 2000 &&
                        el.tagName !== 'MAIN' && el.tagName !== 'BODY' &&
                        el.tagName !== 'SECTION') {
                        card = el;
                        break;
                    }
                    el = el.parentElement;
                }
            }
            if (!card) card = link.parentElement;

            // ---- Extract name ----
            let fullName = '';

            // Strategy A: span[aria-hidden="true"] inside link (old layout)
            const ariaSpans = link.querySelectorAll('span[aria-hidden="true"]');
            for (const sp of ariaSpans) {
                const t = sp.textContent.trim();
                if (t && t.length >= 2 && t.length <= 60 &&
                    /[a-zA-Z\\u00C0-\\u024F\\u0400-\\u04FF]/.test(t) &&
                    !/^(1st|2nd|3rd|View|Follow|Connect)/i.test(t)) {
                    fullName = t;
                    break;
                }
            }

            // Strategy B: direct link text (new obfuscated layout)
            if (!fullName) {
                fullName = link.textContent.trim();
                fullName = fullName.replace(/\\b(1st|2nd|3rd|3rd\\+)\\b/gi, '').trim();
                fullName = fullName.split('\\n')[0].trim();
            }

            if (!fullName || fullName.length < 2 || fullName === 'LinkedIn Member') continue;

            // ---- Extract headline & location ----
            let headline = '';
            let location = '';

            if (card) {
                // STRATEGY 1: data-view-name selectors
                // LinkedIn uses data-view-name with "subtitle" for headline/location.
                // e.g. "search-result-lockup-subtitle", "search-result-lockup-secondary-subtitle"
                const subtitleEls = card.querySelectorAll('[data-view-name*="subtitle"]');
                if (subtitleEls.length >= 1) headline = subtitleEls[0].textContent.trim();
                if (subtitleEls.length >= 2) location = subtitleEls[1].textContent.trim();

                // STRATEGY 2: CSS class selectors (legacy non-obfuscated layouts)
                if (!headline && !location) {
                    const pSub = card.querySelector('[class*="entity-result__primary-subtitle"]');
                    const sSub = card.querySelector('[class*="entity-result__secondary-subtitle"]');
                    if (pSub) headline = pSub.textContent.trim();
                    if (sSub) location = sSub.textContent.trim();
                }

                // STRATEGY 3: Structural navigation from link outward.
                // Walk up from <a> tag, at each level collect text from sibling
                // DIV/P elements (skip inline SPANs that hold badges/dots).
                if (!headline && !location) {
                    const subtitleTexts = [];
                    let _el = link;
                    while (_el && _el !== card) {
                        const _par = _el.parentElement;
                        if (!_par) break;
                        for (const sib of _par.children) {
                            if (sib === _el || sib.contains(link)) continue;
                            const tag = sib.tagName;
                            if (tag !== 'DIV' && tag !== 'P' && tag !== 'SECTION') continue;
                            const txt = sib.textContent.trim();
                            if (!txt || txt.length < 3) continue;
                            if (/^(Connect|Follow|Message|Send|Pending|Save|Dismiss)/i.test(txt)) continue;
                            if (sib.querySelector('button:not([aria-hidden]), [role="button"]')) continue;
                            if (sib.querySelector('img, svg') && txt.length < 10) continue;
                            if (/\\d+\\s*(mutual|shared)\\s*(connection|contact)/i.test(txt)) continue;
                            if (/^\\d+(st|nd|rd|th)\\s*degree/i.test(txt)) continue;
                            // Skip degree badges like "• 2nd", "· 3rd", "2nd", "3rd+"
                            if (/^[\\u2022\\u00b7•·\\s]*(1st|2nd|3rd|3rd\\+)\\s*$/i.test(txt)) continue;
                            if (/^[\\u2022\\u00b7•·]\\s*(1st|2nd|3rd)/i.test(txt)) continue;
                            subtitleTexts.push(txt);
                        }
                        _el = _par;
                    }
                    if (subtitleTexts.length >= 1) headline = subtitleTexts[0];
                    if (subtitleTexts.length >= 2) location = subtitleTexts[1];
                }

                // STRATEGY 4: TreeWalker fallback (last resort)
                if (!headline && !location) {
                    const NOISE = new Set([
                        'connect', 'follow', 'message', 'send', 'inmail', 'pending',
                        '1st', '2nd', '3rd', '3rd+', '\\u00b7', '|', '-', '\\u2013',
                        '\\u2014', '...', '\\u2022', 'view profile', 'send inmail',
                    ]);
                    const nameLow = fullName.toLowerCase();
                    const textBlocks = [];
                    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT, null);
                    let _n;
                    while (_n = walker.nextNode()) {
                        const t = _n.textContent.trim();
                        if (!t || t.length < 3) continue;
                        const tL = t.toLowerCase();
                        if (tL === nameLow) continue;
                        if (NOISE.has(tL)) continue;
                        if (/^(1st|2nd|3rd|3rd\\+|\\d+(st|nd|rd|th)\\+?)$/i.test(t)) continue;
                        // Skip degree badges with bullet: "• 2nd", "· 3rd"
                        if (/^[\\u2022\\u00b7•·\\s]*(1st|2nd|3rd|3rd\\+)\\s*$/i.test(t)) continue;
                        if (/^[\\u2022\\u00b7•·]\\s*(1st|2nd|3rd)/i.test(t)) continue;
                        if (/^(Connect|Follow|Message|Send|View|Pending|Save|Dismiss)/i.test(t)) continue;
                        if (tL.includes(nameLow) && tL.length < nameLow.length + 5) continue;
                        if (/\\d+\\s*(mutual|shared)\\s*(connection|contact)/i.test(t)) continue;
                        textBlocks.push(t);
                    }
                    if (textBlocks.length >= 1) headline = textBlocks[0];
                    if (textBlocks.length >= 2) location = textBlocks[1];
                }

                // Clean up snippet prefixes
                if (headline) {
                    headline = headline.replace(/^(Current|Formerly|Previously):\\s*/i, '').trim();
                }
                // Final cleanup: discard headline/location if it's just a degree badge
                const isDegree = (s) => s && /^[\\u2022\\u00b7•·\\s]*(1st|2nd|3rd|3rd\\+)?[\\s·•]*$/i.test(s);
                if (isDegree(headline)) headline = '';
                if (isDegree(location)) location = '';

                // Sanity check: swap if headline looks like location & vice versa
                const looksLikeLoc = (s) => s && s.length < 45 &&
                    (s.includes(',') || /\\b(Area|Region|Metro|Greater|City)\\b/i.test(s)) &&
                    !/\\b(at|@|CEO|Founder|Manager|Director|Engineer|Officer|Lead|Head)\\b/i.test(s);
                const looksLikeHl = (s) => s &&
                    /\\b(at|@|CEO|Founder|Manager|Director|Engineer|Officer|Lead|Head|President|VP|CTO|CFO|COO|Entrepreneur)\\b/i.test(s);
                if (headline && location && looksLikeLoc(headline) && looksLikeHl(location)) {
                    [headline, location] = [location, headline];
                }
            }

            // Debug: capture data-view-name values for the first result
            let _dvn = [];
            if (results.length === 0 && card) {
                card.querySelectorAll('[data-view-name]').forEach(el => {
                    _dvn.push(el.getAttribute('data-view-name') + ' => ' +
                              (el.textContent || '').trim().substring(0, 60));
                });
            }

            seenUrls.add(profileUrl);
            const entry = {
                full_name: fullName,
                first_name: fullName.split(' ')[0] || '',
                headline: headline,
                company: '',
                location: location,
                profile_url: profileUrl,
            };
            if (_dvn.length > 0) entry._debug_dvn = _dvn;
            results.push(entry);
        } catch(e) {
            continue;
        }
    }

    return results;
}
"""

# Diagnostic JS to understand what LinkedIn's DOM looks like when extraction fails
_DOM_DIAGNOSTIC_JS = """
() => {
    const diag = {};
    diag.url = window.location.href;
    diag.title = document.title;

    // Count all /in/ links on the page
    const profileLinks = document.querySelectorAll('a[href*="/in/"]');
    diag.profileLinkCount = profileLinks.length;
    diag.sampleLinks = [];
    for (let i = 0; i < Math.min(5, profileLinks.length); i++) {
        const link = profileLinks[i];
        diag.sampleLinks.push({
            href: (link.getAttribute('href') || '').substring(0, 80),
            text: (link.textContent || '').trim().substring(0, 60),
            parentTag: link.parentElement ? link.parentElement.tagName : '',
            parentClass: link.parentElement ? (link.parentElement.className || '').substring(0, 80) : '',
            grandparentTag: (link.parentElement && link.parentElement.parentElement)
                ? link.parentElement.parentElement.tagName : '',
            grandparentClass: (link.parentElement && link.parentElement.parentElement)
                ? (link.parentElement.parentElement.className || '').substring(0, 80) : '',
        });
    }

    // Check for "No results" indicators
    diag.noResultsFound = !!(
        document.querySelector('[class*="no-results"]') ||
        document.querySelector('[class*="no-result"]') ||
        document.querySelector('[class*="empty-state"]') ||
        (document.body.textContent && document.body.textContent.includes('No results found'))
    );

    // Count list items in main content
    const main = document.querySelector('main') || document.body;
    diag.mainListItems = main.querySelectorAll('li').length;
    diag.mainDivs = main.querySelectorAll('div').length;

    // Capture the main content area structure (first 1000 chars)
    diag.mainInnerHTMLpreview = (main.innerHTML || '').substring(0, 1000);

    // Check for login wall / auth wall
    diag.hasLoginWall = !!(
        document.querySelector('[class*="login"]') ||
        document.querySelector('[class*="auth-wall"]') ||
        document.querySelector('form[action*="login"]')
    );

    // Check for CAPTCHA
    diag.hasCaptcha = !!(
        document.querySelector('[class*="captcha"]') ||
        document.querySelector('iframe[src*="captcha"]') ||
        document.querySelector('#captcha')
    );

    // List all <li> elements that contain /in/ links
    const lisWithLinks = [];
    const allLis = main.querySelectorAll('li');
    for (const li of allLis) {
        const link = li.querySelector('a[href*="/in/"]');
        if (link) {
            lisWithLinks.push({
                liClass: (li.className || '').substring(0, 80),
                linkHref: (link.getAttribute('href') || '').substring(0, 80),
                liTextLen: (li.textContent || '').trim().length,
            });
        }
    }
    diag.lisWithProfileLinks = lisWithLinks.slice(0, 5);
    diag.lisWithProfileLinksCount = lisWithLinks.length;

    return diag;
}
"""


async def _search_via_dom_scraping(
    page: Page,
    keywords: str,
    geo_urn: str = "",
    network: list[str] | None = None,
    company_size: list[str] | None = None,
    max_results: int = 100,
) -> tuple[list[dict], str | None]:
    """Search by navigating to LinkedIn's search page and scraping the DOM.

    Uses the exact same URL format as the browser address bar:
    https://www.linkedin.com/search/results/people/?keywords=...&geoUrn=[...]

    This ALWAYS works because it's the same page a human sees.
    LinkedIn shows 10 results per page.
    """
    from urllib.parse import quote

    all_leads: list[dict] = []
    pages_to_scrape = min(max(max_results // 10, 1), 10)  # 1-10 pages

    for page_num in range(1, pages_to_scrape + 1):
        # Build the search URL exactly like LinkedIn's browser URL bar
        params = [f"keywords={quote(keywords)}"]
        params.append("origin=FACETED_SEARCH")

        # geoUrn format: ["104305776"] — JSON array with quoted string
        if geo_urn:
            # Extract numeric ID from urn:li:geo:104305776 or raw 104305776
            geo_id = geo_urn
            if "geo:" in geo_urn:
                geo_id = geo_urn.split("geo:")[-1].strip(")")
            params.append(f'geoUrn=%5B%22{geo_id}%22%5D')

        # Network filter: F=1st, S=2nd, O=3rd+
        if network:
            net_val = quote('["' + '","'.join(network) + '"]')
            params.append(f"network={net_val}")

        # Pagination
        if page_num > 1:
            params.append(f"page={page_num}")

        url = "https://www.linkedin.com/search/results/people/?" + "&".join(params)

        print(f"  [Search-DOM] Loading page {page_num}: {url[:100]}...")
        logger.info("DOM scraping page %d: %s", page_num, url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception as exc:
            logger.error("DOM scraping navigation failed: %s", exc)
            return all_leads, f"Page load failed: {exc}"

        # Wait for profile links to appear on the page (much more reliable
        # than waiting a fixed time — we wait for the actual content)
        try:
            await page.wait_for_selector(
                'a[href*="/in/"]', timeout=12000
            )
            print(f"  [Search-DOM] Profile links detected, waiting for full render...")
        except Exception:
            # Profile links not found within timeout — page might be empty,
            # have CAPTCHA, or still loading. We'll continue and let
            # diagnostic JS figure out what's wrong.
            print(f"  [Search-DOM] No profile links detected within 12s, checking page...")

        # Wait for the page to fully settle after initial load
        await page.wait_for_timeout(2000)

        # Scroll down progressively to trigger lazy-loaded content
        try:
            await page.evaluate("window.scrollTo(0, 400)")
            await page.wait_for_timeout(800)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await page.wait_for_timeout(1000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.75)")
            await page.wait_for_timeout(800)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            # Scroll back up so all results are in DOM
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
        except Exception:
            pass  # scrolling is best-effort

        # Extract results from DOM
        try:
            leads = await page.evaluate(_DOM_SCRAPE_JS)
        except Exception as exc:
            logger.error("DOM scraping JS failed: %s", exc)
            return all_leads, f"DOM extraction failed: {exc}"

        if not leads:
            # Run diagnostic JS to understand WHY we got 0 results
            try:
                diag = await page.evaluate(_DOM_DIAGNOSTIC_JS)
                print(f"  [Search-DOM] DIAGNOSTIC: url={diag.get('url', '?')[:80]}")
                print(f"  [Search-DOM] DIAGNOSTIC: title={diag.get('title', '?')}")
                print(f"  [Search-DOM] DIAGNOSTIC: profileLinks={diag.get('profileLinkCount', 0)}, "
                      f"listItems={diag.get('mainListItems', 0)}")
                print(f"  [Search-DOM] DIAGNOSTIC: loginWall={diag.get('hasLoginWall')}, "
                      f"captcha={diag.get('hasCaptcha')}, noResults={diag.get('noResultsFound')}")
                if diag.get("sampleLinks"):
                    for sl in diag["sampleLinks"][:3]:
                        print(f"    link: href={sl.get('href','')}, text='{sl.get('text','')[:40]}', "
                              f"parent={sl.get('parentTag','')} .{sl.get('parentClass','')[:40]}")
                if diag.get("lisWithProfileLinks"):
                    for li_info in diag["lisWithProfileLinks"][:3]:
                        print(f"    li: class='{li_info.get('liClass','')[:50]}', "
                              f"href={li_info.get('linkHref','')[:50]}, textLen={li_info.get('liTextLen',0)}")
                logger.info("DOM diagnostic: %s", diag)

                # If there ARE profile links but extraction failed, the links
                # might be in nav/header. Log the preview to debug.
                if diag.get("profileLinkCount", 0) > 0 and not diag.get("noResultsFound"):
                    print(f"  [Search-DOM] Profile links exist ({diag['profileLinkCount']}) "
                          f"but extraction got 0 — may need selector fix")
                    # Show first 300 chars of main HTML for debugging
                    preview = diag.get("mainInnerHTMLpreview", "")[:300]
                    if preview:
                        print(f"  [Search-DOM] Main HTML preview: {preview}")
            except Exception as diag_exc:
                print(f"  [Search-DOM] Diagnostic JS failed: {diag_exc}")

            # Check for no results indicator
            if not leads:
                print(f"  [Search-DOM] No leads extracted from page {page_num}")
                if page_num == 1:
                    # On first page with no results, try waiting longer once
                    print(f"  [Search-DOM] Retrying with extra wait...")
                    await page.wait_for_timeout(5000)
                    try:
                        leads = await page.evaluate(_DOM_SCRAPE_JS)
                    except Exception:
                        pass
                    if not leads:
                        break
                else:
                    break

        # Log debug data-view-name info from first result (helps diagnose DOM)
        if leads and leads[0].get("_debug_dvn"):
            print(f"  [Search-DOM] DEBUG data-view-name attributes in first card:")
            for dvn_entry in leads[0]["_debug_dvn"]:
                print(f"    {dvn_entry}")
            # Also log first result's extracted data for verification
            first = leads[0]
            print(f"  [Search-DOM] DEBUG first result: name='{first.get('full_name','')}' "
                  f"headline='{first.get('headline','')[:60]}' "
                  f"location='{first.get('location','')[:40]}'")

        # Extract company from headline for each lead
        for lead in leads:
            if "_debug_dvn" in lead:
                del lead["_debug_dvn"]
            if not lead.get("company"):
                lead["company"] = _extract_company(lead.get("headline", ""))

        all_leads.extend(leads)
        print(f"  [Search-DOM] Page {page_num}: got {len(leads)} leads (total: {len(all_leads)})")
        logger.info("DOM scraping page %d: %d leads (total: %d)", page_num, len(leads), len(all_leads))

        if len(all_leads) >= max_results:
            break

        # Delay between pages (human-like)
        await page.wait_for_timeout(2500)

    return all_leads, None


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
