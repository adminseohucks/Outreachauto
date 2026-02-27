"""
LinkedIn Connection Request engine for LinkedPilot v2.

Navigates to a profile and sends a connection request with a
personalized note.  Handles the case where the Connect action is
hidden behind the "More" dropdown button on the profile page.
"""

import logging
from typing import Dict, Any

from playwright.async_api import Page

from . import human_delay

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector strategies (ordered from most-specific to least-specific)
# ---------------------------------------------------------------------------

# "Connect" button directly visible on profile page
CONNECT_BUTTON_SELECTORS = [
    'button[aria-label*="Invite"][aria-label*="to connect"]',
    'button.pvs-profile-actions__action[aria-label*="connect"]',
    'button[aria-label*="Connect"]',
    'div.pvs-profile-actions button:has-text("Connect")',
    'main section button:has-text("Connect")',
    'main button:has-text("Connect")',
]

# "More" / "More actions" button on profile (dropdown trigger)
MORE_BUTTON_SELECTORS = [
    'button[aria-label="More actions"]',
    'button[aria-label="More"]',
    'button.artdeco-dropdown__trigger[aria-label*="More"]',
    'div.pvs-profile-actions button[aria-label*="More"]',
    'div.pvs-profile-actions__overflow-toggle button',
    'section.artdeco-card button[aria-label*="More"]',
]

# Connect option inside the "More" dropdown menu
MORE_CONNECT_SELECTORS = [
    'div.artdeco-dropdown__content-inner li:has-text("Connect")',
    'div[role="menuitem"]:has-text("Connect")',
    'li[role="menuitem"] span:has-text("Connect")',
    'div.artdeco-dropdown__content span:has-text("Connect")',
    'div.artdeco-dropdown__content button:has-text("Connect")',
    'ul[role="menu"] li:has-text("Connect")',
]

# "How do you know …" dialog – pick any option to proceed
HOW_DO_YOU_KNOW_SELECTORS = [
    'button[aria-label*="Other"]',
    'label:has-text("Other")',
    'button:has-text("Other")',
    'fieldset button',
]

# "Add a note" button in the connect dialog
ADD_NOTE_BUTTON_SELECTORS = [
    'button[aria-label="Add a note"]',
    'button:has-text("Add a note")',
]

# Note text area in the connect dialog
NOTE_INPUT_SELECTORS = [
    'textarea[name="message"]',
    'textarea#custom-message',
    'textarea[placeholder*="Add a note"]',
    'textarea[placeholder*="add a note"]',
    'textarea[placeholder*="personal note"]',
    'div.send-invite textarea',
    'div.artdeco-modal textarea',
    'textarea',
]

# "Send" / "Send invitation" / "Send now" button in the connect dialog
SEND_BUTTON_SELECTORS = [
    'button[aria-label="Send invitation"]',
    'button[aria-label="Send now"]',
    'button[aria-label="Send"]',
    'div.artdeco-modal button:has-text("Send")',
    'button:has-text("Send invitation")',
    'button:has-text("Send now")',
    'button:has-text("Send")',
]

# "Send without a note" – fallback when note input is not found
SEND_WITHOUT_NOTE_SELECTORS = [
    'button[aria-label="Send without a note"]',
    'button:has-text("Send without a note")',
]


async def send_connection_request(
    page: Page,
    profile_url: str,
    note_text: str | None = None,
) -> Dict[str, Any]:
    """Navigate to *profile_url* and send a connection request.

    If *note_text* is given, clicks "Add a note" and types the note.
    Otherwise sends without a note.

    Returns
    -------
    dict
        ``{success: bool, skipped: bool, error: str | None}``
    """
    result: Dict[str, Any] = {
        "success": False,
        "skipped": False,
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

    await human_delay.random_delay(1, 2)

    # ------------------------------------------------------------------
    # 3. Find the Connect button (direct or inside "More" dropdown)
    # ------------------------------------------------------------------
    connect_button = None

    # 3a – Try direct Connect button first
    for selector in CONNECT_BUTTON_SELECTORS:
        try:
            connect_button = await page.query_selector(selector)
            if connect_button and await connect_button.is_visible():
                logger.debug("Connect button found directly via %s", selector)
                break
            connect_button = None
        except Exception:
            continue

    # 3b – If not found, open "More" dropdown and look there
    if connect_button is None:
        logger.info("Direct Connect button not found — trying More dropdown")
        for more_sel in MORE_BUTTON_SELECTORS:
            try:
                more_btn = await page.query_selector(more_sel)
                if more_btn and await more_btn.is_visible():
                    logger.info("Found More button via %s — clicking", more_sel)
                    await human_delay.random_delay(0.3, 0.8)
                    await more_btn.click()
                    # Wait for the dropdown to fully render
                    await human_delay.random_delay(1.0, 2.0)
                    try:
                        await page.wait_for_selector(
                            'div.artdeco-dropdown__content-inner, '
                            'div.artdeco-dropdown__content, '
                            'ul[role="menu"]',
                            timeout=5000,
                        )
                    except Exception:
                        logger.debug("Dropdown container selector timed out — trying selectors anyway")

                    # Now find Connect in the dropdown
                    for conn_sel in MORE_CONNECT_SELECTORS:
                        try:
                            connect_button = await page.query_selector(conn_sel)
                            if connect_button and await connect_button.is_visible():
                                logger.info("Connect found in More menu via %s", conn_sel)
                                break
                            connect_button = None
                        except Exception:
                            continue

                    if connect_button:
                        break
                    else:
                        # Close the dropdown before trying the next More selector
                        await page.keyboard.press("Escape")
                        await human_delay.random_delay(0.3, 0.5)
            except Exception:
                continue

    # 3c – Maybe already connected or pending
    if connect_button is None:
        pending = await page.query_selector(
            'button:has-text("Pending"), button:has-text("Message")'
        )
        if pending:
            btn_text = await pending.inner_text()
            result["success"] = True
            result["skipped"] = True
            logger.info("Already connected/pending (%s) for %s", btn_text.strip(), profile_url)
            return result

        result["error"] = "Connect button not found on profile (neither direct nor under More)"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 4. Click Connect
    # ------------------------------------------------------------------
    try:
        await human_delay.random_mouse_move(page)
        await human_delay.random_delay(0.5, 1.5)
        await connect_button.click()
        await human_delay.random_delay(1.5, 2.5)
    except Exception as exc:
        result["error"] = f"Failed to click Connect button: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 4b. Handle "How do you know …" dialog (if it appears)
    # ------------------------------------------------------------------
    for hdk_sel in HOW_DO_YOU_KNOW_SELECTORS:
        try:
            option = await page.query_selector(hdk_sel)
            if option and await option.is_visible():
                logger.info("'How do you know' dialog detected — selecting option")
                await human_delay.random_delay(0.5, 1.0)
                await option.click()
                await human_delay.random_delay(1.0, 1.5)
                # After selecting, look for a "Connect" or "Next" button
                proceed = await page.query_selector(
                    'button:has-text("Connect"), button:has-text("Next")'
                )
                if proceed and await proceed.is_visible():
                    await proceed.click()
                    await human_delay.random_delay(1.0, 2.0)
                break
        except Exception:
            continue

    # ------------------------------------------------------------------
    # 5. Wait for the invite dialog to appear
    # ------------------------------------------------------------------
    try:
        await page.wait_for_selector(
            'div.artdeco-modal, div.send-invite, div[role="dialog"]',
            timeout=5000,
        )
        await human_delay.random_delay(0.5, 1.0)
    except Exception:
        logger.debug("Modal selector timed out — continuing to look for note/send buttons")

    # ------------------------------------------------------------------
    # 6. Add a note if provided
    # ------------------------------------------------------------------
    note_added = False
    if note_text:
        # Click "Add a note" button (might not exist if textarea is already visible)
        add_note_btn = None
        for selector in ADD_NOTE_BUTTON_SELECTORS:
            try:
                add_note_btn = await page.query_selector(selector)
                if add_note_btn and await add_note_btn.is_visible():
                    break
                add_note_btn = None
            except Exception:
                continue

        if add_note_btn:
            try:
                await human_delay.random_delay(0.3, 0.8)
                await add_note_btn.click()
                await human_delay.random_delay(1.0, 1.5)
                logger.info("Clicked 'Add a note' button")
            except Exception as exc:
                logger.warning("Could not click 'Add a note': %s", exc)

        # Type the note
        note_input = None
        for selector in NOTE_INPUT_SELECTORS:
            try:
                note_input = await page.query_selector(selector)
                if note_input and await note_input.is_visible():
                    break
                note_input = None
            except Exception:
                continue

        if note_input:
            try:
                await note_input.click()
                await human_delay.random_delay(0.3, 0.5)
                # Type character by character for human-like behavior
                for char in note_text:
                    await page.keyboard.type(char)
                    await human_delay.random_delay(0.06, 0.12)
                await human_delay.random_delay(0.5, 1.0)
                note_added = True
                logger.info("Typed connection note (%d chars)", len(note_text))
            except Exception as exc:
                logger.warning("Failed to type note: %s", exc)
        else:
            logger.warning("Note input not found — will try sending without note")

    # ------------------------------------------------------------------
    # 7. Click Send
    # ------------------------------------------------------------------
    send_button = None

    # Try standard Send button selectors
    for selector in SEND_BUTTON_SELECTORS:
        try:
            send_button = await page.query_selector(selector)
            if send_button and await send_button.is_visible():
                break
            send_button = None
        except Exception:
            continue

    # If no Send button, try "Send without a note" as fallback
    if send_button is None and not note_added:
        for selector in SEND_WITHOUT_NOTE_SELECTORS:
            try:
                send_button = await page.query_selector(selector)
                if send_button and await send_button.is_visible():
                    logger.info("Using 'Send without a note' button")
                    break
                send_button = None
            except Exception:
                continue

    if send_button is None:
        # Last resort: look for any visible button with Send text inside modal
        try:
            buttons = await page.query_selector_all(
                'div.artdeco-modal button, div[role="dialog"] button'
            )
            for btn in buttons:
                txt = (await btn.inner_text()).strip().lower()
                if "send" in txt and await btn.is_visible():
                    send_button = btn
                    logger.info("Found Send button via modal scan: '%s'", txt)
                    break
        except Exception:
            pass

    if send_button is None:
        result["error"] = "Send button not found in connection dialog"
        logger.error(result["error"])
        return result

    try:
        await human_delay.random_delay(0.3, 0.8)
        await send_button.click()
        await human_delay.random_delay(2, 4)
        logger.info("Connection request sent to %s%s", profile_url,
                     " with note" if note_added else "")
    except Exception as exc:
        result["error"] = f"Failed to click Send: {exc}"
        logger.error(result["error"])
        return result

    result["success"] = True
    return result
