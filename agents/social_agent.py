"""Social media agent for extracting public profile data."""

import urllib.parse
from playwright.async_api import Page
from browser.browser_manager import BrowserManager

class SocialAgent:
    """Agent to extract public profile data from social media URLs."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def scrape(self, url: str) -> dict:
        context = await self.browser_manager.new_context()
        try:
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            
            await page.wait_for_timeout(2000)
            return await self._extract_profile(page, url)
        finally:
            await context.close()

    async def _extract_profile(self, page: Page, url: str) -> dict:
        async def attr(selectors: list, attribute: str) -> str:
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        val = await el.get_attribute(attribute)
                        if val and val.strip():
                            return val.strip()
                except Exception:
                    pass
            return ""

        # Most social platforms put critical info in meta tags for sharing
        title = await attr(['meta[property="og:title"]', 'meta[name="twitter:title"]'], "content")
        description = await attr(['meta[property="og:description"]', 'meta[name="description"]'], "content")
        image = await attr(['meta[property="og:image"]', 'meta[name="twitter:image"]'], "content")
        
        # Extract username from URL
        username = ""
        parsed = urllib.parse.urlparse(url)
        path_parts = [p for p in parsed.path.strip('/').split('/') if p]
        if path_parts:
            username = path_parts[0]

        return {
            "url": url,
            "platform": parsed.netloc.replace("www.", ""),
            "username": username,
            "name": title.split("(@")[0].split("|")[0].strip() if title else "",
            "bio_or_description": description,
            "profile_image": image,
        }
