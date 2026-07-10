"""Extract skill using plain HTTP to extract data from pages.

Enriches leads by visiting each business website and pulling out
email addresses and social-media profile links. Uses ``httpx`` instead
of a Playwright browser tab (Speed Optimization Playbook, Step 2) —
homepage/contact-page HTML is server-rendered for the vast majority of
small-business sites, so a full Chromium render is unneeded overhead.
"""

import asyncio
import re
import urllib.parse

import httpx

from config import settings

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
ANCHOR_RE = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")

# Junk emails that show up in page source (asset filenames, placeholders).
EMAIL_JUNK = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", "example.com",
              "sentry.io", "wixpress.com", "yourdomain", "domain.com")

SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com",
                  "x.com", "youtube.com", "tiktok.com", "wa.me", "whatsapp.com")


class ExtractSkill:
    """Visits business websites over HTTP and extracts emails + social links."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": settings.USER_AGENT},
            timeout=settings.ENRICH_TIMEOUT_MS / 1000,
            follow_redirects=True,
            verify=False,
        )

    async def enrich_leads(self, leads: list[dict], progress=None) -> None:
        """Fill ``emails`` for every lead with a website."""
        # Step 6: httpx requests are genuinely I/O-bound (no JS/render cost
        # per tab like Playwright), so this can use the full cpu_count*4
        # headroom rather than the conservative cap used for detail tabs.
        semaphore = asyncio.Semaphore(settings.get_optimal_concurrency(hard_cap=settings.ENRICH_CONCURRENCY))
        targets = [l for l in leads if l.get("website")]
        done = 0

        async def worker(lead: dict) -> None:
            nonlocal done
            async with semaphore:
                emails, _socials = await self._extract_from_site(lead["website"])
                lead["emails"] = ", ".join(emails)
                done += 1
                if progress:
                    found = f"{len(emails)} email(s)" if emails else "no email"
                    progress(f"  [{done}/{len(targets)}] {lead['name']}: {found}")

        try:
            await asyncio.gather(*(worker(l) for l in targets), return_exceptions=True)
        finally:
            if self._owns_client:
                await self.client.aclose()

    async def _extract_from_site(self, website: str) -> tuple[list[str], list[str]]:
        # Many small businesses list a WhatsApp/Instagram link as their
        # "website" - crawling those only yields widget junk. Record the
        # link itself as the social profile and skip.
        host = urllib.parse.urlparse(website).netloc.lower().removeprefix("www.")
        if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
            return [], [website.split("?")[0].rstrip("/")]

        emails: dict[str, None] = {}
        socials: dict[str, None] = {}

        # Step 1: fetch the homepage
        home_html = await self._fetch(website)
        if home_html:
            self._harvest(home_html, emails, socials)

        # Step 2: if we didn't find any email, find contact/about links on the homepage
        if not emails and home_html:
            for contact_url in self._discover_contact_links(home_html, website, host)[:2]:
                contact_html = await self._fetch(contact_url)
                if contact_html:
                    self._harvest(contact_html, emails, socials)
                    if emails:
                        break

        return list(emails)[:5], list(socials)[:5]

    async def _fetch(self, url: str) -> str:
        try:
            resp = await self.client.get(url)
            if resp.status_code < 400:
                return resp.text
        except Exception:
            pass
        return ""

    def _discover_contact_links(self, html: str, website: str, host: str) -> list[str]:
        """Find same-domain contact/about links referenced in the homepage HTML."""
        target_urls = []
        seen_urls = set()
        for href, inner_html in ANCHOR_RE.findall(html):
            text = TAG_RE.sub("", inner_html).lower()
            href_host = urllib.parse.urlparse(href).netloc.lower().removeprefix("www.")
            if href_host and href_host != host:
                continue  # off-site link

            full_url = urllib.parse.urljoin(website, href)
            normalized_url = full_url.split("?")[0].rstrip("/")
            if normalized_url in seen_urls:
                continue

            if any(kw in normalized_url.lower() or kw in text for kw in
                   ("contact", "about", "info", "support", "address")):
                seen_urls.add(normalized_url)
                target_urls.append(full_url)

        return target_urls

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
