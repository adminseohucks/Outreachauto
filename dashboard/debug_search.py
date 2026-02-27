"""
LinkedIn Search Diagnostic Script
==================================
Run this on your LAPTOP where the browser is open and logged into LinkedIn.

Usage:
    python debug_search.py

This will:
1. Connect to your existing browser session
2. Try each queryId hash individually
3. Try the REST endpoint
4. Try simple keywords vs OR keywords
5. Report exactly what works and what fails
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.automation.browser import browser_manager
from app.database import get_lp_db


async def test_search_endpoints(page):
    """Test all search endpoints and report results."""

    print("\n" + "=" * 70)
    print("  LinkedIn Search Diagnostic")
    print("=" * 70)

    # Step 1: Check if we're on LinkedIn and logged in
    print("\n[1] Checking LinkedIn login status...")
    current_url = page.url
    print(f"    Current URL: {current_url}")

    if "linkedin.com" not in current_url:
        print("    NOT on LinkedIn! Navigating...")
        await page.goto("https://www.linkedin.com/feed/", timeout=30000)
        await page.wait_for_timeout(3000)

    # Check CSRF token
    csrf = await page.evaluate("""
        () => {
            const c = document.cookie.split('; ').find(c => c.startsWith('JSESSIONID='));
            return c ? c.split('=')[1].replace(/"/g, '') : null;
        }
    """)
    if csrf:
        print(f"    CSRF token: {csrf[:20]}... OK")
    else:
        print("    ERROR: No CSRF token! Not logged in?")
        return

    # Step 2: Try to extract dynamic queryId
    print("\n[2] Trying to extract dynamic queryId from LinkedIn's JS...")

    # Strategy A: Ember module system
    dynamic_qid = await page.evaluate("""
        () => {
            try {
                if (window.require && window.require.entries) {
                    const entries = Object.keys(window.require.entries);
                    const matches = entries.filter(k => k.includes('voyagerSearchDashClusters'));
                    return { found: matches.length, entries: matches.slice(0, 5) };
                }
                return { found: 0, note: 'window.require.entries not available' };
            } catch(e) {
                return { found: 0, error: e.message };
            }
        }
    """)
    print(f"    Ember modules: {dynamic_qid}")

    # Step 3: Test each GraphQL queryId hash
    hashes = [
        'b0928897b71bd00a5a7291755dcd64f0',
        '994bf4e7d2173b92ccdb5935710c3c5d',
        '52fec77d08aa4598c8a056ca6bce6c11',
        '1f5ea36a42fc3319f534af1022b6dd64',
    ]

    # Simple keyword (no OR operators, no filters)
    simple_keyword = "CEO"
    simple_variables = (
        f"(start:0,origin:GLOBAL_SEARCH_HEADER,"
        f"query:(keywords:{simple_keyword},"
        f"flagshipSearchIntent:SEARCH_SRP,"
        f"queryParameters:List((key:resultType,value:List(PEOPLE))),"
        f"includeFiltersInResponse:false))"
    )

    print(f"\n[3] Testing GraphQL hashes with simple keyword '{simple_keyword}'...")
    for h in hashes:
        qid = f"voyagerSearchDashClusters.{h}"
        status = await page.evaluate("""
            async ({url}) => {
                const csrfToken = document.cookie.split('; ')
                    .find(c => c.startsWith('JSESSIONID='))?.split('=')[1]?.replace(/"/g, '') || '';
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
                        const keys = Object.keys(data || {});
                        const included = (data.included || []).length;
                        return { status: resp.status, ok: true, keys, includedCount: included };
                    }
                    let body = '';
                    try { body = await resp.text(); } catch(e) {}
                    return { status: resp.status, ok: false, body: body.substring(0, 200) };
                } catch(e) {
                    return { status: 0, ok: false, error: e.message };
                }
            }
        """, {
            "url": f"https://www.linkedin.com/voyager/api/graphql?variables={simple_variables}&queryId={qid}"
        })
        marker = "OK" if status.get("ok") else "FAIL"
        print(f"    [{marker}] {h[:12]}... => status={status.get('status')}", end="")
        if status.get("ok"):
            print(f" keys={status.get('keys')} included={status.get('includedCount')}")
        else:
            print(f" body={status.get('body', status.get('error', ''))}")

    # Step 4: Test REST endpoint (non-GraphQL)
    print(f"\n[4] Testing REST endpoint (non-GraphQL)...")
    rest_status = await page.evaluate("""
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
                    const keys = Object.keys(data || {});
                    const included = (data.included || []).length;
                    return { status: resp.status, ok: true, keys, includedCount: included };
                }
                let body = '';
                try { body = await resp.text(); } catch(e) {}
                return { status: resp.status, ok: false, body: body.substring(0, 200) };
            } catch(e) {
                return { status: 0, ok: false, error: e.message };
            }
        }
    """)
    marker = "OK" if rest_status.get("ok") else "FAIL"
    print(f"    [{marker}] REST endpoint => status={rest_status.get('status')}", end="")
    if rest_status.get("ok"):
        print(f" keys={rest_status.get('keys')} included={rest_status.get('includedCount')}")
    else:
        print(f" body={rest_status.get('body', rest_status.get('error', ''))}")

    # Step 5: Test with OR keywords (the actual failing query)
    or_keywords = "President OR Entrepreneur OR Founder OR CEO"
    encoded_kw = or_keywords.replace(" ", "%20")
    or_variables = (
        f"(start:0,origin:GLOBAL_SEARCH_HEADER,"
        f"query:(keywords:{encoded_kw},"
        f"flagshipSearchIntent:SEARCH_SRP,"
        f"queryParameters:List((key:resultType,value:List(PEOPLE))),"
        f"includeFiltersInResponse:false))"
    )

    print(f"\n[5] Testing with OR keywords: '{or_keywords}'...")
    # Use first hash that worked above, or try first one
    best_hash = hashes[0]
    qid = f"voyagerSearchDashClusters.{best_hash}"
    or_status = await page.evaluate("""
        async ({url}) => {
            const csrfToken = document.cookie.split('; ')
                .find(c => c.startsWith('JSESSIONID='))?.split('=')[1]?.replace(/"/g, '') || '';
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
                    const included = (data.included || []).length;
                    return { status: resp.status, ok: true, includedCount: included };
                }
                let body = '';
                try { body = await resp.text(); } catch(e) {}
                return { status: resp.status, ok: false, body: body.substring(0, 200) };
            } catch(e) {
                return { status: 0, ok: false, error: e.message };
            }
        }
    """, {
        "url": f"https://www.linkedin.com/voyager/api/graphql?variables={or_variables}&queryId={qid}"
    })
    marker = "OK" if or_status.get("ok") else "FAIL"
    print(f"    [{marker}] OR keywords => status={or_status.get('status')}", end="")
    if or_status.get("ok"):
        print(f" included={or_status.get('includedCount')}")
    else:
        print(f" body={or_status.get('body', or_status.get('error', ''))}")

    # Step 6: Test with geoUrn filter (the full failing query)
    geo_variables = (
        f"(start:0,origin:GLOBAL_SEARCH_HEADER,"
        f"query:(keywords:{encoded_kw},"
        f"flagshipSearchIntent:SEARCH_SRP,"
        f"queryParameters:List((key:resultType,value:List(PEOPLE)),(key:geoUrn,value:List(urn:li:geo:103644278))),"
        f"includeFiltersInResponse:false))"
    )

    print(f"\n[6] Testing with OR keywords + geoUrn (USA)...")
    geo_status = await page.evaluate("""
        async ({url}) => {
            const csrfToken = document.cookie.split('; ')
                .find(c => c.startsWith('JSESSIONID='))?.split('=')[1]?.replace(/"/g, '') || '';
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
                    const included = (data.included || []).length;
                    return { status: resp.status, ok: true, includedCount: included };
                }
                let body = '';
                try { body = await resp.text(); } catch(e) {}
                return { status: resp.status, ok: false, body: body.substring(0, 300) };
            } catch(e) {
                return { status: 0, ok: false, error: e.message };
            }
        }
    """, {
        "url": f"https://www.linkedin.com/voyager/api/graphql?variables={geo_variables}&queryId={qid}"
    })
    marker = "OK" if geo_status.get("ok") else "FAIL"
    print(f"    [{marker}] OR + geoUrn => status={geo_status.get('status')}", end="")
    if geo_status.get("ok"):
        print(f" included={geo_status.get('includedCount')}")
    else:
        print(f" body={geo_status.get('body', geo_status.get('error', ''))}")

    # Step 7: Try intercepting queryId from a real search navigation
    print(f"\n[7] Trying to intercept queryId from LinkedIn's own search...")
    intercepted = await page.evaluate("""
        async () => {
            // Navigate to search results page and intercept the API calls
            const intercepted = [];
            const origFetch = window.fetch;
            window.fetch = function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
                if (url.includes('voyagerSearchDash') || url.includes('search/dash')) {
                    intercepted.push(url.substring(0, 300));
                }
                return origFetch.apply(this, args);
            };

            // Trigger a search by navigating
            try {
                const searchUrl = 'https://www.linkedin.com/search/results/people/?keywords=CEO&origin=GLOBAL_SEARCH_HEADER';
                const resp = await fetch(searchUrl, { credentials: 'include', redirect: 'follow' });
                // Wait a bit for any subsequent API calls
                await new Promise(r => setTimeout(r, 2000));
            } catch(e) {}

            // Restore original fetch
            window.fetch = origFetch;

            return intercepted;
        }
    """)
    if intercepted:
        print(f"    Intercepted {len(intercepted)} API calls:")
        for url in intercepted:
            # Extract queryId if present
            if 'queryId=' in url:
                qid_part = url.split('queryId=')[1].split('&')[0]
                print(f"    => queryId: {qid_part}")
            else:
                print(f"    => {url[:100]}...")
    else:
        print("    No API calls intercepted (search may use different mechanism)")

    print("\n" + "=" * 70)
    print("  DIAGNOSTIC COMPLETE")
    print("=" * 70)
    print("\n  Share the output above so we can fix the exact issue.\n")


async def main():
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env")

    db = await get_lp_db()

    # Find a sender with an open browser
    cursor = await db.execute(
        "SELECT id, name FROM senders WHERE status IN ('active', 'paused') ORDER BY id"
    )
    senders = [dict(r) for r in await cursor.fetchall()]

    if not senders:
        print("ERROR: No senders found in database!")
        print("Make sure the dashboard has been set up with at least one sender.")
        return

    print("Available senders:")
    for s in senders:
        is_open = browser_manager.is_open(s["id"])
        print(f"  [{s['id']}] {s['name']} - Browser: {'OPEN' if is_open else 'closed'}")

    # Find sender with open browser
    sender_id = None
    for s in senders:
        if browser_manager.is_open(s["id"]):
            sender_id = s["id"]
            break

    if not sender_id:
        print("\nERROR: No sender has an open browser!")
        print("Open the dashboard, go to Senders, and click 'Open Chrome & Login' first.")
        return

    print(f"\nUsing sender #{sender_id} for testing...")
    page = await browser_manager.get_page(sender_id)
    await test_search_endpoints(page)


if __name__ == "__main__":
    asyncio.run(main())
