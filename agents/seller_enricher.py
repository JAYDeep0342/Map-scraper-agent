"""Seller contact enrichment — finds phone/email for a seller by name via Google search."""

import asyncio
import re
import urllib.parse
import logging

from playwright.async_api import BrowserContext

logger = logging.getLogger("seller-enricher")

# Indian mobile: 10 digits starting with 6-9
PHONE_RE = re.compile(
    r'(?:\+91|91|0)?[\s\-.]?([6-9]\d{2})[\s\-.]?(\d{3})[\s\-.]?(\d{4})'
)

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Always-junk email patterns
EMAIL_JUNK = (
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
    'example.com', 'sentry.io', 'wixpress.com', 'yourdomain', 'domain.com',
    'flipkart.com', 'amazon.in', 'amazon.com', 'noreply', 'no-reply',
    'samaritans', 'privacy@', 'legal@', 'abuse@', 'postmaster@', 'webmaster@',
    'support@google', 'info@apple', 'donotreply', 'notifications@',
)

# Skip these domains entirely — not seller business pages
SKIP_DOMAINS = (
    'google.', 'flipkart.com', 'amazon.in', 'amazon.com',
    'youtube.com', 'facebook.com', 'twitter.com', 'x.com',
    'instagram.com', 'linkedin.com', 'tiktok.com', 'pinterest.com',
    'quora.com', 'reddit.com', 'medium.com', 'wikipedia.org',
    'stackoverflow.com', 'stackexchange.com',
    'meesho.com', 'snapdeal.com', 'myntra.com',
    'w3schools.com', 'geeksforgeeks.org',
    # Complaint / review sites — phones here belong to complainants, not sellers
    'consumercomplaint', 'mouthshut.com', 'complaintsboard.com',
    'consumercomplaints.in', 'grievances.', 'grahakvaani', 'pgportal.',
    'trustpilot.com', 'sitejabber.com', 'resellerratings.com',
)


class SellerEnricher:
    """Finds phone/email for a Flipkart seller via Google search."""

    def __init__(self, context: BrowserContext):
        self.context = context

    async def enrich(self, seller_name: str) -> dict:
        """Returns {'phone': ..., 'email': ..., 'website': ...}."""
        empty = {'phone': '', 'email': '', 'website': ''}
        if not seller_name or len(seller_name.strip()) < 3:
            return empty

        result_urls = await self._google_search(seller_name)
        logger.info(f"[ENRICH] '{seller_name}' → {len(result_urls)} candidate URLs")

        phones: list[str] = []
        emails: list[str] = []
        website = ''

        for url in result_urls[:4]:
            p, e = await self._extract_contact(url, seller_name)
            phones.extend(x for x in p if x not in phones)
            emails.extend(x for x in e if x not in emails)
            if not website and (p or e):
                website = url
            if phones and emails:
                break

        logger.info(f"[ENRICH] '{seller_name}' → phone={phones[0] if phones else '-'} email={emails[0] if emails else '-'}")
        return {
            'phone': phones[0] if phones else '',
            'email': emails[0] if emails else '',
            'website': website,
        }

    async def enrich_batch(self, products: list[dict]) -> None:
        """Enrich seller contact for a list of product dicts in-place (deduped by seller name)."""
        seller_cache: dict[str, dict] = {}
        seller_locks: dict[str, asyncio.Lock] = {}
        concurrency = asyncio.Semaphore(2)

        async def worker(product: dict) -> None:
            name = product.get('seller_name', '').strip()
            if not name:
                return
            # One lock per unique seller name — prevents duplicate searches
            if name not in seller_locks:
                seller_locks[name] = asyncio.Lock()
            async with seller_locks[name]:
                if name not in seller_cache:
                    async with concurrency:
                        seller_cache[name] = await self.enrich(name)
                contact = seller_cache[name]
            product['seller_phone'] = contact['phone']
            product['seller_email'] = contact['email']
            product['seller_website'] = contact['website']

        await asyncio.gather(*(worker(p) for p in products))

    # ------------------------------------------------------------------

    async def _google_search(self, seller_name: str) -> list[str]:
        """Try two queries — quoted exact first, then broader. Return up to 5 URLs."""
        queries = [
            f'"{seller_name}" contact phone email',
            f'{seller_name} seller shop contact India phone',
        ]
        seen: set[str] = set()
        urls: list[str] = []

        for query in queries:
            if len(urls) >= 5:
                break
            search_url = (
                f'https://www.google.com/search'
                f'?q={urllib.parse.quote(query)}&num=8&hl=en'
            )
            page = await self.context.new_page()
            try:
                try:
                    await page.goto(search_url, wait_until='domcontentloaded', timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(2000)
                hrefs = await page.eval_on_selector_all('a[href]', 'els => els.map(e => e.href)')
                for h in hrefs:
                    url = self._unwrap_google_url(h)
                    if not url or url in seen or self._should_skip(url):
                        continue
                    seen.add(url)
                    urls.append(url)
                    if len(urls) >= 5:
                        break
            except Exception as e:
                logger.warning(f"[ENRICH] Google search failed: {e}")
            finally:
                await page.close()

        return urls

    async def _extract_contact(self, url: str, seller_name: str) -> tuple[list[str], list[str]]:
        page = await self.context.new_page()
        try:
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=12000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            html = await page.content()
            phones = self._parse_phones(html)
            emails = self._parse_emails(html, seller_name)
            return phones, emails
        except Exception as e:
            logger.warning(f"[ENRICH] Failed {url[:60]}: {e}")
            return [], []
        finally:
            await page.close()

    def _parse_phones(self, html: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for m in PHONE_RE.finditer(html):
            digits = m.group(1) + m.group(2) + m.group(3)
            if digits not in seen:
                seen.add(digits)
                result.append(f'+91 {m.group(1)} {m.group(2)} {m.group(3)}')
        return result

    def _parse_emails(self, html: str, seller_name: str) -> list[str]:
        """Extract emails, preferring those from seller's own domain."""
        html = html.replace('&#64;', '@').replace('%40', '@')
        # Keywords from seller name to check domain relevance
        name_words = set(re.sub(r'[^a-z0-9]', ' ', seller_name.lower()).split())

        seen: set[str] = set()
        own_domain: list[str] = []
        generic: list[str] = []

        for email in EMAIL_RE.findall(html):
            e = email.lower()
            if any(j in e for j in EMAIL_JUNK) or e in seen:
                continue
            seen.add(e)
            domain = e.split('@')[-1].replace('.', ' ')
            if any(word in domain for word in name_words if len(word) > 2):
                own_domain.append(e)   # email from seller's own domain → highest priority
            else:
                generic.append(e)

        # Return own-domain emails first, then generic (limited to 3)
        return own_domain + generic[:3]

    def _should_skip(self, url: str) -> bool:
        domain = urllib.parse.urlparse(url).netloc.lower()
        return any(s in domain for s in SKIP_DOMAINS)

    def _unwrap_google_url(self, href: str) -> str:
        if not href or not href.startswith('http'):
            return ''
        if '/url?q=' in href or 'google.com/url' in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return qs.get('q', [''])[0]
        if 'google.' in urllib.parse.urlparse(href).netloc:
            return ''
        return href
