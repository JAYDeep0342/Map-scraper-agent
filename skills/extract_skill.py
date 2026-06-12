"""Extract skill using Playwright to extract data from pages.

Enriches leads by visiting each business website and pulling out
email addresses and social-media profile links.
"""

import asyncio
import re
import urllib.parse

from playwright.async_api import BrowserContext

from config import settings

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Junk emails that show up in page source (asset filenames, placeholders).
EMAIL_JUNK = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", "example.com",
              "sentry.io", "wixpress.com", "yourdomain", "domain.com")

SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com",
                  "x.com", "youtube.com", "tiktok.com", "wa.me", "whatsapp.com")


class ExtractSkill:
    """Visits business websites and extracts emails + social links."""

    def __init__(self, context: BrowserContext):
        self.context = context

    async def enrich_leads(self, leads: list[dict], progress=None) -> None:
        """Fill ``emails`` and ``social_links`` for every lead with a website."""
        semaphore = asyncio.Semaphore(settings.ENRICH_CONCURRENCY)
        targets = [l for l in leads if l.get("website")]
        done = 0

        async def worker(lead: dict) -> None:
            nonlocal done
            async with semaphore:
                emails, socials = await self._extract_from_site(lead["website"])
                lead["emails"] = ", ".join(emails)
                lead["social_links"] = ", ".join(socials)
                done += 1
                if progress:
                    found = f"{len(emails)} email(s)" if emails else "no email"
                    progress(f"  [{done}/{len(targets)}] {lead['name']}: {found}")

        await asyncio.gather(*(worker(l) for l in targets), return_exceptions=True)

    async def _extract_from_site(self, website: str) -> tuple[list[str], list[str]]:
        # Many small businesses list a WhatsApp/Instagram link as their
        # "website" - crawling those only yields widget junk. Record the
        # link itself as the social profile and skip.
        host = urllib.parse.urlparse(website).netloc.lower().removeprefix("www.")
        if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
            return [], [website.split("?")[0].rstrip("/")]

        emails: dict[str, None] = {}
        socials: dict[str, None] = {}

        page = await self.context.new_page()
        try:
            urls = [website]
            base = website.rstrip("/")
            urls += [f"{base}/{path}" for path in settings.CONTACT_PATHS]

            for i, url in enumerate(urls):
                try:
                    resp = await page.goto(
                        url, wait_until="domcontentloaded",
                        timeout=settings.ENRICH_TIMEOUT_MS,
                    )
                    if resp is None or resp.status >= 400:
                        continue
                    html = await page.content()
                    self._harvest(html, emails, socials)
                except Exception:
                    continue
                # Homepage is always scanned; contact pages only until
                # we have found at least one email.
                if i >= 0 and emails:
                    break
        finally:
            await page.close()

        return list(emails)[:5], list(socials)[:5]

    def _harvest(self, html: str, emails: dict, socials: dict) -> None:
        html = urllib.parse.unquote(html.replace("&#64;", "@").replace("%40", "@"))
        for email in EMAIL_RE.findall(html):
            lowered = email.lower()
            if not any(junk in lowered for junk in EMAIL_JUNK):
                emails.setdefault(lowered)
        for match in re.findall(r'https?://[^\s"\'<>]+', html):
            host = urllib.parse.urlparse(match).netloc.lower().removeprefix("www.")
            if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
                clean = match.split("?")[0].rstrip("/")
                # Skip share/intent/tracking widget links, keep profile URLs.
                if any(x in clean for x in ("/sharer", "/share", "/intent", "/plugins")):
                    continue
                if clean.endswith("facebook.com/tr"):  # FB tracking pixel
                    continue
                socials.setdefault(clean)
