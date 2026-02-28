"""
Playwright browser manager for LinkedPilot v2.

Each sender gets its own persistent browser context so LinkedIn sessions
are completely independent (cookies, local-storage, etc.).
"""

import asyncio
import logging
import os
import platform
import shutil
import subprocess
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
    "Chrome/145.0.0.0 Safari/537.36"
)

# JavaScript to remove automation fingerprints
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
"""


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
            ignore_default_args=[
                "--enable-automation",
                "--no-sandbox",
                "--disable-extensions",
            ],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )

        # Inject stealth script on every new page to hide automation fingerprints
        await context.add_init_script(STEALTH_JS)

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

    async def health_check(self, sender_id: int) -> bool:
        """Quick check if browser context is still alive and responsive."""
        ctx = self._contexts.get(sender_id)
        if ctx is None:
            return False
        try:
            pages = ctx.pages
            if pages:
                # Run a trivial JS eval — throws if browser process died
                await asyncio.wait_for(
                    pages[0].evaluate("() => true"),
                    timeout=5.0,
                )
            return True
        except Exception:
            self._contexts.pop(sender_id, None)
            logger.warning("Browser context for sender %s is dead — removed", sender_id)
            return False

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
        Fast path: checks current URL first before navigating.
        """
        if sender_id not in self._contexts:
            return {"logged_in": False, "error": "Browser not open"}

        try:
            page = await self.get_page(sender_id)
            current_url = page.url

            # Fast path: if already on a LinkedIn app page, we're logged in
            if any(x in current_url for x in (
                "/feed", "/mynetwork", "/messaging", "/search", "/in/",
                "/notifications", "/jobs",
            )):
                return {"logged_in": True, "name": ""}

            # If clearly on login/checkpoint, not logged in
            if "login" in current_url or "checkpoint" in current_url:
                return {"logged_in": False, "error": "Not logged in"}

            # Navigate to feed to check (reduced wait)
            await page.goto("https://www.linkedin.com/feed/", timeout=15000)
            await page.wait_for_timeout(1000)

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

    async def close_all_contexts(self) -> None:
        """Close every open context but keep Playwright running.

        Use this for the UI 'Close All Browsers' button.
        """
        for sender_id in list(self._contexts):
            await self.close_context(sender_id)

    async def close_all(self) -> None:
        """Close every open context and stop Playwright (app shutdown)."""
        await self.close_all_contexts()

        if self._playwright is not None:
            try:
                await self._playwright.stop()
                logger.info("Playwright stopped")
            except Exception as exc:
                logger.warning("Error stopping Playwright: %s", exc)
            finally:
                self._playwright = None


def _find_chrome() -> Optional[str]:
    """Find the Chrome executable on this system."""
    if platform.system() == "Windows":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
    # Linux / fallback
    return shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")


class ExtensionChromeManager:
    """Launch regular Chrome (not Playwright) with separate profiles for the extension.

    Each sender gets a profile at ``data/extension_profiles/sender_<id>``.
    No automation flags — it's a normal Chrome window where the user can
    install the commenting extension and log into LinkedIn.
    """

    def __init__(self) -> None:
        # sender_id → subprocess.Popen
        self._processes: dict[int, subprocess.Popen] = {}

    @staticmethod
    def profile_dir(sender_id: int) -> str:
        """Return the extension-profile directory path for a sender."""
        base = Path(__file__).resolve().parent.parent.parent / "data" / "extension_profiles"
        return str(base / f"sender_{sender_id}")

    def is_open(self, sender_id: int) -> bool:
        proc = self._processes.get(sender_id)
        if proc is None:
            return False
        # Check if process is still alive
        if proc.poll() is not None:
            self._processes.pop(sender_id, None)
            return False
        return True

    def open(self, sender_id: int) -> bool:
        """Open a regular Chrome window with a dedicated profile.

        Returns True if launched successfully, False otherwise.
        """
        if self.is_open(sender_id):
            return True  # already open

        chrome = _find_chrome()
        if chrome is None:
            logger.error("Chrome executable not found on this system")
            return False

        profile = self.profile_dir(sender_id)
        Path(profile).mkdir(parents=True, exist_ok=True)

        cmd = [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--enable-extensions",
            "https://www.linkedin.com/login",
        ]

        # Clean environment: remove Playwright/Chromium vars that could leak
        clean_env = {
            k: v for k, v in os.environ.items()
            if not any(x in k.upper() for x in ("PLAYWRIGHT", "CHROMIUM", "CHROME_FLAGS"))
        }

        try:
            proc = subprocess.Popen(cmd, env=clean_env)
            self._processes[sender_id] = proc
            logger.info(
                "Extension Chrome opened for sender %s (pid %s, profile %s)",
                sender_id, proc.pid, profile,
            )
            return True
        except Exception as exc:
            logger.error("Failed to launch Chrome for sender %s: %s", sender_id, exc)
            return False

    def close(self, sender_id: int) -> None:
        proc = self._processes.pop(sender_id, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            logger.info("Extension Chrome closed for sender %s", sender_id)

    def close_all(self) -> None:
        for sid in list(self._processes):
            self.close(sid)


# Module-level singletons
browser_manager = BrowserManager()
extension_chrome = ExtensionChromeManager()
