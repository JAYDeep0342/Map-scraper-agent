"""E-Commerce agent for extracting product and seller data."""

import asyncio
import logging
import urllib.parse
from playwright.async_api import Page
from browser.browser_manager import BrowserManager
from agents.seller_enricher import SellerEnricher

logger = logging.getLogger("ecommerce-agent")

# Sites where Cloudflare session state is per-page (localStorage/sessionStorage).
# Opening new tabs loses the challenge token → Access Denied.
# For these, we navigate the same page sequentially instead of opening new tabs.
IN_PAGE_NAV_SITES = ('meesho.com',)

BOT_KEYWORDS = ('access denied', 'are you human', 'captcha',
                'security check', 'robot check', 'forbidden')


class EcommerceAgent:
    """Agent to scrape generic e-commerce product pages (Flipkart, Amazon, Meesho, etc)."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def scrape(self, url: str, limit: int = 1, enrich_sellers: bool = False) -> list[dict]:
        context = await self.browser_manager.new_context()
        try:
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                logger.warning(f"[SEARCH-GOTO] timeout/error (continuing): {e}")

            await page.wait_for_timeout(3000)

            product_links: list[str] = []
            if limit > 1:
                hrefs = await page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.href)"
                )
                seen: set[str] = set()
                for h in hrefs:
                    if "/p/" in h or "/dp/" in h or "/product/" in h:
                        h_clean = h.split('?')[0]
                        if h_clean not in seen:
                            seen.add(h_clean)
                            product_links.append(h)

                product_links = product_links[:limit]
                logger.info(f"[LINKS] Found {len(product_links)} product links on search page")

            if not product_links:
                product_links = [url]

            domain = urllib.parse.urlparse(url).netloc.lower()
            is_meesho = 'meesho.com' in domain

            if is_meesho:
                # Extract directly from search page cards — product pages are Cloudflare-protected
                results = await self._scrape_meesho_cards(page, limit)
            else:
                # Open new tabs (Flipkart, Amazon — faster, no Cloudflare issues)
                await page.close()
                results = await self._scrape_new_tabs(context, product_links)

            if enrich_sellers and results:
                logger.info(f"[ENRICH] Finding seller contacts for {len(results)} products...")
                enricher = SellerEnricher(context)
                await enricher.enrich_batch(results)
                logger.info("[ENRICH] Done")

            return results

        finally:
            await context.close()

    # ------------------------------------------------------------------
    # Meesho: extract cards directly from search page (no product page visit)
    # ------------------------------------------------------------------

    async def _scrape_meesho_cards(self, page: Page, limit: int) -> list[dict]:
        # Scroll to load more cards if needed
        for _ in range(max(1, limit // 12)):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

        cards = await page.evaluate(r"""
            (limit) => {
                const clean = t => t ? t.replace(/\n/g, ' ').trim() : '';
                let results = [];
                let seen = new Set();

                for (let card of document.querySelectorAll('a[href*="/p/"]')) {
                    if (results.length >= limit) break;
                    let href = card.href.split('?')[0];
                    if (seen.has(href)) continue;
                    seen.add(href);

                    let img = card.querySelector('img');
                    let fullText = (card.textContent || '').trim();

                    // Price: first ₹XXX pattern
                    let priceMatch = fullText.match(/[₹]\s?[\d,]+/);
                    let price = priceMatch ? priceMatch[0].trim() : '';

                    // Name: text before the price
                    let name = '';
                    let pricePart = price ? fullText.indexOf(price) : -1;
                    if (pricePart > 0) {
                        name = fullText.substring(0, pricePart)
                            .replace(/^\+\d+\s*More/i, '')
                            .trim();
                    }
                    if (!name) {
                        // fallback: p or span inside card
                        let el = card.querySelector('p, h4, h3');
                        if (el) name = clean(el.innerText);
                    }

                    // Rating and reviews (rating is always 1.0-5.0)
                    let ratingMatch = fullText.match(/([1-5]\.\d{1,2})\d*\s*(\d+)\s*Review/i);
                    let rating = ratingMatch ? ratingMatch[1] : '';
                    let reviews = ratingMatch ? ratingMatch[2] : '';

                    if (!name && !price) continue;

                    results.push({
                        url: card.href,
                        product_name: name.substring(0, 150),
                        price: price,
                        image: img ? img.src : '',
                        description: rating ? `Rating: ${rating} | Reviews: ${reviews}` : '',
                        seller_name: '',
                        seller_phone: '',
                    });
                }
                return results;
            }
        """, limit)

        logger.info(f"[DONE] {len(cards)}/{limit} Meesho cards extracted from search page")
        return cards

    # ------------------------------------------------------------------
    # Strategy A: same-page sequential navigation (fallback)
    # ------------------------------------------------------------------

    async def _scrape_in_page(self, page: Page, links: list[str]) -> list[dict]:
        results: list[dict] = []
        for link in links:
            try:
                try:
                    await page.goto(link, wait_until="domcontentloaded", timeout=25000)
                except Exception as e:
                    logger.warning(f"[GOTO] {link[:60]}: {e}")

                await page.wait_for_timeout(3000)

                page_title = (await page.title()).lower()
                if any(kw in page_title for kw in BOT_KEYWORDS):
                    logger.warning(f"[BOT] Blocked: {await page.title()} — {link[:60]}")
                    continue

                result = await self._extract_product(page, link)
                logger.info(f"[OK] {link[:60]} → title={result.get('product_name','')[:40]!r} price={result.get('price','')!r}")
                results.append(result)

            except Exception as e:
                logger.error(f"[FAIL] {link[:60]}: {type(e).__name__}: {e}")

        logger.info(f"[DONE] {len(results)}/{len(links)} products extracted (in-page)")
        return results

    # ------------------------------------------------------------------
    # Strategy B: new tab per product (Flipkart, Amazon — faster)
    # ------------------------------------------------------------------

    async def _scrape_new_tabs(self, context, links: list[str]) -> list[dict]:
        semaphore = asyncio.Semaphore(3)

        async def worker(link: str):
            async with semaphore:
                p = await context.new_page()
                try:
                    try:
                        await p.goto(link, wait_until="domcontentloaded", timeout=25000)
                    except Exception as e:
                        logger.warning(f"[GOTO] {link[:60]}: {e}")

                    await p.wait_for_timeout(3000)

                    page_title = (await p.title()).lower()
                    if any(kw in page_title for kw in BOT_KEYWORDS):
                        logger.warning(f"[BOT] Blocked: {await p.title()} — {link[:60]}")
                        return None

                    result = await self._extract_product(p, link)
                    logger.info(f"[OK] {link[:60]} → title={result.get('product_name','')[:40]!r} price={result.get('price','')!r}")
                    return result
                except Exception as e:
                    logger.error(f"[FAIL] {link[:60]}: {type(e).__name__}: {e}")
                    return None
                finally:
                    if not p.is_closed():
                        await p.close()

        tasks = [asyncio.create_task(worker(l)) for l in links]
        gathered = await asyncio.gather(*tasks)
        results = [g for g in gathered if g is not None]
        logger.info(f"[DONE] {len(results)}/{len(links)} products extracted (new-tabs)")
        return results

    # ------------------------------------------------------------------
    # Data extraction (shared)
    # ------------------------------------------------------------------

    async def _extract_product(self, page: Page, url: str) -> dict:
        data = await page.evaluate(r"""
            () => {
                let result = {
                    title: "",
                    price: "",
                    seller: "",
                    seller_phone: "",
                    image: "",
                    description: ""
                };

                const clean = t => t ? t.replace(/\n/g, ' ').trim() : '';

                // 1. TITLE
                const h1 = document.querySelector('h1');
                if (h1) result.title = clean(h1.innerText);
                if (!result.title) result.title = document.title;

                // 2. PRICE
                let priceEls = Array.from(document.querySelectorAll('div, span, p')).filter(e => {
                    let t = (e.innerText || '').trim();
                    return /^[₹$]\s?[\d,]+(\.\d+)?$/.test(t) ||
                           /^[\d,]+(\.\d+)?\s?[₹$]$/.test(t);
                });

                if (priceEls.length > 0) {
                    priceEls.sort((a, b) => {
                        let fsA = parseFloat(window.getComputedStyle(a).fontSize) || 0;
                        let fsB = parseFloat(window.getComputedStyle(b).fontSize) || 0;
                        return fsB - fsA;
                    });
                    result.price = clean(priceEls[0].innerText);
                } else {
                    let amzPrice = document.querySelector('.a-price-whole, #priceblock_ourprice, #priceblock_dealprice');
                    if (amzPrice) result.price = clean(amzPrice.innerText);
                }

                if (!result.price) {
                    let allText = document.body.innerText || '';
                    let match = allText.match(/[₹$]\s?[\d,]+(\.\d+)?/);
                    if (match) result.price = match[0].trim();
                }

                // 3. SELLER
                let amzSeller = document.querySelector('#sellerProfileTriggerId, #merchant-info a, a#bylineInfo');
                if (amzSeller) {
                    result.seller = clean(amzSeller.innerText);
                } else {
                    let otherSellersLink = document.querySelector('a[href*="/sellers?pid"]');
                    if (otherSellersLink) {
                        let container = otherSellersLink;
                        for (let i = 0; i < 6; i++) container = container.parentElement || container;
                        let sectionText = (container.textContent || '').trim();
                        let m = sectionText.match(/(?:Fulfilled by|Sold by|Sold By)\s+([^0-9\n]{2,60}?)(?=\d|See other)/);
                        if (m) result.seller = m[1].trim();
                    }
                }

                // 4. IMAGE
                let ogImage = document.querySelector('meta[property="og:image"]');
                if (ogImage) result.image = ogImage.getAttribute('content');
                if (!result.image) {
                    let img = document.querySelector('img[src*="rukminim"], img[src*="meesho"], img#landingImage, img.a-dynamic-image');
                    if (img) result.image = img.src;
                }

                // 5. DESCRIPTION — JSON-LD first (most reliable)
                try {
                    let jsonLd = document.querySelector('script[type="application/ld+json"]');
                    if (jsonLd) {
                        let ld = JSON.parse(jsonLd.textContent);
                        if (Array.isArray(ld)) ld = ld[0];
                        if (ld && ld.description) result.description = clean(ld.description);
                        else if (ld && Array.isArray(ld['@graph'])) {
                            let prod = ld['@graph'].find(x => x['@type'] === 'Product');
                            if (prod && prod.description) result.description = clean(prod.description);
                        }
                    }
                } catch(e) {}

                if (!result.description) {
                    let ogDesc = document.querySelector('meta[property="og:description"], meta[name="description"]');
                    if (ogDesc) result.description = ogDesc.getAttribute('content');
                }

                if (!result.description) {
                    let bullets = Array.from(document.querySelectorAll('ul li')).filter(li => {
                        let ancestor = li;
                        while (ancestor) {
                            let tag = (ancestor.tagName || '').toLowerCase();
                            if (tag === 'nav' || tag === 'header' || tag === 'footer') return false;
                            ancestor = ancestor.parentElement;
                        }
                        let t = (li.innerText || '').trim();
                        return t.length > 8 && t.length < 200 &&
                               !/profile|seller|gift card|notification|wish|cart|login|sign/i.test(t);
                    }).slice(0, 5).map(li => (li.innerText || '').trim());
                    if (bullets.length > 0) result.description = bullets.join(' | ');
                }

                return result;
            }
        """)

        return {
            "url": url,
            "product_name": data["title"],
            "price": data["price"],
            "image": data["image"],
            "description": data["description"],
            "seller_name": data["seller"],
            "seller_phone": data["seller_phone"],
        }
