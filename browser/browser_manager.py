# browser_manager.py
"""Manages Playwright browser instances for agents."""

import asyncio

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

from config import settings

# Resource types that carry no data we extract (Playbook Step 1).
_BLOCKED_RESOURCE_TYPES = {"image", "font", "media", "stylesheet"}
_BLOCKED_URL_FRAGMENTS = (
    "google-analytics", "doubleclick", "facebook.com/tr", "gtag", "googletagmanager",
)


async def _block_resources(route) -> None:
    request = route.request
    if request.resource_type in _BLOCKED_RESOURCE_TYPES:
        await route.abort()
    elif any(fragment in request.url for fragment in _BLOCKED_URL_FRAGMENTS):
        await route.abort()
    else:
        await route.continue_()


_BENIGN_CANCELLATION_MARKERS = (
    "ERR_ABORTED",
    "Target page, context or browser has been closed",
    # A goto() that timed out client-side doesn't necessarily abort the
    # browser-side navigation — a same-page retry's goto() then races it,
    # and the first goto's future rejects with this once nothing is
    # awaiting it anymore (maps_agent._scrape_detail_with_retry_on_page).
    "is interrupted by another navigation",
)


def _quiet_cancelled_navigation_errors(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Silence the benign asyncio noise from cancelling an in-flight page
    navigation (closing/cancelling a detail tab the instant enough leads are
    found, or a phase-timeout ceiling firing, races with its own goto() /
    wait_for_selector()). Playwright's internal navigation future ends up
    holding that error (net::ERR_ABORTED, or TargetClosedError once the
    context is closed) with nothing left to await it, so asyncio logs
    "Future exception was never retrieved" — cosmetic only, it doesn't
    affect any returned lead. Anything else still goes through to the
    default handler so real bugs stay visible.
    """
    exc = context.get("exception")
    if isinstance(exc, Exception) and any(marker in str(exc) for marker in _BENIGN_CANCELLATION_MARKERS):
        return
    loop.default_exception_handler(context)


class BrowserManager:
    """Launches and manages a shared Chromium browser and its contexts."""

    def __init__(self, headless: bool | None = None):
        self.headless = settings.HEADLESS if headless is None else headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> Browser:
        """Return a live browser, launching it (or relaunching after a
        crash) at most once even under concurrent callers.

        Playwright's driver process (``async_playwright().start()``) and
        Chromium (``chromium.launch()``) together cost ~2.5-3.5s measured
        on this host — paid once here instead of on every scrape job/batch
        when this manager is kept alive for the server's lifetime (see
        api/server.py lifespan). ``is_connected()`` detects a crashed
        browser so a stale handle isn't handed back to callers.
        """
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                self._browser = None
                if self._playwright is None:
                    asyncio.get_running_loop().set_exception_handler(_quiet_cancelled_navigation_errors)
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
        await context.route("**/*", _block_resources)
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
