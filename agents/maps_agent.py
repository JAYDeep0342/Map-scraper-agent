"""Maps agent using Playwright for automation.

Scrapes Google Maps search results into structured lead records:
business name, category, rating, reviews, address, phone, website,
plus code and coordinates.
"""

import asyncio
import re
import urllib.parse

from playwright.async_api import BrowserContext, Page, TimeoutError as PWTimeout

from browser.browser_manager import BrowserManager
from config import settings

END_OF_LIST_MARKERS = ("you've reached the end of the list",)

COORDS_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
COORDS_AT_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    # Maps prefixes icon glyphs (private-use unicode) before some texts.
    return re.sub('[\\ue000-\\uf8ff]', '', text).strip().lstrip(', ')


class MapsAgent:
    """Agent to interact with Google Maps via Playwright."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def scrape(self, query: str, max_results: int = 50, progress=None) -> list[dict]:
        """Scrape up to ``max_results`` leads for a Maps search query."""
        context = await self.browser_manager.new_context()
        try:
            page = await context.new_page()
            url = settings.MAPS_SEARCH_URL.format(query=urllib.parse.quote(query))
            await page.goto(url, wait_until="domcontentloaded")
            await self._handle_consent(page)

            # Single-business queries redirect straight to the place page.
            if "/maps/place/" in page.url:
                lead = await self._extract_place(page, query)
                return [lead] if lead else []

            buffer_results = int(max_results * 1.5) + 15
            links = await self._collect_listing_links(page, buffer_results, progress)
            await page.close()

            if progress:
                progress(f"  Found {len(links)} listings, scraping details...")

            return await self._scrape_details(context, links, query, max_results, progress)
        finally:
            await context.close()

    # ------------------------------------------------------------------ #
    # Results feed
    # ------------------------------------------------------------------ #
    async def _handle_consent(self, page: Page) -> None:
        """Click through the Google consent screen if it appears."""
        try:
            if "consent.google" not in page.url:
                return
            for label in ("Accept all", "I agree", "Alle akzeptieren"):
                btn = page.get_by_role("button", name=label)
                if await btn.count():
                    await btn.first.click()
                    await page.wait_for_load_state("domcontentloaded")
                    return
        except Exception:
            pass

    async def _collect_listing_links(self, page: Page, max_results: int, progress=None) -> list[str]:
        """Scroll the results feed and collect unique /maps/place/ links."""
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=20_000)
        except PWTimeout:
            if progress:
                progress("  Error: Results feed element not found on the page.")
            return []

        links: dict[str, None] = {}  # ordered set
        stale_rounds = 0

        for scroll_round in range(settings.MAX_FEED_SCROLLS):
            hrefs = await page.eval_on_selector_all(
                'div[role="feed"] a[href*="/maps/place/"]',
                "els => els.map(e => e.href)",
            )
            before = len(links)
            for href in hrefs:
                links.setdefault(href.split("?")[0])
            
            if progress:
                progress(f"  Collected {len(links)} links so far...")

            if len(links) >= max_results:
                if progress:
                    progress(f"  Reached target limit: {len(links)} >= {max_results}")
                break

            feed_text = await page.locator('div[role="feed"]').inner_text()
            if any(m in feed_text.lower() for m in END_OF_LIST_MARKERS):
                if progress:
                    progress("  Reached end of search results list on Google Maps.")
                break

            stale_rounds = stale_rounds + 1 if len(links) == before else 0
            if stale_rounds >= 3:
                if progress:
                    progress(f"  Scrolling halted: no new results loaded after 3 attempts. Total links: {len(links)}")
                break

            # Scroll last element into view to force next page of listings to load
            try:
                await page.locator('div[role="feed"] a[href*="/maps/place/"]').last.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                await page.eval_on_selector(
                    'div[role="feed"]', "el => el.scrollBy(0, el.clientHeight * 2)"
                )
            await page.wait_for_timeout(settings.FEED_SCROLL_PAUSE_MS)

        return list(links)[:max_results]

    # ------------------------------------------------------------------ #
    # Detail pages
    # ------------------------------------------------------------------ #
    async def _scrape_details(
        self, context: BrowserContext, links: list[str], query: str, max_results: int, progress=None
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(settings.DETAIL_CONCURRENCY)
        unique_leads = []
        seen = set()
        lock = asyncio.Lock()
        stop_event = asyncio.Event()

        async def worker(link: str) -> None:
            if stop_event.is_set():
                return
            async with semaphore:
                if stop_event.is_set():
                    return
                detail_page = await context.new_page()
                try:
                    await detail_page.goto(link, wait_until="domcontentloaded", timeout=15000)
                    lead = await self._extract_place(detail_page, query)
                    if lead:
                        async with lock:
                            if stop_event.is_set():
                                return
                            key = (lead["name"], lead["address"])
                            if key not in seen:
                                seen.add(key)
                                unique_leads.append(lead)
                                if progress:
                                    progress(f"  [{len(unique_leads)}/{max_results}] {lead['name']}")
                                if len(unique_leads) >= max_results:
                                    stop_event.set()
                except Exception:
                    pass
                finally:
                    await detail_page.close()

        tasks = [asyncio.create_task(worker(l)) for l in links]
        await asyncio.gather(*tasks, return_exceptions=True)
        return unique_leads[:max_results]

    async def _extract_place(self, page: Page, query: str) -> dict | None:
        """Extract one business' details from an open place page."""
        try:
            await page.wait_for_selector("h1", timeout=15_000)
        except PWTimeout:
            return None
        await page.wait_for_timeout(200)

        async def text(selector: str) -> str:
            el = await page.query_selector(selector)
            if el is None:
                return ""
            return _clean(await el.inner_text())

        async def attr(selector: str, name: str) -> str:
            el = await page.query_selector(selector)
            if el is None:
                return ""
            return (await el.get_attribute(name)) or ""

        name = await text("h1.DUwDvf") or await text("h1")
        if not name:
            return None

        rating = await text('div.F7nice span[aria-hidden="true"]')
        reviews_raw = await attr('div.F7nice span[aria-label*="review"]', "aria-label")
        reviews = re.sub(r"[^\d]", "", reviews_raw)

        phone = ""
        phone_id = await attr('button[data-item-id^="phone:tel:"]', "data-item-id")
        if phone_id:
            phone = phone_id.removeprefix("phone:tel:")
        else:
            phone = await text('button[data-item-id^="phone"] div.Io6YTe')

        lat, lng = "", ""
        m = COORDS_RE.search(page.url) or COORDS_AT_RE.search(page.url)
        if m:
            lat, lng = m.group(1), m.group(2)

        return {
            "query": query,
            "name": name,
            "category": await text("button.DkEaL"),
            "rating": rating,
            "reviews": reviews,
            "address": await text('button[data-item-id="address"] div.Io6YTe'),
            "phone": phone,
            "website": await attr('a[data-item-id="authority"]', "href"),
            "plus_code": await text('button[data-item-id="oloc"] div.Io6YTe'),
            "latitude": lat,
            "longitude": lng,
            "maps_url": page.url.split("?")[0],
            "emails": "",
            "social_links": "",
        }
