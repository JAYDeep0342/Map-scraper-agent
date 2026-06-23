"""Web Search Agent using Playwright to scrape Google web search results.

When Google Maps runs out of results for a query, this agent searches the
regular Google web search for the same query and extracts any Google Maps
place links found in the results. Those links are then scraped by MapsAgent.
"""

import urllib.parse
import asyncio
import re

from playwright.async_api import Page, TimeoutError as PWTimeout

from browser.browser_manager import BrowserManager
from config import settings


MAPS_PLACE_RE = re.compile(
    r"https://www\.google\.com/maps/place/[^\s\"'<>]+"
)

GOOGLE_SEARCH_URL = "https://www.google.com/search?q={query}&hl=en&num=20"


class WebSearchAgent:
    """Searches Google web results to find Google Maps place links."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def find_maps_links(
        self,
        query: str,
        max_links: int = 30,
        progress=None,
    ) -> list[str]:
        """
        Search Google web for ``query`` and extract any /maps/place/ URLs found.

        Returns a deduplicated list of Google Maps place URLs (up to max_links).
        """
        context = await self.browser_manager.new_context()
        found: dict[str, None] = {}  # ordered set

        try:
            page = await context.new_page()
            # Scan multiple pages of Google search results (10 results per page)
            for page_num in range(0, min(max_links, 50), 10):
                if len(found) >= max_links:
                    break

                search_url = GOOGLE_SEARCH_URL.format(
                    query=urllib.parse.quote(query)
                ) + f"&start={page_num}"

                if progress:
                    progress(f"  [WebSearch] Scanning Google search page {page_num // 10 + 1}...")

                try:
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
                    await self._dismiss_consent(page)

                    # Method 1: Extract Maps links directly from anchor tags on the page
                    hrefs = await page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => e.href)"
                    )
                    for href in hrefs:
                        clean = href.split("?")[0].rstrip("/")
                        if "/maps/place/" in clean and clean not in found:
                            found.setdefault(clean)

                    # Method 2: Parse raw page HTML for embedded Maps URLs
                    html = await page.content()
                    for match in MAPS_PLACE_RE.findall(html):
                        clean = urllib.parse.unquote(match).split("?")[0].rstrip("/")
                        if "/maps/place/" in clean:
                            found.setdefault(clean)

                    if progress:
                        progress(f"  [WebSearch] Found {len(found)} Maps links so far...")

                    # If we found zero results on this page, stop paginating
                    if len(found) == 0 and page_num > 0:
                        break

                except Exception as exc:
                    if progress:
                        progress(f"  [WebSearch] Page {page_num // 10 + 1} failed: {exc}")
                    break

                await page.wait_for_timeout(500)

        finally:
            await context.close()

        links = list(found)[:max_links]
        if progress:
            progress(f"  [WebSearch] Total unique Maps links found: {len(links)}")
        return links

    async def _dismiss_consent(self, page: Page) -> None:
        """Click through Google consent screen if present."""
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
