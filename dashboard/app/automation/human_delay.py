"""
Human-simulation delays for LinkedPilot v2.

Every interaction with LinkedIn should look as human as possible: random
pauses, character-by-character typing, gentle scrolling, and occasional
mouse movement.
"""

import asyncio
import random
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)


async def random_delay(min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
    """Sleep for a uniformly-random duration between *min_seconds* and *max_seconds*."""
    delay = random.uniform(min_seconds, max_seconds)
    logger.debug("Sleeping %.2f s", delay)
    await asyncio.sleep(delay)


async def type_like_human(page: Page, selector: str, text: str) -> None:
    """Type *text* into the element matching *selector* one character at a time.

    Each keystroke is followed by a random 80-150 ms pause so the typing
    cadence looks natural.
    """
    await page.click(selector)
    for char in text:
        await page.keyboard.type(char)
        delay_ms = random.uniform(80, 150)
        await asyncio.sleep(delay_ms / 1000.0)
    logger.debug("Typed %d characters into %s", len(text), selector)


async def human_scroll(page: Page) -> None:
    """Scroll the page down by a small random amount to mimic reading."""
    scroll_amount = random.randint(200, 600)
    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
    await random_delay(0.5, 1.5)
    logger.debug("Scrolled down %d px", scroll_amount)


async def random_mouse_move(page: Page) -> None:
    """Move the mouse to a random position inside the viewport."""
    x = random.randint(100, 1100)
    y = random.randint(100, 700)
    await page.mouse.move(x, y)
    await random_delay(0.2, 0.6)
    logger.debug("Mouse moved to (%d, %d)", x, y)


async def warm_up_delay() -> None:
    """Longer pause used at the very beginning of a session.

    Simulates the time a real user would spend glancing at the feed,
    reading notifications, etc. before performing any automated action.
    """
    delay = random.uniform(30, 60)
    logger.info("Warm-up delay: %.1f s", delay)
    await asyncio.sleep(delay)
