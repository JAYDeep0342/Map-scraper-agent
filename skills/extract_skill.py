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
            # Step 1: Visit the homepage
            try:
                resp = await page.goto(
                    website, wait_until="domcontentloaded",
                    timeout=settings.ENRICH_TIMEOUT_MS,
                )
                if resp is not None and resp.status < 400:
                    html = await page.content()
                    self._harvest(html, emails, socials)
            except Exception:
                pass

            # Step 2: If we didn't find any email, search for actual contact/about links on the homepage
            if not emails:
                try:
                    anchors = await page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => ({href: e.href, text: e.innerText.toLowerCase()}))"
                    )
                    
                    # Look for contact/about links on the same domain
                    target_urls = []
                    seen_urls = set()
                    for anchor in anchors:
                        href = anchor["href"]
                        text = anchor["text"]
                        
                        href_parsed = urllib.parse.urlparse(href)
                        href_host = href_parsed.netloc.lower().removeprefix("www.")
                        
                        if href_host == host or not href_host:
                            full_url = urllib.parse.urljoin(website, href)
                            normalized_url = full_url.split("?")[0].rstrip("/")
                            
                            if normalized_url not in seen_urls:
                                if any(kw in normalized_url.lower() or kw in text for kw in ("contact", "about", "info", "support", "address")):
                                    seen_urls.add(normalized_url)
                                    target_urls.append(full_url)
                    
                    # Visit up to 2 discovered URLs to harvest emails
                    for contact_url in target_urls[:2]:
                        try:
                            resp = await page.goto(
                                contact_url, wait_until="domcontentloaded",
                                timeout=settings.ENRICH_TIMEOUT_MS,
                            )
                            if resp is not None and resp.status < 400:
                                html = await page.content()
                                self._harvest(html, emails, socials)
                                if emails:
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass
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
