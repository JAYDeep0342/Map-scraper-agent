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
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--disable-web-security",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--window-size=1366,850",
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
            extra_http_headers={
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Upgrade-Insecure-Requests": "1",
            },
        )
        context.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
        context.set_default_timeout(settings.NAV_TIMEOUT_MS)
        # Comprehensive anti-detection init script
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-IN', 'en', 'hi']});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'permissions', {
                get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
            });
        """)
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
