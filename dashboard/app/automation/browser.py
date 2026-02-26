"""
Playwright browser manager for LinkedPilot v2.

Each sender gets its own persistent browser context so LinkedIn sessions
are completely independent (cookies, local-storage, etc.).
"""

import logging
from pathlib import Path
from typing import Optional

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
        self._contexts: dict[int, BrowserContext] = {}
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
        self, sender_id: int, profile_dir: str
    ) -> BrowserContext:
        """Return (or create) a persistent browser context for *sender_id*."""
        if sender_id in self._contexts:
            return self._contexts[sender_id]

        if self._playwright is None:
            await self.initialize()

        # Ensure the profile directory exists
        Path(profile_dir).mkdir(parents=True, exist_ok=True)

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

    async def get_page(self, sender_id: int) -> Page:
        """Return a usable page from the sender's context."""
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

    def is_open(self, sender_id: int) -> bool:
        """Check if a browser context is open for this sender."""
        return sender_id in self._contexts

    async def open_for_login(self, sender_id: int, profile_dir: str) -> None:
        """Open browser and navigate to LinkedIn login page.

        The browser stays open for the user to manually log in.
        """
        context = await self.get_context(sender_id, profile_dir)
        page = await self.get_page(sender_id)
        try:
            await page.goto("https://www.linkedin.com/login", timeout=30000)
        except Exception:
            # If login page fails, try the homepage
            await page.goto("https://www.linkedin.com/", timeout=30000)
        logger.info("Opened LinkedIn login for sender %s", sender_id)

    async def check_login_status(self, sender_id: int) -> dict:
        """Check if the sender is logged into LinkedIn.

        Returns dict with 'logged_in' (bool) and 'name' (str if logged in).
        """
        if sender_id not in self._contexts:
            return {"logged_in": False, "error": "Browser not open"}

        try:
            page = await self.get_page(sender_id)
            current_url = page.url

            # If we're on the feed or any non-login page, we're logged in
            if "feed" in current_url or "mynetwork" in current_url or "messaging" in current_url:
                # Try to get the user's name
                try:
                    name_el = await page.query_selector(".feed-identity-module__actor-meta a")
                    name = await name_el.inner_text() if name_el else ""
                except Exception:
                    name = ""
                return {"logged_in": True, "name": name}

            # Navigate to feed to check
            await page.goto("https://www.linkedin.com/feed/", timeout=15000)
            await page.wait_for_timeout(2000)

            final_url = page.url
            if "login" in final_url or "checkpoint" in final_url:
                return {"logged_in": False, "error": "Not logged in"}

            return {"logged_in": True, "name": ""}

        except Exception as e:
            return {"logged_in": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def close_context(self, sender_id: int) -> None:
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
