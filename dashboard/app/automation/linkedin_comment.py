"""
LinkedIn Comment engine for LinkedPilot v2.

Navigates to a profile, finds the latest post, extracts its text, and
submits a comment using human-like typing.
"""

import logging
from typing import Dict, Any, Optional

from playwright.async_api import Page

from . import human_delay
from .page_identity import switch_comment_identity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector strategies
# ---------------------------------------------------------------------------

POST_CONTAINER_SELECTORS = [
    "div.feed-shared-update-v2",
    "div.occludable-update",
    "li.profile-creator-shared-feed-update__container",
    'div[data-urn*="activity"]',
]

POST_TEXT_SELECTORS = [
    "div.feed-shared-update-v2__description",
    "span.feed-shared-text__text-view",
    "div.feed-shared-text",
    "div.update-components-text",
    'span[dir="ltr"]',
]

COMMENT_BUTTON_SELECTORS = [
    'button[aria-label*="Comment"]',
    'button[aria-label*="comment"]',
    "button.comment-button",
    'button span.comment-button__text',
]

COMMENT_INPUT_SELECTORS = [
    'div.ql-editor[data-placeholder="Add a comment…"]',
    "div.ql-editor",
    'div[role="textbox"][aria-label*="comment"]',
    'div[role="textbox"][contenteditable="true"]',
    "div.comments-comment-box__form div[contenteditable]",
]

SUBMIT_COMMENT_SELECTORS = [
    'button.comments-comment-box__submit-button',
    'button[aria-label="Post comment"]',
    'button[type="submit"].comments-comment-box__submit-button',
    'form.comments-comment-box__form button[type="submit"]',
]


async def extract_post_text(
    page: Page, profile_url: str
) -> Dict[str, Any]:
    """Navigate to *profile_url* and extract the text of the latest post.

    Returns
    -------
    dict
        ``{post_text: str | None, post_url: str | None, error: str | None}``
    """
    result: Dict[str, Any] = {
        "post_text": None,
        "post_url": None,
        "error": None,
    }

    # ------------------------------------------------------------------
    # Navigate
    # ------------------------------------------------------------------
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await human_delay.random_delay(2, 4)
    except Exception as exc:
        result["error"] = f"Failed to navigate to profile: {exc}"
        logger.error(result["error"])
        return result

    # Wait for main content
    try:
        await page.wait_for_selector("main", timeout=15000)
    except Exception as exc:
        result["error"] = f"Page did not load in time: {exc}"
        logger.error(result["error"])
        return result

    # Scroll to load activity section
    try:
        await human_delay.human_scroll(page)
        await human_delay.random_delay(1, 2)
        await human_delay.human_scroll(page)
        await human_delay.random_delay(1, 2)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Find latest post
    # ------------------------------------------------------------------
    post_element = None
    for selector in POST_CONTAINER_SELECTORS:
        try:
            post_element = await page.query_selector(selector)
            if post_element:
                break
        except Exception:
            continue

    if post_element is None:
        result["error"] = "No posts found on profile"
        logger.error(result["error"])
        return result

    # Grab post URL
    try:
        link = await post_element.query_selector("a[href*='/feed/update/']")
        if link:
            result["post_url"] = await link.get_attribute("href")
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Extract post text
    # ------------------------------------------------------------------
    post_text = await _extract_text_from_post(post_element)
    result["post_text"] = post_text
    if not post_text:
        result["error"] = "Could not extract post text"
        logger.warning(result["error"])

    return result


async def comment_on_latest_post(
    page: Page,
    profile_url: str,
    comment_text: str,
    company_page_name: str | None = None,
) -> Dict[str, Any]:
    """Navigate to *profile_url*, find the latest post, and leave *comment_text*.

    If *company_page_name* is given, switches the commenting identity to that
    company page after opening the comment box.

    Returns
    -------
    dict
        ``{success: bool, post_text: str | None, post_url: str | None, error: str | None}``
    """
    result: Dict[str, Any] = {
        "success": False,
        "post_text": None,
        "post_url": None,
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
    # 2. Wait for page load
    # ------------------------------------------------------------------
    try:
        await page.wait_for_selector("main", timeout=15000)
    except Exception as exc:
        result["error"] = f"Page did not load in time: {exc}"
        logger.error(result["error"])
        return result

    # Scroll to load activity
    try:
        await human_delay.human_scroll(page)
        await human_delay.random_delay(1, 2)
        await human_delay.human_scroll(page)
        await human_delay.random_delay(1, 2)
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 3. Find latest post
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

    # Grab post URL
    try:
        link = await post_element.query_selector("a[href*='/feed/update/']")
        if link:
            result["post_url"] = await link.get_attribute("href")
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 4. Extract post text content (useful for AI comment selection)
    # ------------------------------------------------------------------
    post_text = await _extract_text_from_post(post_element)
    result["post_text"] = post_text

    # ------------------------------------------------------------------
    # 5. Click "Comment" button to open comment box
    # ------------------------------------------------------------------
    comment_button = None
    for selector in COMMENT_BUTTON_SELECTORS:
        try:
            comment_button = await post_element.query_selector(selector)
            if comment_button:
                logger.debug("Comment button found via %s", selector)
                break
        except Exception:
            continue

    if comment_button is None:
        result["error"] = "Comment button not found on latest post"
        logger.error(result["error"])
        return result

    try:
        await human_delay.random_mouse_move(page)
        await human_delay.random_delay(0.5, 1.0)
        await comment_button.click()
        await human_delay.random_delay(1, 2)
    except Exception as exc:
        result["error"] = f"Failed to click Comment button: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 5b. Switch to company page identity if requested
    # ------------------------------------------------------------------
    if company_page_name:
        switched = await switch_comment_identity(page, company_page_name)
        if switched:
            logger.info("Switched comment identity to '%s'", company_page_name)
        else:
            logger.warning("Could not switch to '%s' — commenting as personal profile", company_page_name)
        await human_delay.random_delay(0.5, 1.0)

    # ------------------------------------------------------------------
    # 6. Type comment using human-like keystrokes
    # ------------------------------------------------------------------
    comment_input_selector: Optional[str] = None
    for selector in COMMENT_INPUT_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el:
                comment_input_selector = selector
                logger.debug("Comment input found via %s", selector)
                break
        except Exception:
            continue

    if comment_input_selector is None:
        result["error"] = "Comment input box not found"
        logger.error(result["error"])
        return result

    try:
        await human_delay.type_like_human(page, comment_input_selector, comment_text)
        await human_delay.random_delay(0.5, 1.5)
    except Exception as exc:
        result["error"] = f"Failed to type comment: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 7. Click "Post" / submit button
    # ------------------------------------------------------------------
    submit_button = None
    for selector in SUBMIT_COMMENT_SELECTORS:
        try:
            submit_button = await page.query_selector(selector)
            if submit_button:
                logger.debug("Submit button found via %s", selector)
                break
        except Exception:
            continue

    if submit_button is None:
        result["error"] = "Submit/Post button not found for comment"
        logger.error(result["error"])
        return result

    try:
        await human_delay.random_delay(0.3, 0.8)
        await submit_button.click()
        await human_delay.random_delay(2, 4)
        logger.info("Comment posted on %s", profile_url)
    except Exception as exc:
        result["error"] = f"Failed to submit comment: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 8. Return success
    # ------------------------------------------------------------------
    result["success"] = True
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _extract_text_from_post(post_element) -> Optional[str]:
    """Try multiple selector strategies to pull text from a post element."""
    for selector in POST_TEXT_SELECTORS:
        try:
            text_el = await post_element.query_selector(selector)
            if text_el:
                text = await text_el.inner_text()
                text = text.strip()
                if text:
                    return text
        except Exception:
            continue
    return None
