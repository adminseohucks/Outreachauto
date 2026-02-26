"""
LinkedIn Connection Request engine for LinkedPilot v2.

Navigates to a profile and sends a connection request with a
personalized note.
"""

import logging
from typing import Dict, Any

from playwright.async_api import Page

from . import human_delay

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selector strategies
# ---------------------------------------------------------------------------

# "Connect" button on profile page
CONNECT_BUTTON_SELECTORS = [
    'button[aria-label*="Invite"][aria-label*="to connect"]',
    'button.pvs-profile-actions__action[aria-label*="connect"]',
    'button[aria-label*="Connect"]',
    'div.pvs-profile-actions button:has-text("Connect")',
    'main button:has-text("Connect")',
]

# "More" button (Connect might be hidden under "More" dropdown)
MORE_BUTTON_SELECTORS = [
    'button[aria-label="More actions"]',
    'button.artdeco-dropdown__trigger[aria-label*="More"]',
    'div.pvs-profile-actions button[aria-label*="More"]',
]

# Connect option inside "More" dropdown
MORE_CONNECT_SELECTORS = [
    'div[role="menuitem"]:has-text("Connect")',
    'li[role="menuitem"] span:has-text("Connect")',
    'div.artdeco-dropdown__content button:has-text("Connect")',
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
    'textarea[placeholder*="add a personal note"]',
    'div.send-invite textarea',
    'textarea',
]

# "Send" button in the connect dialog
SEND_BUTTON_SELECTORS = [
    'button[aria-label="Send invitation"]',
    'button[aria-label="Send now"]',
    'button:has-text("Send")',
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
    # 3. Find the Connect button
    # ------------------------------------------------------------------
    connect_button = None

    # First try direct Connect button
    for selector in CONNECT_BUTTON_SELECTORS:
        try:
            connect_button = await page.query_selector(selector)
            if connect_button:
                # Make sure it's visible
                is_visible = await connect_button.is_visible()
                if is_visible:
                    logger.debug("Connect button found via %s", selector)
                    break
                connect_button = None
        except Exception:
            continue

    # If not found, try "More" dropdown
    if connect_button is None:
        for more_sel in MORE_BUTTON_SELECTORS:
            try:
                more_btn = await page.query_selector(more_sel)
                if more_btn and await more_btn.is_visible():
                    await human_delay.random_delay(0.3, 0.8)
                    await more_btn.click()
                    await human_delay.random_delay(0.5, 1.0)

                    # Now find Connect in the dropdown
                    for conn_sel in MORE_CONNECT_SELECTORS:
                        try:
                            connect_button = await page.query_selector(conn_sel)
                            if connect_button:
                                logger.debug("Connect found in More menu via %s", conn_sel)
                                break
                        except Exception:
                            continue
                    if connect_button:
                        break
            except Exception:
                continue

    if connect_button is None:
        # Maybe already connected or pending
        pending = await page.query_selector('button:has-text("Pending")')
        if pending:
            result["success"] = True
            result["skipped"] = True
            logger.info("Connection already pending for %s", profile_url)
            return result

        result["error"] = "Connect button not found on profile"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 4. Click Connect
    # ------------------------------------------------------------------
    try:
        await human_delay.random_mouse_move(page)
        await human_delay.random_delay(0.5, 1.5)
        await connect_button.click()
        await human_delay.random_delay(1, 2)
    except Exception as exc:
        result["error"] = f"Failed to click Connect button: {exc}"
        logger.error(result["error"])
        return result

    # ------------------------------------------------------------------
    # 5. Add a note if provided
    # ------------------------------------------------------------------
    if note_text:
        # Click "Add a note" button
        add_note_btn = None
        for selector in ADD_NOTE_BUTTON_SELECTORS:
            try:
                add_note_btn = await page.query_selector(selector)
                if add_note_btn:
                    break
            except Exception:
                continue

        if add_note_btn:
            try:
                await human_delay.random_delay(0.3, 0.8)
                await add_note_btn.click()
                await human_delay.random_delay(0.5, 1.0)
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
                    import random
                    await human_delay.random_delay(0.06, 0.12)
                await human_delay.random_delay(0.5, 1.0)
                logger.info("Typed connection note (%d chars)", len(note_text))
            except Exception as exc:
                logger.warning("Failed to type note: %s", exc)
        else:
            logger.warning("Note input not found — sending without note")

    # ------------------------------------------------------------------
    # 6. Click Send
    # ------------------------------------------------------------------
    send_button = None
    for selector in SEND_BUTTON_SELECTORS:
        try:
            send_button = await page.query_selector(selector)
            if send_button and await send_button.is_visible():
                break
            send_button = None
        except Exception:
            continue

    if send_button is None:
        result["error"] = "Send button not found in connection dialog"
        logger.error(result["error"])
        return result

    try:
        await human_delay.random_delay(0.3, 0.8)
        await send_button.click()
        await human_delay.random_delay(2, 4)
        logger.info("Connection request sent to %s%s", profile_url,
                     " with note" if note_text else "")
    except Exception as exc:
        result["error"] = f"Failed to click Send: {exc}"
        logger.error(result["error"])
        return result

    result["success"] = True
    return result
