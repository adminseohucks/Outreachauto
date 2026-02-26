"""
LinkedIn Like engine for LinkedPilot v2.

Navigates to a profile, finds the most recent post, and clicks the Like
button.  Every step is wrapped in try/except so a single DOM change never
crashes the whole pipeline.
"""

import logging
from typing import Dict, Any

from playwright.async_api import Page

from . import human_delay
from .page_identity import switch_like_identity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector strategies — LinkedIn changes its DOM frequently, so we try
# multiple approaches in order of reliability.
# ---------------------------------------------------------------------------

# Candidate selectors for the "Recent Activity" / posts section
ACTIVITY_SECTION_SELECTORS = [
    "section.pv-recent-activity-section-v2",
    "div.pv-recent-activity-section-v2",
    'section[data-section="recent-activity"]',
    "#content_collections",
    "div.feed-shared-update-v2",
    'main[aria-label="Recent activity"]',
]

# Candidate selectors for individual post containers
POST_CONTAINER_SELECTORS = [
    "div.feed-shared-update-v2",
    "div.occludable-update",
    'li.profile-creator-shared-feed-update__container',
    'div[data-urn*="activity"]',
]

# Candidate selectors for the Like button inside a post
LIKE_BUTTON_SELECTORS = [
    'button[aria-label*="Like"]',
    'button.react-button__trigger[aria-label*="Like"]',
    'button span.reactions-react-button',
    'button[aria-pressed] svg[data-test-icon="thumbs-up-outline-medium"]',
]


async def like_latest_post(
    page: Page, profile_url: str, company_page_name: str | None = None,
) -> Dict[str, Any]:
    """Navigate to *profile_url*, find the latest post, and like it.

    If *company_page_name* is given, attempts to switch the like identity
    to that company page before clicking Like.

    Returns
    -------
    dict
        ``{success: bool, post_url: str | None, skipped: bool | None, error: str | None}``
    """
    result: Dict[str, Any] = {
        "success": False,
        "post_url": None,
        "skipped": None,
        "error": None,
    }

    # ------------------------------------------------------------------
    # 1. Navigate to profile
    # ------------------------------------------------------------------
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await human_delay.random_delay(2, 4)
    except Exception as exc:
        result["error"] = f"Failed to navigate to profile: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 2. Wait for page load — try known selectors
    # ------------------------------------------------------------------
    try:
        await page.wait_for_selector("main", timeout=15000)
    except Exception as exc:
        result["error"] = f"Page did not load in time: {exc}"
        logger.error(result["error"])
        return result

    # Scroll down a bit so the activity section loads
    try:
        await human_delay.human_scroll(page)
        await human_delay.random_delay(1, 2)
        await human_delay.human_scroll(page)
        await human_delay.random_delay(1, 2)
    except Exception:
        pass  # scrolling is best-effort

    # ------------------------------------------------------------------
    # 3. Look for the activity / posts section
    # ------------------------------------------------------------------
    activity_found = False
    for selector in ACTIVITY_SECTION_SELECTORS:
        try:
            element = await page.query_selector(selector)
            if element:
                activity_found = True
                logger.debug("Activity section found via %s", selector)
                break
        except Exception:
            continue

    if not activity_found:
        # Not fatal — posts might still be present in the main feed area
        logger.warning("Could not locate dedicated activity section")

    # ------------------------------------------------------------------
    # 4. Find the first post container
    # ------------------------------------------------------------------
    post_element = None
    for selector in POST_CONTAINER_SELECTORS:
        try:
            post_element = await page.query_selector(selector)
            if post_element:
                logger.debug("Post container found via %s", selector)
                break
        except Exception:
            continue

    if post_element is None:
        result["error"] = "No posts found on profile"
        logger.error(result["error"])
        return result

    # Try to grab the post URL (href inside the post timestamp link)
    try:
        link = await post_element.query_selector("a[href*='/feed/update/']")
        if link:
            result["post_url"] = await link.get_attribute("href")
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 5. Find the Like button
    # ------------------------------------------------------------------
    like_button = None
    for selector in LIKE_BUTTON_SELECTORS:
        try:
            like_button = await post_element.query_selector(selector)
            if like_button:
                logger.debug("Like button found via %s", selector)
                break
        except Exception:
            continue

    if like_button is None:
        result["error"] = "Like button not found on latest post"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 6. Check if already liked
    # ------------------------------------------------------------------
    try:
        aria_pressed = await like_button.get_attribute("aria-pressed")
        aria_label = (await like_button.get_attribute("aria-label")) or ""
        if aria_pressed == "true" or "Unlike" in aria_label:
            result["success"] = True
            result["skipped"] = True
            logger.info("Post already liked — skipping")
            return result
    except Exception:
        pass  # proceed to click

    # ------------------------------------------------------------------
    # 6b. Switch to company page identity if requested
    # ------------------------------------------------------------------
    if company_page_name:
        switched = await switch_like_identity(page, company_page_name, post_element)
        if switched:
            logger.info("Switched like identity to '%s'", company_page_name)
        else:
            logger.warning("Could not switch to '%s' — liking as personal profile", company_page_name)

    # ------------------------------------------------------------------
    # 7. Click the Like button
    # ------------------------------------------------------------------
    try:
        await human_delay.random_mouse_move(page)
        await human_delay.random_delay(0.5, 1.5)
        await like_button.click()
        await human_delay.random_delay(1, 3)
        logger.info("Liked latest post on %s%s", profile_url, f" as {company_page_name}" if company_page_name else "")
    except Exception as exc:
        result["error"] = f"Failed to click Like button: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 8. Return success
    # ------------------------------------------------------------------
    result["success"] = True
    result["skipped"] = False
    return result
