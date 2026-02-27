"""LinkedPilot v2 — LinkedIn Search router.

Search LinkedIn people using sender's authenticated browser session.
Supports search with filters: location, network degree, company size.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.automation.browser import browser_manager
from app.automation.linkedin_search import _lookup_geo_urn_local, GEO_URN_MAP

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
logger = logging.getLogger(__name__)

# In-memory cache of last search results (per session, simple approach)
_last_search_results: list[dict] = []


def _build_geo_locations() -> list[dict]:
    """Build a sorted, deduplicated list of {name, geoUrn} from GEO_URN_MAP."""
    seen_urns: dict[str, str] = {}  # geo_id -> best display name
    # Prefer longer, more descriptive names (e.g. "United Kingdom" over "UK")
    for name, geo_id in GEO_URN_MAP.items():
        existing = seen_urns.get(geo_id, "")
        if len(name) > len(existing):
            seen_urns[geo_id] = name
    locations = []
    for geo_id, name in seen_urns.items():
        locations.append({
            "name": name.title(),
            "geoUrn": f"urn:li:geo:{geo_id}",
        })
    locations.sort(key=lambda x: x["name"])
    return locations


# Pre-build once at import time
_GEO_LOCATIONS = _build_geo_locations()


def _build_template_context(request, senders, lists, **extra):
    """Build the base template context for search page."""
    ctx = {
        "request": request,
        "senders": senders,
        "lists": lists,
        "results": [],
        "search_query": "",
        "search_location": "",
        "search_network": "",
        "search_company_size": "",
        "total_results": 0,
        "active_page": "search",
        "geo_locations": _GEO_LOCATIONS,
    }
    ctx.update(extra)
    return ctx


async def _get_senders_and_lists():
    """Fetch senders with browser status and lists."""
    db = await get_lp_db()

    cursor = await db.execute(
        "SELECT id, name FROM senders WHERE status IN ('active', 'paused') ORDER BY name"
    )
    senders = [dict(r) for r in await cursor.fetchall()]
    for s in senders:
        s["browser_open"] = browser_manager.is_open(s["id"])

    cursor = await db.execute("SELECT id, name FROM custom_lists ORDER BY name")
    lists = [dict(r) for r in await cursor.fetchall()]

    return senders, lists


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    """Show the LinkedIn search page."""
    senders, lists = await _get_senders_and_lists()
    return templates.TemplateResponse(
        "search.html",
        _build_template_context(request, senders, lists),
    )


@router.post("/search/run")
async def run_search(
    request: Request,
    keywords: str = Form(...),
    sender_id: int = Form(...),
    max_results: int = Form(100),
    location: str = Form(""),
    geo_urn: str = Form(""),
    network: str = Form(""),
    company_size: str = Form(""),
):
    """Execute a LinkedIn people search using the sender's browser."""
    global _last_search_results
    from app.automation.linkedin_search import search_people

    senders, lists = await _get_senders_and_lists()
    extra = {
        "search_query": keywords,
        "search_location": location,
        "search_network": network,
        "search_company_size": company_size,
    }

    # Validate sender has browser open
    if not browser_manager.is_open(sender_id):
        return templates.TemplateResponse("search.html", _build_template_context(
            request, senders, lists,
            error="Browser is not open for this sender. Go to Senders page and click 'Open Chrome & Login' first.",
            **extra,
        ))

    # Cap at 999
    max_results = min(max_results, 999)

    # Parse filters
    network_filter = None
    if network:
        network_filter = [network]

    company_size_filter = None
    if company_size:
        company_size_filter = [company_size]

    try:
        page = await browser_manager.get_page(sender_id)
        results, search_error = await search_people(
            page,
            keywords,
            max_results=max_results,
            location=location,
            geo_urn=geo_urn,
            network=network_filter,
            company_size=company_size_filter,
        )
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        results = []
        search_error = str(exc)

    # Cache results
    _last_search_results = results

    error = None
    if search_error:
        if results:
            error = f"Partial results. Warning: {search_error}"
        else:
            error = f"Search failed: {search_error}"

    return templates.TemplateResponse("search.html", _build_template_context(
        request, senders, lists,
        results=results,
        total_results=len(results),
        error=error,
        **extra,
    ))


@router.get("/search/geo-lookup")
async def geo_lookup(request: Request, q: str = "", sender_id: str = ""):
    """Live location autocomplete using LinkedIn's typeahead API.

    Called by the search form's location input as the user types.
    Returns a list of {name, geoUrn} matching the query.
    """
    if not q or len(q) < 2:
        return JSONResponse([])

    # 1. Check hardcoded mapping first (instant, no browser needed)
    query_lower = q.strip().lower()
    local_matches = []
    for name, geo_id in GEO_URN_MAP.items():
        if name.startswith(query_lower) or query_lower in name:
            # Capitalize the display name nicely
            display_name = name.title()
            urn = f"urn:li:geo:{geo_id}"
            if not any(m["geoUrn"] == urn for m in local_matches):
                local_matches.append({"name": display_name, "geoUrn": urn})
    if local_matches:
        logger.info("Geo typeahead for '%s' matched %d from local mapping", q, len(local_matches))
        return JSONResponse(local_matches[:10])

    # 2. Try LinkedIn typeahead API via browser
    # Parse sender_id safely (JS may send empty string)
    try:
        sid = int(sender_id) if sender_id else 0
    except (ValueError, TypeError):
        sid = 0

    if not sid or not browser_manager.is_open(sid):
        # Find any open sender as fallback
        db = await get_lp_db()
        cursor = await db.execute(
            "SELECT id FROM senders WHERE status IN ('active', 'paused') ORDER BY id"
        )
        for row in await cursor.fetchall():
            if browser_manager.is_open(row["id"]):
                sid = row["id"]
                break
        if not sid or not browser_manager.is_open(sid):
            return JSONResponse([])

    GEO_TYPEAHEAD_JS = """
    async (query) => {
        const csrfToken = document.cookie
            .split('; ')
            .find(c => c.startsWith('JSESSIONID='))
            ?.split('=')[1]
            ?.replace(/"/g, '') || '';
        if (!csrfToken) return [];

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

        // Helper: extract display name from an element
        function findName(el, urnToName) {
            const name = el?.displayText?.text || el?.text?.text
                || el?.title?.text || el?.defaultLocalizedName
                || el?.hitInfo?.['com.linkedin.voyager.typeahead.TypeaheadGeo']?.defaultLocalizedName
                || el?.name || '';
            if (name) return name;
            const urn = findGeoUrn(el);
            return (urn && urnToName[urn]) || '';
        }

        // Try multiple typeahead endpoints
        const urls = [
            'https://www.linkedin.com/voyager/api/typeahead/hitsV2?' +
                new URLSearchParams({ keywords: query, origin: 'GLOBAL_SEARCH_HEADER', q: 'type', type: 'GEO', count: '10' }).toString(),
            'https://www.linkedin.com/voyager/api/graphql?variables=(query:' + encodeURIComponent(query) +
                ',type:GEO,count:10)&queryId=voyagerSearchDashTypeaheadByGlobalTypeahead.bcc3b0a84a2a75b7e5c67c0808245e61',
        ];

        for (const url of urls) {
            try {
                const resp = await fetch(url, {
                    headers: {
                        'csrf-token': csrfToken,
                        'x-restli-protocol-version': '2.0.0',
                    },
                    credentials: 'include',
                });
                if (!resp.ok) continue;
                const data = await resp.json();

                const results = [];
                const included = data?.included || [];

                // Gather elements from all possible locations
                const allElements = [
                    ...(data?.elements || []),
                    ...(data?.data?.elements || []),
                    ...((data?.data?.searchDashTypeaheadByGlobalTypeahead || {})?.elements || []),
                    ...((data?.data?.typeaheadByGlobalTypeahead || {})?.elements || []),
                ];

                // Build URN→name map from included
                const urnToName = {};
                for (const el of included) {
                    const urn = el?.entityUrn || '';
                    const name = el?.defaultLocalizedName || el?.text?.text || el?.name || '';
                    if (urn && name) urnToName[urn] = name;
                }

                // Parse all elements
                const seen = new Set();
                for (const el of allElements) {
                    const urn = findGeoUrn(el);
                    const name = findName(el, urnToName);
                    if (urn && name && !seen.has(urn)) {
                        seen.add(urn);
                        results.push({ name: name, geoUrn: urn });
                    }
                }

                // Fallback: use included directly
                if (results.length === 0) {
                    for (const el of included) {
                        const urn = findGeoUrn(el);
                        const name = el?.defaultLocalizedName || el?.text?.text || '';
                        if (urn && name && !seen.has(urn)) {
                            seen.add(urn);
                            results.push({ name: name, geoUrn: urn });
                        }
                    }
                }

                if (results.length > 0) return results;
            } catch (e) {
                continue;
            }
        }
        return [];
    }
    """

    try:
        page = await browser_manager.get_page(sid)
        results = await page.evaluate(GEO_TYPEAHEAD_JS, q)
        if not results:
            logger.warning("Geo typeahead returned empty for query: '%s'", q)
        else:
            logger.info("Geo typeahead for '%s' returned %d results", q, len(results))
        return JSONResponse(results or [])
    except Exception as exc:
        logger.warning("Geo lookup error: %s", exc)
        return JSONResponse([])


@router.post("/search/add-to-list")
async def add_search_results_to_list(
    request: Request,
    list_name: str = Form(""),
    new_list_name: str = Form(""),
    selected_indices: str = Form(""),
):
    """Add selected search results to a list."""
    global _last_search_results
    db = await get_lp_db()

    # Determine target list
    target = new_list_name.strip() if new_list_name.strip() else list_name.strip()
    if not target:
        return RedirectResponse(url="/search", status_code=303)

    # Get or create list
    cursor = await db.execute("SELECT id FROM custom_lists WHERE name = ?", (target,))
    row = await cursor.fetchone()
    if row:
        list_id = row["id"]
    else:
        cursor = await db.execute(
            "INSERT INTO custom_lists (name, source) VALUES (?, 'search')",
            (target,),
        )
        await db.commit()
        list_id = cursor.lastrowid

    # Parse selected indices
    indices = [int(i.strip()) for i in selected_indices.split(",") if i.strip().isdigit()]
    added = 0
    now = datetime.utcnow().isoformat()

    for idx in indices:
        if idx < 0 or idx >= len(_last_search_results):
            continue
        lead = _last_search_results[idx]
        profile_url = lead.get("profile_url", "")
        if not profile_url:
            continue

        # Skip duplicate
        cursor = await db.execute(
            "SELECT id FROM custom_list_leads WHERE list_id = ? AND profile_url = ?",
            (list_id, profile_url),
        )
        if await cursor.fetchone():
            continue

        await db.execute(
            """
            INSERT INTO custom_list_leads
                (list_id, full_name, first_name, headline, company,
                 location, profile_url, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'search', ?)
            """,
            (
                list_id,
                lead.get("full_name", ""),
                lead.get("first_name", ""),
                lead.get("headline", ""),
                lead.get("company", ""),
                lead.get("location", ""),
                profile_url,
                now,
            ),
        )
        added += 1

    # Update lead count
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?", (list_id,)
    )
    count_row = await cursor.fetchone()
    await db.execute(
        "UPDATE custom_lists SET lead_count = ? WHERE id = ?",
        (count_row["cnt"] if count_row else 0, list_id),
    )
    await db.commit()

    logger.info("Added %d search results to list '%s' (id=%d)", added, target, list_id)

    return JSONResponse({
        "success": True,
        "added": added,
        "list_name": target,
    })


@router.get("/search/debug")
async def debug_search(request: Request, sender_id: int = 0):
    """Diagnostic endpoint: test all search methods and report what works.

    Visit: http://localhost:8080/search/debug?sender_id=1
    Returns JSON with test results for each approach.
    """
    results = {"tests": [], "sender_id": sender_id}

    if not sender_id:
        # Find any open sender
        db = await get_lp_db()
        cursor = await db.execute(
            "SELECT id, name FROM senders WHERE status IN ('active', 'paused') ORDER BY id"
        )
        for row in await cursor.fetchall():
            if browser_manager.is_open(row["id"]):
                sender_id = row["id"]
                results["sender_id"] = sender_id
                results["sender_name"] = row["name"]
                break

    if not sender_id or not browser_manager.is_open(sender_id):
        results["error"] = "No sender has an open browser. Go to Senders page and click 'Open Chrome & Login' first."
        return JSONResponse(results)

    page = await browser_manager.get_page(sender_id)

    # Test 1: Check CSRF token
    csrf = await page.evaluate("""
        () => {
            const c = document.cookie.split('; ').find(c => c.startsWith('JSESSIONID='));
            return c ? c.split('=')[1].replace(/"/g, '') : null;
        }
    """)
    results["tests"].append({
        "name": "CSRF Token",
        "ok": bool(csrf),
        "detail": csrf[:20] + "..." if csrf else "NOT FOUND - not logged in?"
    })

    if not csrf:
        return JSONResponse(results)

    # Test 2: Try network interception to capture live queryId
    from app.automation.linkedin_search import capture_query_id_via_navigation
    captured_qid = None
    try:
        captured_qid = await capture_query_id_via_navigation(page)
        results["tests"].append({
            "name": "Live queryId capture (navigation intercept)",
            "ok": bool(captured_qid),
            "detail": captured_qid or "Could not capture"
        })
    except Exception as e:
        results["tests"].append({
            "name": "Live queryId capture (navigation intercept)",
            "ok": False,
            "detail": f"Error: {e}"
        })

    # Test 3: Try GraphQL hashes with simple keyword "CEO"
    hashes = [
        'voyagerSearchDashClusters.b0928897b71bd00a5a7291755dcd64f0',
        'voyagerSearchDashClusters.994bf4e7d2173b92ccdb5935710c3c5d',
        'voyagerSearchDashClusters.52fec77d08aa4598c8a056ca6bce6c11',
    ]
    if captured_qid:
        hashes.insert(0, captured_qid)

    for qid in hashes:
        test_result = await page.evaluate("""
            async ({qid}) => {
                const csrfToken = document.cookie.split('; ')
                    .find(c => c.startsWith('JSESSIONID='))?.split('=')[1]?.replace(/"/g, '') || '';
                const variables = '(start:0,origin:GLOBAL_SEARCH_HEADER,query:(keywords:CEO,flagshipSearchIntent:SEARCH_SRP,queryParameters:List((key:resultType,value:List(PEOPLE))),includeFiltersInResponse:false))';
                const url = 'https://www.linkedin.com/voyager/api/graphql?variables=' + variables + '&queryId=' + qid;
                try {
                    const resp = await fetch(url, {
                        headers: {
                            'csrf-token': csrfToken,
                            'accept': 'application/vnd.linkedin.normalized+json+2.1',
                            'x-restli-protocol-version': '2.0.0',
                        },
                        credentials: 'include',
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        return { status: resp.status, ok: true, includedCount: (data.included || []).length };
                    }
                    let body = '';
                    try { body = await resp.text(); } catch(e) {}
                    return { status: resp.status, ok: false, body: body.substring(0, 200) };
                } catch(e) {
                    return { status: 0, ok: false, error: e.message };
                }
            }
        """, {"qid": qid})
        results["tests"].append({
            "name": f"GraphQL hash: {qid.split('.')[-1][:12]}...",
            "ok": test_result.get("ok", False),
            "detail": f"status={test_result.get('status')} included={test_result.get('includedCount', 0)}" if test_result.get("ok") else f"status={test_result.get('status')} {test_result.get('body', test_result.get('error', ''))}"
        })

    # Test 4: REST endpoint
    rest_result = await page.evaluate("""
        async () => {
            const csrfToken = document.cookie.split('; ')
                .find(c => c.startsWith('JSESSIONID='))?.split('=')[1]?.replace(/"/g, '') || '';
            const url = 'https://www.linkedin.com/voyager/api/search/dash/clusters?' +
                'decorationId=com.linkedin.voyager.dash.deco.search.SearchClusterCollection-175' +
                '&origin=GLOBAL_SEARCH_HEADER&q=all' +
                '&query=(keywords:CEO,flagshipSearchIntent:SEARCH_SRP,' +
                'queryParameters:List((key:resultType,value:List(PEOPLE))))' +
                '&start=0&count=10';
            try {
                const resp = await fetch(url, {
                    headers: {
                        'csrf-token': csrfToken,
                        'accept': 'application/vnd.linkedin.normalized+json+2.1',
                        'x-restli-protocol-version': '2.0.0',
                    },
                    credentials: 'include',
                });
                if (resp.ok) {
                    const data = await resp.json();
                    return { status: resp.status, ok: true, includedCount: (data.included || []).length };
                }
                let body = '';
                try { body = await resp.text(); } catch(e) {}
                return { status: resp.status, ok: false, body: body.substring(0, 200) };
            } catch(e) {
                return { status: 0, ok: false, error: e.message };
            }
        }
    """)
    results["tests"].append({
        "name": "REST endpoint (non-GraphQL)",
        "ok": rest_result.get("ok", False),
        "detail": f"status={rest_result.get('status')} included={rest_result.get('includedCount', 0)}" if rest_result.get("ok") else f"status={rest_result.get('status')} {rest_result.get('body', rest_result.get('error', ''))}"
    })

    # Test 5: Try actual search_people function
    try:
        from app.automation.linkedin_search import search_people
        leads, err = await search_people(page, "CEO", max_results=10)
        results["tests"].append({
            "name": "Full search_people('CEO', max=10)",
            "ok": len(leads) > 0,
            "detail": f"{len(leads)} leads found" + (f", error: {err}" if err else "")
        })
    except Exception as e:
        results["tests"].append({
            "name": "Full search_people('CEO', max=10)",
            "ok": False,
            "detail": f"Exception: {e}"
        })

    # Summary
    passed = sum(1 for t in results["tests"] if t["ok"])
    total = len(results["tests"])
    results["summary"] = f"{passed}/{total} tests passed"

    return JSONResponse(results, status_code=200)


@router.post("/search/enrich")
async def enrich_lead(
    request: Request,
    lead_id: int = Form(...),
    sender_id: int = Form(...),
):
    """Enrich a single lead's profile using LinkedIn Voyager API."""
    from app.automation.linkedin_search import enrich_profile

    db = await get_lp_db()

    # Get the lead
    cursor = await db.execute(
        "SELECT * FROM custom_list_leads WHERE id = ?", (lead_id,)
    )
    lead = await cursor.fetchone()
    if not lead:
        return JSONResponse({"success": False, "error": "Lead not found"})

    lead = dict(lead)
    profile_url = lead.get("profile_url", "")
    if not profile_url:
        return JSONResponse({"success": False, "error": "No profile URL"})

    # Check sender's browser is open
    if not browser_manager.is_open(sender_id):
        return JSONResponse({"success": False, "error": "Browser not open for sender"})

    try:
        page = await browser_manager.get_page(sender_id)
        enriched = await enrich_profile(page, profile_url)
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})

    if "error" in enriched:
        return JSONResponse({"success": False, "error": enriched["error"]})

    # Update lead with enriched data
    updates = []
    params = []
    for field in ["full_name", "first_name", "headline", "company", "location"]:
        if enriched.get(field):
            updates.append(f"{field} = ?")
            params.append(enriched[field])

    if updates:
        params.append(lead_id)
        await db.execute(
            f"UPDATE custom_list_leads SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()

    return JSONResponse({
        "success": True,
        "enriched": enriched,
    })
