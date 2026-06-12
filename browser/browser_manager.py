# browser_manager.py
"""Manages Playwright browser instances for agents."""

import asyncio

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

from config import settings


class BrowserManager:
    """Launches and manages a shared Chromium browser and its contexts."""

    def __init__(self, headless: bool | None = None):
        self.headless = settings.HEADLESS if headless is None else headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> Browser:
        async with self._lock:
            if self._browser is None:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--disable-infobars",
                        "--lang=en-US",
                    ],
                )
        return self._browser

    async def new_context(self) -> BrowserContext:
        """Create a fresh context with sane defaults and light stealth."""
        browser = await self.start()
        context = await browser.new_context(
            user_agent=settings.USER_AGENT,
            viewport=settings.VIEWPORT,
            locale=settings.LOCALE,
        )
        context.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
        context.set_default_timeout(settings.NAV_TIMEOUT_MS)
        # Hide the webdriver flag that headless Chromium exposes.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        return context

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
