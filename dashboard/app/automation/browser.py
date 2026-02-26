"""
Playwright browser manager for LinkedPilot v2.

Each sender gets its own persistent browser context so LinkedIn sessions
are completely independent (cookies, local-storage, etc.).
"""

import logging
from typing import Dict, Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
)

logger = logging.getLogger(__name__)

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


class BrowserManager:
    """Manages one persistent Chromium context per sender."""

    def __init__(self) -> None:
        self._contexts: Dict[str, BrowserContext] = {}
        self._playwright: Optional[Playwright] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Launch the Playwright driver (call once at app startup)."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            logger.info("Playwright initialised")

    async def get_context(
        self, sender_id: str, profile_dir: str
    ) -> BrowserContext:
        """Return (or create) a persistent browser context for *sender_id*.

        Parameters
        ----------
        sender_id:
            Unique identifier for the sender (e.g. ``"sender_1"``).
        profile_dir:
            Path to the Chrome user-data directory,
            e.g. ``"data/browser_profiles/sender_1"``.
        """
        if sender_id in self._contexts:
            return self._contexts[sender_id]

        if self._playwright is None:
            await self.initialize()

        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            user_agent=CHROME_USER_AGENT,
            args=["--disable-blink-features=AutomationControlled"],
        )

        self._contexts[sender_id] = context
        logger.info("Browser context created for sender %s", sender_id)
        return context

    async def get_page(self, sender_id: str) -> Page:
        """Return a usable page from the sender's context.

        If the context already has open pages the first one is returned;
        otherwise a new page is created.

        Raises ``KeyError`` if no context exists for *sender_id*.
        """
        context = self._contexts.get(sender_id)
        if context is None:
            raise KeyError(
                f"No browser context for sender '{sender_id}'. "
                "Call get_context() first."
            )

        pages = context.pages
        if pages:
            return pages[0]

        page = await context.new_page()
        return page

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def close_context(self, sender_id: str) -> None:
        """Close and remove the context for a single sender."""
        context = self._contexts.pop(sender_id, None)
        if context is not None:
            try:
                await context.close()
                logger.info("Browser context closed for sender %s", sender_id)
            except Exception as exc:
                logger.warning(
                    "Error closing context for sender %s: %s",
                    sender_id,
                    exc,
                )

    async def close_all(self) -> None:
        """Close every open context and stop Playwright."""
        for sender_id in list(self._contexts):
            await self.close_context(sender_id)

        if self._playwright is not None:
            try:
                await self._playwright.stop()
                logger.info("Playwright stopped")
            except Exception as exc:
                logger.warning("Error stopping Playwright: %s", exc)
            finally:
                self._playwright = None


# Module-level singleton
browser_manager = BrowserManager()
