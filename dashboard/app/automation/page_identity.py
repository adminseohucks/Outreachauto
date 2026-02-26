"""
LinkedIn Page Identity Switcher for LinkedPilot v2.

Provides helpers to switch the "acting as" identity to a company page
before performing a like or comment.  LinkedIn shows an identity picker
(small avatar/dropdown) in the comment box area and a reaction-type
picker on the Like button that also allows choosing a page identity.
"""

import logging
from typing import Optional

from playwright.async_api import Page

from . import human_delay

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors for the page-identity dropdown (comment box area)
# LinkedIn renders a small clickable avatar near the comment input that opens
# an identity-picker overlay when the user manages company pages.
# ---------------------------------------------------------------------------

IDENTITY_PICKER_SELECTORS = [
    # New LinkedIn UI — avatar button in comment box
    'button.comments-comment-box-comment-text-field__avatar',
    'button[aria-label*="Commenting as"]',
    'div.comments-comment-box__identity button',
    # Fallback — any clickable avatar in the comment form
    'form.comments-comment-box__form button[class*="avatar"]',
    'div.comments-comment-box div[role="button"][class*="avatar"]',
]

# Selectors for the identity option inside the picker dropdown
IDENTITY_OPTION_SELECTORS = [
    # Each option in the identity picker
    'div[role="option"]',
    'li[role="option"]',
    'div.comments-comment-box-identity-picker__item',
    'button[data-control-name*="identity"]',
]

# ---------------------------------------------------------------------------
# Selectors for "React as" on the Like button (long-press / hover shows
# reaction picker that includes page identities)
# ---------------------------------------------------------------------------

REACT_AS_TRIGGER_SELECTORS = [
    # The small dropdown arrow next to the Like button
    'button[aria-label*="React as"]',
    'button[aria-label*="react as"]',
    'button.reactions-react-button__action-bar-action',
    # Some LinkedIn versions show a caret/arrow
    'button.reactions-menu-trigger',
]

REACT_AS_OPTION_SELECTORS = [
    'div[role="menuitem"]',
    'li[role="menuitem"]',
    'div.reactions-menu-item',
    'button[data-control-name*="react_as"]',
]


async def switch_comment_identity(
    page: Page,
    company_page_name: str,
) -> bool:
    """
    Switch the commenting identity to the given company page.

    Must be called AFTER the comment box is open (comment button clicked).

    Returns True if successfully switched, False otherwise.
    """
    logger.info("Attempting to switch comment identity to '%s'", company_page_name)

    # 1. Find and click the identity picker button
    picker_button = None
    for selector in IDENTITY_PICKER_SELECTORS:
        try:
            picker_button = await page.query_selector(selector)
            if picker_button:
                logger.debug("Identity picker found via %s", selector)
                break
        except Exception:
            continue

    if picker_button is None:
        logger.warning("Identity picker button not found — may not have page admin access")
        return False

    try:
        await human_delay.random_delay(0.3, 0.8)
        await picker_button.click()
        await human_delay.random_delay(0.5, 1.0)
    except Exception as exc:
        logger.error("Failed to click identity picker: %s", exc)
        return False

    # 2. Find the option matching the company page name
    return await _select_identity_option(page, company_page_name, IDENTITY_OPTION_SELECTORS)


async def switch_like_identity(
    page: Page,
    company_page_name: str,
    post_element=None,
) -> bool:
    """
    Switch the like/reaction identity to the given company page.

    On LinkedIn, hovering over the Like button reveals a reaction picker
    that may include a "React as [Page]" option.

    Returns True if successfully switched, False otherwise.
    """
    logger.info("Attempting to switch like identity to '%s'", company_page_name)

    # 1. Find "React as" trigger
    trigger = None
    search_root = post_element or page
    for selector in REACT_AS_TRIGGER_SELECTORS:
        try:
            trigger = await search_root.query_selector(selector)
            if trigger:
                logger.debug("React-as trigger found via %s", selector)
                break
        except Exception:
            continue

    if trigger is None:
        logger.warning("React-as trigger not found — will like as personal profile")
        return False

    try:
        await human_delay.random_delay(0.3, 0.8)
        await trigger.click()
        await human_delay.random_delay(0.5, 1.0)
    except Exception as exc:
        logger.error("Failed to click react-as trigger: %s", exc)
        return False

    # 2. Select the company page option
    return await _select_identity_option(page, company_page_name, REACT_AS_OPTION_SELECTORS)


async def _select_identity_option(
    page: Page,
    target_name: str,
    option_selectors: list[str],
) -> bool:
    """Find and click the identity option matching target_name."""
    target_lower = target_name.lower().strip()

    for selector in option_selectors:
        try:
            options = await page.query_selector_all(selector)
            for option in options:
                text = (await option.inner_text()).strip()
                if target_lower in text.lower():
                    logger.info("Found identity option: '%s'", text)
                    await human_delay.random_delay(0.2, 0.5)
                    await option.click()
                    await human_delay.random_delay(0.5, 1.0)
                    return True
        except Exception:
            continue

    logger.warning("Could not find identity option for '%s'", target_name)
    return False
