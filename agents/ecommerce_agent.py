"""E-Commerce agent — Flipkart, Amazon, Meesho, and generic product scraper.

Improvements over v1:
  - Site-specific extractors (Flipkart / Amazon / Meesho / generic)
  - Retry logic on timeout (2 retries, 1 s backoff)
  - Faster waits — wait_for_load_state instead of fixed sleep
  - Concurrency bumped to 5 (was 3)
  - Pagination support (scrape multiple search-result pages)
  - Robust Flipkart seller extraction (updated for current layout)
  - Robust Amazon price / seller / image selectors
  - Better Meesho card parsing
"""

import asyncio
import logging
import re
import urllib.parse

from playwright.async_api import Page, BrowserContext
from browser.browser_manager import BrowserManager
from agents.seller_enricher import SellerEnricher

logger = logging.getLogger("ecommerce-agent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sites where every new tab loses Cloudflare session → scrape cards only
IN_PAGE_NAV_SITES = ("meesho.com",)

# Page-title keywords that signal bot/CAPTCHA block
BOT_KEYWORDS = (
    "access denied", "are you human", "captcha",
    "security check", "robot check", "forbidden",
    "verify you are", "ddos-guard",
)

# How many product-detail tabs to open in parallel
DETAIL_CONCURRENCY = 5

# Retries when a page load fails
MAX_RETRIES = 2
RETRY_DELAY_S = 1.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_site(url: str) -> str:
    """Return a short site key based on URL domain."""
    host = urllib.parse.urlparse(url).netloc.lower()
    if "flipkart.com" in host:
        return "flipkart"
    if "amazon.in" in host or "amazon.com" in host:
        return "amazon"
    if "meesho.com" in host:
        return "meesho"
    if "myntra.com" in host:
        return "myntra"
    if "snapdeal.com" in host:
        return "snapdeal"
    if "nykaa.com" in host:
        return "nykaa"
    if "zepto.com" in host:
        return "zepto"
    return "generic"


def _is_product_url(href: str, site: str) -> bool:
    """Return True if href looks like a product-detail page for the given site."""
    if not href or not href.startswith("http"):
        return False
    path = urllib.parse.urlparse(href).path.lower()
    if site == "flipkart":
        return "/p/" in path
    if site == "amazon":
        return "/dp/" in path or "/gp/product/" in path
    if site == "meesho":
        return "/p/" in path
    if site == "myntra":
        return re.search(r"/\d+/buy$", path) is not None
    if site == "snapdeal":
        return "/product/" in path
    if site == "nykaa":
        return "/p/" in path or "/buy/" in path
    if site == "zepto":
        return "/pvid/" in path or "/pn/" in path
    # generic fallback
    return any(seg in path for seg in ("/p/", "/dp/", "/product/", "/item/", "/buy/"))


async def _safe_goto(page: Page, url: str, timeout: int = 20_000) -> bool:
    """Navigate to url with retry logic. Returns True on success."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return True
        except Exception as exc:
            if attempt < MAX_RETRIES:
                logger.warning(f"[GOTO] attempt {attempt+1} failed for {url[:60]}: {exc}. Retrying…")
                await asyncio.sleep(RETRY_DELAY_S)
            else:
                logger.warning(f"[GOTO] all retries exhausted for {url[:60]}: {exc}")
    return False


async def _smart_wait(page: Page, timeout: int = 5_000) -> None:
    """Wait for network to be reasonably idle — faster than a fixed sleep."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        # networkidle can time-out on heavy JS pages — that's fine
        pass


def _is_blocked(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in BOT_KEYWORDS)


async def _safe_evaluate(page: Page, js: str, arg=None, retries: int = 2) -> any:
    """Run page.evaluate() with retry on navigation-destroyed context errors.

    Flipkart (and some other sites) issue redirects mid-load which destroy the
    JS execution context.  We wait for the page to stabilise before each attempt.
    """
    for attempt in range(retries + 1):
        try:
            # Ensure page is not mid-navigation before evaluating
            await page.wait_for_load_state("domcontentloaded", timeout=8_000)
            if arg is not None:
                return await page.evaluate(js, arg)
            return await page.evaluate(js)
        except Exception as exc:
            err = str(exc)
            is_nav_err = (
                "execution context was destroyed" in err.lower()
                or "navigation" in err.lower()
                or "target closed" in err.lower()
            )
            if is_nav_err and attempt < retries:
                logger.warning(f"[EVAL] Context destroyed (attempt {attempt+1}) — waiting and retrying…")
                await asyncio.sleep(1.5)
                # Wait again after the redirect settles
                try:
                    await page.wait_for_load_state("networkidle", timeout=6_000)
                except Exception:
                    pass
            else:
                raise


# ---------------------------------------------------------------------------
# Main Agent
# ---------------------------------------------------------------------------

class EcommerceAgent:
    """Scrape product listings from Flipkart, Amazon, Meesho, and other sites."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def scrape(
        self,
        url: str,
        limit: int = 1,
        pages: int = 1,
        enrich_sellers: bool = False,
    ) -> list[dict]:
        """
        Scrape up to `limit` products from a search/category/product URL.

        Args:
            url:             Product page or search-results URL.
            limit:           Max products to return.
            pages:           How many search-result pages to paginate through.
            enrich_sellers:  Google-search each seller for phone/email.
        """
        site = _detect_site(url)
        logger.info(f"[ECOM] Site detected: {site!r} | url={url[:80]} | limit={limit} | pages={pages}")

        context = await self.browser_manager.new_context()
        try:
            results = await self._run(context, url, site, limit, pages)

            if enrich_sellers and results:
                logger.info(f"[ENRICH] Enriching {len(results)} sellers…")
                enricher = SellerEnricher(context)
                await enricher.enrich_batch(results)
                logger.info("[ENRICH] Done")

            return results

        finally:
            await context.close()

    # ------------------------------------------------------------------
    # Core dispatch
    # ------------------------------------------------------------------

    async def _run(
        self,
        context: BrowserContext,
        url: str,
        site: str,
        limit: int,
        pages: int,
    ) -> list[dict]:
        page = await context.new_page()

        # ── Zepto: Blocked Category URLs / Cloudflare check ────────────────────
        if site == "zepto":
            if "/cn/" in url or "/c/" in url:
                logger.warning(f"[ZEPTO] Category URL detected: {url}. These are blocked by Cloudflare. Returning empty, API validator should catch this.")
                await page.close()
                return []
            
            # For Search and Product URLs, Zepto requires location cookies first.
            logger.info("[ZEPTO] Pre-loading homepage to set location cookies")
            await _safe_goto(page, "https://www.zepto.com/")
            await _smart_wait(page)
            # Now let it fall through to load the actual URL

        # ── Load the first page (all other sites) ───────────────────────────────
        await _safe_goto(page, url)
        await _smart_wait(page)

        # ── Myntra: card scraping with smart scroll ──────────────────────────────
        if site == "myntra":
            results = await self._scrape_myntra_cards(page, limit)
            await page.close()
            return results

        # ── Meesho: ALWAYS try card scraping — skip bot check here ──────────────
        # Meesho homepage often returns "Access Denied" as initial page title
        # while the actual React app loads. Card scraper handles this with scrolls.
        if site == "meesho":
            # Meesho is heavily JS-rendered — use networkidle to ensure cards are in DOM
            try:
                await page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass  # proceed even if networkidle times out
            results = await self._scrape_meesho_cards(page, limit)
            await page.close()
            return results

        # Non-Meesho: check for bot/CAPTCHA block now
        if _is_blocked(await page.title()):
            logger.warning(f"[BOT] Blocked on first load — {await page.title()}")
            await page.close()
            return []

        # Non-meesho: if limit==1 and URL is already a product detail page, scrape directly
        if limit == 1 and _is_product_url(url, site):
            result = await self._extract_product(page, url, site)
            await page.close()
            return [result] if result else []

        # ── Flipkart / Zepto: scroll to trigger lazy-loaded product cards ──────
        if site in ("flipkart", "zepto"):
            await self._scroll_to_load(page, rounds=4 if site == "zepto" else 8)
            # Wait for at least one product link — grid is heavily JS-rendered
            try:
                if site == "flipkart":
                    await page.wait_for_selector('a[href*="/p/"]', timeout=8_000)
                elif site == "zepto":
                    await page.wait_for_selector('a[href*="/pn/"]', timeout=8_000)
                logger.info(f"[{site.upper()}] product links detected in DOM")
            except Exception:
                pass
            # Diagnostic: log exactly what the page shows
            _title = await page.title()
            _url = page.url
            _all_hrefs: list[str] = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            _p_links = [h for h in _all_hrefs if "/p/" in urllib.parse.urlparse(h).path.lower()]
            logger.info(
                "[FLIPKART DIAG] title=%r | current_url=%s | total_hrefs=%d | /p/_links=%d",
                _title, _url[:100], len(_all_hrefs), len(_p_links),
            )
            if _p_links:
                logger.info("[FLIPKART DIAG] Sample: %s", _p_links[:3])
            else:
                logger.warning("[FLIPKART DIAG] Zero /p/ links — bot-blocked or store page renders client-side only")


        # ── Search/category page → collect product links ─────────────
        all_links: list[str] = []
        seen_links: set[str] = set()
        current_page = 1

        while current_page <= pages and len(all_links) < limit:
            new_links = await self._collect_product_links(page, site, limit - len(all_links))
            for lnk in new_links:
                clean = lnk.split("?")[0]
                if clean not in seen_links:
                    seen_links.add(clean)
                    all_links.append(lnk)

            logger.info(f"[LINKS] Page {current_page}/{pages} — {len(all_links)} product links collected")

            if current_page < pages and len(all_links) < limit:
                went_next = await self._go_next_page(page, site, current_page)
                if not went_next:
                    logger.info("[PAGINATION] No next page found — stopping.")
                    break
                await _smart_wait(page)
                current_page += 1
            else:
                break

        await page.close()

        all_links = all_links[:limit]
        if not all_links:
            logger.warning("[LINKS] No product links found — nothing to scrape.")
            return []

        # ── Scrape product details in parallel ────────────────────────
        return await self._scrape_new_tabs(context, all_links, site)

    # ------------------------------------------------------------------
    # Scroll helper (triggers lazy-loaded JS cards)
    # ------------------------------------------------------------------

    async def _scroll_to_load(self, page: Page, rounds: int = 8) -> None:
        """Scroll to page bottom repeatedly to trigger Flipkart lazy loading.

        scrollTo(0, scrollHeight) jumps to the true bottom each round, triggering
        intersection observers as new content appends and extends scrollHeight.
        Do NOT scroll back to top between rounds — that resets the viewport and
        prevents product cards from entering the visible zone.
        """
        logger.info(f"[SCROLL] Bottom-scroll ({rounds} rounds)…")
        for i in range(rounds):
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.0)
            except Exception as exc:
                logger.warning(f"[SCROLL] Round {i+1} interrupted: {exc}")
                break
        # Return to top once at the end so link collection covers the full page
        try:
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)
        except Exception:
            pass
        # Final networkidle after scrolls settle
        try:
            await page.wait_for_load_state("networkidle", timeout=6_000)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Link collection
    # ------------------------------------------------------------------

    async def _collect_product_links(self, page: Page, site: str, max_links: int) -> list[str]:
        """Collect product-detail hrefs from the current search-results page."""
        # Standard a[href] collection + robust fallback for custom stores (like Flipkart inline stores)
        hrefs: list[str] = await page.evaluate(r"""
            () => {
                const links = new Set([...document.querySelectorAll('a[href]')].map(a => a.href));
                
                // Fallback: Find elements with prices (₹) and get their wrapping anchor
                // This helps on custom promotional pages where links might be structured weirdly
                const priceEls = [...document.querySelectorAll('*')].filter(e => {
                    const t = (e.innerText || '').trim();
                    return /^₹[\d,]+$/.test(t) && e.children.length === 0;
                });
                
                for (const p of priceEls) {
                    let curr = p;
                    let anchor = null;
                    for (let i = 0; i < 6 && curr; i++) {
                        if (curr.tagName === 'A' && curr.href) {
                            anchor = curr;
                            break;
                        }
                        curr = curr.parentElement;
                    }
                    if (anchor && anchor.href) {
                        links.add(anchor.href);
                    }
                }
                
                return Array.from(links);
            }
        """)

        links: list[str] = []
        seen: set[str] = set()
        for h in hrefs:
            if not _is_product_url(h, site):
                continue
            clean = h.split("?")[0]
            if clean in seen:
                continue
            seen.add(clean)
            links.append(h)
            if len(links) >= max_links * 2:   # collect buffer for dedup
                break
        return links

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def _go_next_page(self, page: Page, site: str, current_page_num: int) -> bool:
        """Try to navigate to the next page. Returns True if successful."""
        try:
            if site == "flipkart":
                # Flipkart: look for "Next" button or page number
                next_btn = page.locator("a._9QVEpD, a[href*='page=']").filter(has_text="Next")
                if await next_btn.count():
                    await next_btn.first.click()
                    await _smart_wait(page)
                    return True
                # URL-based: add page param
                parsed = urllib.parse.urlparse(page.url)
                qs = urllib.parse.parse_qs(parsed.query)
                qs["page"] = [str(current_page_num + 1)]
                new_url = parsed._replace(query=urllib.parse.urlencode(qs, doseq=True)).geturl()
                await _safe_goto(page, new_url)
                return True

            if site == "amazon":
                next_btn = page.locator("a.s-pagination-next")
                if await next_btn.count():
                    await next_btn.first.click()
                    await _smart_wait(page)
                    return True

            if site in ("myntra", "snapdeal", "nykaa", "generic"):
                next_btn = page.locator("a[aria-label='next'], button[aria-label='next'], a:has-text('Next')")
                if await next_btn.count():
                    await next_btn.first.click()
                    await _smart_wait(page)
                    return True

        except Exception as exc:
            logger.warning(f"[PAGINATION] Error going to next page: {exc}")
        return False

    # ------------------------------------------------------------------
    # Parallel tab scraping (Flipkart, Amazon, generic)
    # ------------------------------------------------------------------

    async def _scrape_new_tabs(
        self, context: BrowserContext, links: list[str], site: str
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def worker(link: str) -> dict | None:
            async with semaphore:
                p = await context.new_page()
                try:
                    ok = await _safe_goto(p, link)
                    if not ok:
                        return None
                    await _smart_wait(p)

                    if _is_blocked(await p.title()):
                        logger.warning(f"[BOT] {await p.title()} — {link[:60]}")
                        return None

                    result = await self._extract_product(p, link, site)
                    if result:
                        logger.info(
                            f"[OK] {result.get('product_name','')[:45]!r} | "
                            f"price={result.get('price','')!r} | "
                            f"seller={result.get('seller_name','')!r}"
                        )
                    return result
                except Exception as exc:
                    logger.error(f"[FAIL] {link[:60]}: {type(exc).__name__}: {exc}")
                    return None
                finally:
                    if not p.is_closed():
                        await p.close()

        gathered = await asyncio.gather(*(worker(l) for l in links))
        results = [g for g in gathered if g is not None]
        logger.info(f"[DONE] {len(results)}/{len(links)} products extracted")
        return results

    # ------------------------------------------------------------------
    # Myntra: card extraction with smart scroll
    # ------------------------------------------------------------------

    async def _scrape_myntra_cards(self, page: Page, limit: int) -> list[dict]:
        """Extract product cards from a Myntra listing/brand/shop page.

        Myntra brand-store pages have hero banners before the product grid.
        We scroll in rounds and stop as soon as /digits/buy links appear —
        no fixed-round limit that risks stopping in the hero section.
        """
        title = await page.title()
        if _is_blocked(title):
            logger.warning(f"[MYNTRA] Blocked — title={title!r}")
            return []

        logger.info("[MYNTRA] Smart-scroll to find product grid…")
        found_products = False
        for i in range(20):  # max 20 rounds (~20 s) to reach the product grid
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.0)
            except Exception as exc:
                logger.warning(f"[MYNTRA] Scroll round {i+1} interrupted: {exc}")
                break

            # Check after each round whether real product links are in DOM
            buy_count: int = await page.evaluate("""
                () => document.querySelectorAll('a[href*="/buy"]').length
            """)
            logger.info(f"[MYNTRA] Round {i+1}: /buy links in DOM = {buy_count}")
            if buy_count >= 3:  # at least 3 products visible → grid is loaded
                found_products = True
                logger.info(f"[MYNTRA] Product grid found after {i+1} scroll rounds")
                break

        if not found_products:
            logger.warning("[MYNTRA] Product grid not found — may be a brand-story page with no direct product grid")

        # Keep scrolling to load more cards up to limit
        extra_rounds = max(2, limit // 10)
        for i in range(extra_rounds):
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.2)
            except Exception:
                break

        cards: list[dict] = await _safe_evaluate(
            page,
            """(limit) => {
                const clean = t => t ? t.replace(/\\n/g, ' ').replace(/\\s+/g, ' ').trim() : '';

                // Myntra product links end with /digits/buy
                const anchors = [...document.querySelectorAll('a[href]')].filter(a => {
                    return /\\/\\d+\\/buy/.test(new URL(a.href, location.href).pathname);
                });
                const seen = new Set();
                const results = [];

                for (const a of anchors) {
                    if (results.length >= limit) break;
                    const href = a.href.split('?')[0];
                    if (seen.has(href)) continue;
                    seen.add(href);

                    // Image — first img inside the card anchor
                    const img = a.querySelector('img');

                    // All visible text in this card
                    const text = clean(a.textContent || '');

                    // Price — ₹ followed by digits/commas
                    const priceMatch = text.match(/₹[\\s\\u00a0]?[\\d,]+/);
                    const price = priceMatch ? priceMatch[0].replace(/\\s/g, '').trim() : '';

                    // Product name — text before the price, or first heading
                    let name = '';
                    if (price) {
                        const idx = text.indexOf(price);
                        if (idx > 0) name = text.substring(0, idx).trim();
                    }
                    if (!name) {
                        const el = a.querySelector('h3, h4, p, [class*="product-brand"], [class*="product-product"]');
                        if (el) name = clean(el.textContent);
                    }
                    if (!name && text) {
                        // last fallback: first line of card text
                        name = text.split(' ').slice(0, 8).join(' ');
                    }

                    // Rating
                    let rating = '', reviews = '';
                    const ratingEl = a.querySelector('[class*="product-ratingsCount"], [class*="ratings"]');
                    if (ratingEl) {
                        const rm = ratingEl.textContent.match(/([1-5](?:\\.[0-9])?)/);
                        if (rm) rating = rm[1];
                    }
                    const revMatch = text.match(/(\\d[\\d,]*)\\s*(?:Rating|Review)s?/i);
                    if (revMatch) reviews = revMatch[1];

                    if (!name && !price) continue;

                    results.push({
                        url: a.href,
                        product_name: name.substring(0, 150),
                        price: price,
                        image: img ? img.src : '',
                        description: '',
                        rating: rating,
                        reviews: reviews,
                        seller_name: '',
                        seller_phone: '',
                        platform: 'myntra',
                    });
                }
                return results;
            }""",
            limit,
        )

        logger.info(f"[MYNTRA] {len(cards)}/{limit} cards extracted")
        return cards

    # ------------------------------------------------------------------
    # Zepto: API response interception — most reliable for location-gated SPAs
    # ------------------------------------------------------------------

    async def _scrape_zepto_cards(self, page: Page, url: str, limit: int) -> list[dict]:
        """Intercept Zepto's JSON API responses to extract product data directly.

        Zepto is location-gated: DOM scraping fails without a set location.
        Instead, we listen to every JSON API response the page fires and parse
        any response that contains product-like objects (name + price fields).
        We also inject a Mumbai-area location into localStorage before load so
        the category page actually fires product API calls.
        Must be called BEFORE page navigation so the response listener is active.
        """
        captured: list[dict] = []

        def _make_product(item: dict) -> dict | None:
            """Convert a Zepto product dict to our standard format."""
            name = item.get("name") or item.get("productName") or item.get("itemName") or ""
            if not isinstance(name, str) or not name.strip():
                return None
            price_raw = (item.get("sellingPrice") or item.get("discountedPrice")
                         or item.get("mrp") or item.get("price") or 0)
            if not isinstance(price_raw, (int, float)) or price_raw <= 0:
                return None
            rupees = int(price_raw) // 100 if int(price_raw) > 500 else int(price_raw)

            img = item.get("images") or item.get("image") or item.get("imageUrl") or ""
            if isinstance(img, list) and img:
                img = img[0].get("url", img[0]) if isinstance(img[0], dict) else img[0]
            if isinstance(img, dict):
                img = img.get("url") or img.get("src") or ""

            pid = item.get("productId") or item.get("id") or ""
            slug = name.lower().replace(" ", "-")[:40]
            prod_url = f"https://www.zepto.com/pn/{slug}/pvid/{pid}/" if pid else page.url

            unit = item.get("unit") or item.get("unitString") or ""
            if isinstance(unit, dict):
                unit = unit.get("text") or ""

            return {
                "url": prod_url,
                "product_name": (f"{name} — {unit}" if unit else name)[:150],
                "price": f"₹{rupees}",
                "image": str(img),
                "description": str(item.get("description") or item.get("shortDescription") or "")[:300],
                "rating": str(item.get("rating") or item.get("averageRating") or ""),
                "reviews": str(item.get("reviewCount") or item.get("numRatings") or ""),
                "seller_name": "",
                "seller_phone": "",
                "platform": "zepto",
            }

        def _extract_from_json(obj, depth: int = 0) -> list[dict]:
            """Walk Zepto JSON: try pageLayout structure first, then generic."""
            if depth > 15:
                return []
            results = []

            # ── Zepto pageLayout: known structure ─────────────────────────────
            if isinstance(obj, dict) and "pageLayout" in obj:
                layout = obj["pageLayout"]
                sections = layout.get("sections") or [] if isinstance(layout, dict) else []
                for section in sections:
                    if not isinstance(section, dict):
                        continue
                    for widget in (section.get("widgets") or []):
                        if not isinstance(widget, dict):
                            continue
                        wdata = widget.get("data") or {}
                        for key in ("products", "items", "catalogItems", "productList",
                                    "productData", "data"):
                            items = wdata.get(key) if isinstance(wdata, dict) else None
                            if isinstance(items, list):
                                for it in items:
                                    if isinstance(it, dict):
                                        p = _make_product(it)
                                        if p:
                                            results.append(p)
                if results:
                    return results

            # ── Generic recursive fallback ─────────────────────────────────────
            if isinstance(obj, list):
                for item in obj:
                    results.extend(_extract_from_json(item, depth + 1))
            elif isinstance(obj, dict):
                p = _make_product(obj)
                if p:
                    results.append(p)
                else:
                    for v in obj.values():
                        results.extend(_extract_from_json(v, depth + 1))
            return results

        store_id: list[str] = []

        async def on_response(response):
            if len(captured) >= limit * 3:
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            resp_url = response.url
            try:
                body = await response.json()
            except Exception:
                return
            top_keys = list(body.keys())[:8] if isinstance(body, dict) else f"list[{len(body)}]"
            logger.info(f"[ZEPTO JSON] {resp_url[:100]} → keys={top_keys}")

            # Extract store_id from storeServiceableResponse
            if isinstance(body, dict) and "storeServiceableResponse" in body:
                ssr = body["storeServiceableResponse"]
                if isinstance(ssr, dict):
                    sid = ssr.get("storeId") or ssr.get("store_id") or ssr.get("id") or ""
                    if sid and not store_id:
                        store_id.append(str(sid))
                        logger.info(f"[ZEPTO] store_id captured: {sid}")
                    logger.info(f"[ZEPTO] storeServiceableResponse keys: {list(ssr.keys())[:8]}")

            # Log pageLayout section structure for debugging
            if isinstance(body, dict) and "pageLayout" in body:
                layout = body.get("pageLayout") or {}
                sections = layout.get("sections") or [] if isinstance(layout, dict) else []
                logger.info(f"[ZEPTO] pageLayout has {len(sections)} sections")
                for i, sec in enumerate(sections[:3]):
                    if not isinstance(sec, dict):
                        continue
                    widgets = sec.get("widgets") or []
                    logger.info(f"[ZEPTO] Section[{i}] keys={list(sec.keys())[:6]} widgets={len(widgets)}")
                    for j, w in enumerate(widgets[:2]):
                        if isinstance(w, dict):
                            wdata = w.get("data") or {}
                            logger.info(f"[ZEPTO]   Widget[{j}] type={w.get('widgetType','?')} data_keys={list(wdata.keys())[:6] if isinstance(wdata,dict) else '?'}")

            products = _extract_from_json(body)
            if products:
                logger.info(f"[ZEPTO API] +{len(products)} products from {resp_url[:90]}")
                captured.extend(products)

        # Intercept requests to grab Zepto's real lat/lon (IP-detected by Zepto)
        zepto_lat: list[str] = []
        zepto_lon: list[str] = []

        async def on_request(request):
            if "bff-gateway.zepto.com" in request.url and "get_page" in request.url:
                parsed_req = urllib.parse.urlparse(request.url)
                qs = urllib.parse.parse_qs(parsed_req.query)
                if qs.get("latitude"):
                    zepto_lat.clear(); zepto_lat.append(qs["latitude"][0])
                    zepto_lon.clear(); zepto_lon.append(qs.get("longitude", ["72.8777"])[0])
                    logger.info(f"[ZEPTO] Detected location lat={zepto_lat[0]} lon={zepto_lon[0]}")

        page.on("request", on_request)
        page.on("response", on_response)

        logger.info(f"[ZEPTO] Step 1 — loading home to detect location & set cookies")
        await _safe_goto(page, "https://www.zepto.com/")
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        # Now parse category/subcategory IDs from the original URL
        parsed_url = urllib.parse.urlparse(url)
        path_parts = [p for p in parsed_url.path.split("/") if p]
        cid, scid = "", ""
        try:
            if "cid" in path_parts:
                cid = path_parts[path_parts.index("cid") + 1]
            if "scid" in path_parts:
                scid = path_parts[path_parts.index("scid") + 1]
        except (ValueError, IndexError):
            pass

        lat = zepto_lat[0] if zepto_lat else "19.0760"
        lon = zepto_lon[0] if zepto_lon else "72.8777"
        sid = store_id[0] if store_id else ""
        logger.info(f"[ZEPTO] Category fetch: cid={cid} scid={scid} lat={lat} lon={lon} store_id={sid}")

        # Use browser's fetch (Zepto cookies set) to call category API with store_id
        for page_type in ("SUBCATEGORY", "CATEGORY", "PLP", "SUB_CATEGORY"):
            api_url = (
                f"https://bff-gateway.zepto.com/lms/api/v2/get_page"
                f"?latitude={lat}&longitude={lon}&page_type={page_type}"
                + (f"&store_id={sid}&storeId={sid}" if sid else "")
                + (f"&category_id={cid}" if cid else "")
                + (f"&sub_category_id={scid}" if scid else "")
            )
            logger.info(f"[ZEPTO] Trying {page_type}: {api_url[:140]}")
            try:
                resp_data = await page.evaluate(f"""
                    async () => {{
                        const r = await fetch({repr(api_url)}, {{
                            credentials: 'include',
                            headers: {{
                                'Accept': 'application/json, text/plain, */*',
                                'x-store-id': {repr(sid)},
                                'x-latitude': {repr(lat)},
                                'x-longitude': {repr(lon)},
                            }}
                        }});
                        const status = r.status;
                        let body = null;
                        try {{ body = await r.json(); }} catch(e) {{}}
                        return {{ status, body }};
                    }}
                """)
                if resp_data:
                    status = resp_data.get("status", 0)
                    body = resp_data.get("body")
                    top = list(body.keys())[:6] if isinstance(body, dict) else body
                    logger.info(f"[ZEPTO] {page_type} HTTP {status} → keys={top}")
                    if body and isinstance(body, dict):
                        products = _extract_from_json(body)
                        if products:
                            logger.info(f"[ZEPTO] {len(products)} products from {page_type}")
                            captured.extend(products)
                            break
            except Exception as exc:
                logger.warning(f"[ZEPTO] {page_type} fetch failed: {exc}")
                continue

        # Fallback: scroll the category page and capture any new API calls
        if not captured:
            logger.info("[ZEPTO] Falling back to category page scroll")
            await _safe_goto(page, url)
            try:
                await page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            for _ in range(8):
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(1.2)
                except Exception:
                    break
                if len(captured) >= limit:
                    break

        # Deduplicate by product_name + price
        seen: set[str] = set()
        unique: list[dict] = []
        for p in captured:
            key = f"{p['product_name']}|{p['price']}"
            if key not in seen:
                seen.add(key)
                unique.append(p)
            if len(unique) >= limit:
                break

        logger.info(f"[ZEPTO] {len(unique)}/{limit} unique products after dedup")
        return unique

    # ------------------------------------------------------------------
    # Meesho: card extraction (no product page visit — Cloudflare protected)
    # ------------------------------------------------------------------

    async def _scrape_meesho_cards(self, page: Page, limit: int) -> list[dict]:
        """Extract product cards directly from Meesho page (search or category URL).

        NOTE: Meesho homepage (meesho.com/) is permanently blocked by Cloudflare
        for headless browsers. Always use a search URL like:
          https://www.meesho.com/search?q=kurta
        """
        # ── FAST FAIL: check if Cloudflare already blocked us ──────────────────
        # Do this BEFORE any scrolling to save ~50s of wasted wait time
        current_title = await page.title()
        current_url = page.url
        if _is_blocked(current_title):
            logger.warning(
                f"[MEESHO] ❌ Page is BLOCKED by Cloudflare — "
                f"title={current_title!r} | url={current_url}\n"
                f"         → Use a search URL like: https://www.meesho.com/search?q=PRODUCT"
            )
            return []

        # ── Phase 1: Initial scrolls to trigger React hydration ────────────────
        logger.info("[MEESHO] Initial scroll to trigger card load...")
        for _ in range(3):
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.2)
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.5)
            except Exception as exc:
                # Cloudflare may redirect mid-scroll — bail early
                logger.warning(f"[MEESHO] Scroll interrupted (likely Cloudflare redirect): {exc}")
                return []

        # ── Phase 2: Wait for ANY product card to appear ───────────────────────
        found = False
        for selector in [
            'a[href*="/p/"]',           # standard product links
            '[class*="ProductCard"]',   # React component class
            '[class*="product-card"]',
            'div[class*="Card"] a',
        ]:
            try:
                await page.wait_for_selector(selector, timeout=8_000)
                found = True
                logger.info(f"[MEESHO] Cards detected with selector: {selector!r}")
                break
            except Exception:
                continue

        if not found:
            logger.warning(
                f"[MEESHO] No product cards found | url={page.url} | title={await page.title()!r}\n"
                f"         → Make sure you are using a search/category URL, not the homepage."
            )
            return []

        # ── Phase 3: Additional scrolls to load more cards ─────────────────────
        scroll_rounds = max(2, limit // 10)
        for i in range(scroll_rounds):
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.5)
                logger.info(f"[MEESHO] Loading scroll {i+1}/{scroll_rounds}...")
            except Exception:
                logger.warning("[MEESHO] Scroll interrupted in phase 3 — proceeding with cards found so far")
                break

        cards: list[dict] = await _safe_evaluate(
            page,
            """(limit) => {
                const clean = t => t ? t.replace(/\\n/g, ' ').replace(/\\s+/g, ' ').trim() : '';

                // Meesho product cards are <div> inside a grid — each contains an <a href="/p/...">
                const anchors = [...document.querySelectorAll('a[href*="/p/"]')];
                const seen = new Set();
                const results = [];

                for (const a of anchors) {
                    if (results.length >= limit) break;
                    const href = a.href.split('?')[0];
                    if (seen.has(href)) continue;
                    seen.add(href);

                    // Image
                    const img = a.querySelector('img');

                    // All text inside the card
                    const text = clean(a.textContent || '');

                    // Price — ₹ followed by digits/commas
                    const priceMatch = text.match(/₹\\s?[\\d,]+/);
                    const price = priceMatch ? priceMatch[0].trim() : '';

                    // Product name — text before price
                    // First strip countdown timer pattern (e.g. "23h : 45m : 10s")
                    let rawText = text.replace(/\\d{1,2}h\\s*:\\s*\\d{2}m\\s*:\\s*\\d{2}s/gi, '').trim();
                    let name = '';
                    if (price) {
                        const idx = rawText.indexOf(price);
                        if (idx > 0) name = rawText.substring(0, idx).replace(/^\\+\\d+\\s*More/i, '').trim();
                    }
                    if (!name) {
                        // fallback: first <p> or <h4> inside card
                        const el = a.querySelector('p, h4, h3, span[class*="name"], span[class*="title"]');
                        if (el) name = clean(el.textContent).replace(/\\d{1,2}h\\s*:\\s*\\d{2}m\\s*:\\s*\\d{2}s/gi, '').trim();
                    }

                    // Rating — look for aria-label on star/rating elements first
                    let rating = '', reviews = '';
                    const ratingEl = a.querySelector('[aria-label*="star"], [aria-label*="rating"], [class*="StarRating"], [class*="star-rating"]');
                    if (ratingEl) {
                        const label = ratingEl.getAttribute('aria-label') || ratingEl.textContent || '';
                        const rm = label.match(/([1-5](?:\\.[0-9])?)/); 
                        if (rm) rating = rm[1];
                    }
                    if (!rating) {
                        // fallback: find floating-point 1.0–5.0 in full text
                        const rm2 = rawText.match(/\\b([1-5]\\.[0-9])\\b/);
                        if (rm2) rating = rm2[1];
                    }
                    // Review count
                    const revMatch = rawText.match(/(\\d[\\d,]*)\\s*(?:Review|review|Rating|rating)s?/);
                    if (revMatch) reviews = revMatch[1];

                    if (!name && !price) continue;

                    results.push({
                        url: a.href,
                        product_name: name.substring(0, 150),
                        price: price,
                        image: img ? img.src : '',
                        description: '',
                        rating: rating,
                        reviews: reviews,
                        seller_name: '',
                        seller_phone: '',
                        platform: 'meesho',
                    });
                }
                return results;
            }""",
            limit,
        )

        logger.info(f"[MEESHO] {len(cards)}/{limit} cards extracted from search page")
        return cards

    # ------------------------------------------------------------------
    # Site-specific product page extraction
    # ------------------------------------------------------------------

    async def _extract_product(self, page: Page, url: str, site: str) -> dict | None:
        """Extract structured data from an open product detail page."""
        try:
            if site == "flipkart":
                return await self._extract_flipkart(page, url)
            if site == "amazon":
                return await self._extract_amazon(page, url)
            if site == "myntra":
                return await self._extract_myntra(page, url)
            if site == "snapdeal":
                return await self._extract_snapdeal(page, url)
            return await self._extract_generic(page, url, site)
        except Exception as exc:
            logger.error(f"[EXTRACT] {url[:60]}: {exc}")
            return None

    # ── Flipkart ────────────────────────────────────────────────────

    async def _extract_flipkart(self, page: Page, url: str) -> dict | None:
        data: dict = await _safe_evaluate(page, r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';

                // Title
                const h1 = document.querySelector('h1.yhB1nd, h1._6EBuvT, h1');
                const title = h1 ? clean(h1.innerText) : document.title;

                // Price — Flipkart uses span.Nx9bqj or div._30jeq3
                let price = '';
                const priceEl = document.querySelector('div._30jeq3, div.Nx9bqj, div._16Jk6d, [class*="finalPrice"]');
                if (priceEl) price = clean(priceEl.innerText);
                if (!price) {
                    const els = [...document.querySelectorAll('div,span,p')].filter(e => {
                        const t = (e.innerText||'').trim();
                        return /^₹[\d,]+$/.test(t);
                    });
                    if (els.length) {
                        els.sort((a,b) => parseFloat(getComputedStyle(b).fontSize||0) - parseFloat(getComputedStyle(a).fontSize||0));
                        price = clean(els[0].innerText);
                    }
                }

                // ── Seller: extract sellers-page URL from product page ─────────────
                // Flipkart no longer shows "Sold by" on product pages (2024+).
                // Instead it has: <a href="/sellers?pid=...">See other sellers</a>
                let sellers_url = '';
                const sellersLink = document.querySelector('a[href*="/sellers?pid="]');
                if (sellersLink) sellers_url = sellersLink.href;

                // Image
                let image = '';
                const ogImg = document.querySelector('meta[property="og:image"]');
                if (ogImg) image = ogImg.getAttribute('content') || '';
                if (!image) {
                    const img = document.querySelector('img.DByuf4, img._396cs4, img[src*="rukminim"]');
                    if (img) image = img.src;
                }

                // Description — JSON-LD first
                let description = '';
                try {
                    const ld = JSON.parse(document.querySelector('script[type="application/ld+json"]')?.textContent || '{}');
                    const obj = Array.isArray(ld) ? ld[0] : ld;
                    description = obj?.description || '';
                } catch(e) {}
                if (!description) {
                    const meta = document.querySelector('meta[property="og:description"],meta[name="description"]');
                    if (meta) description = meta.getAttribute('content') || '';
                }

                return { title, price, sellers_url, image, description };
            }
        """)

        if not data or not data.get("title"):
            return None

        # ── Visit sellers page to get first seller name ───────────────────────
        seller_name = ""
        sellers_url = data.get("sellers_url", "")
        if sellers_url:
            seller_name = await self._get_flipkart_first_seller(page, sellers_url)
            logger.info(f"[FLIPKART] seller={seller_name!r} from {sellers_url[:60]}")

        return {
            "url": url,
            "product_name": data["title"],
            "price": data["price"],
            "image": data["image"],
            "description": data["description"],
            "seller_name": seller_name,
            "seller_phone": "",
            "platform": "flipkart",
        }

    async def _get_flipkart_first_seller(self, page: Page, sellers_url: str) -> str:
        """Visit Flipkart /sellers?pid= page and return the first seller's name."""
        try:
            await page.goto(sellers_url, wait_until="domcontentloaded", timeout=15_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass

            seller = await _safe_evaluate(page, r"""
                () => {
                    const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';

                    // Body text pattern on sellers page:
                    // "Seller\nPrice\nDelivery\nSELLER_NAME\n4.x\n..."
                    const bodyText = document.body.innerText || '';
                    const m = bodyText.match(/Seller\s+Price\s+Delivery\s+([A-Za-z0-9][A-Za-z0-9 &',.-]{1,59})(?:\n|\r)/i);
                    if (m) return m[1].trim();

                    // Fallback: first span with text that looks like a seller handle
                    // Seller names on Flipkart are typically CamelCase or AllUpperCase, 5-40 chars
                    const spans = [...document.querySelectorAll('span, div, a')].filter(e => {
                        const t = (e.innerText || '').trim();
                        return t.length >= 4 && t.length <= 50 &&
                               /^[A-Z][A-Za-z0-9]/.test(t) &&
                               !/(seller|price|delivery|flipkart|login|cart|more|offer|emi|policy|replacement|cod|available)/i.test(t) &&
                               e.children.length === 0;
                    });
                    return spans.length ? clean(spans[0].innerText) : '';
                }
            """)
            return seller or ""
        except Exception as exc:
            logger.warning(f"[FLIPKART] Seller page fetch failed: {exc}")
            return ""

    # ── Amazon ──────────────────────────────────────────────────────

    async def _extract_amazon(self, page: Page, url: str) -> dict | None:
        data: dict = await _safe_evaluate(page, r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';

                // Title — #productTitle span is the most reliable on Amazon
                // Avoid plain h1 which can pick up accessibility/shortcut text
                let title = '';
                const ptSpan = document.querySelector('#productTitle span, #productTitle');
                if (ptSpan) title = clean(ptSpan.innerText);
                if (!title) {
                    // fallback: og:title meta (usually clean)
                    const og = document.querySelector('meta[property="og:title"]');
                    if (og) title = og.getAttribute('content') || '';
                }
                if (!title) title = document.title.split(':')[0].trim();

                // Price — Amazon splits whole + fraction
                let price = '';
                const whole = document.querySelector('span.a-price-whole');
                const frac  = document.querySelector('span.a-price-fraction');
                if (whole) {
                    price = '₹' + clean(whole.innerText).replace('.','') + (frac ? '.' + clean(frac.innerText) : '');
                }
                if (!price) {
                    const priceEl = document.querySelector(
                        '#priceblock_ourprice,#priceblock_dealprice,#priceblock_saleprice,.a-price .a-offscreen'
                    );
                    if (priceEl) price = clean(priceEl.innerText);
                }

                // Seller
                let seller = '';
                const sellerEl = document.querySelector(
                    '#sellerProfileTriggerId, #merchant-info a, a#bylineInfo, #SSOFpopoverLink'
                );
                if (sellerEl) seller = clean(sellerEl.innerText);
                if (!seller) {
                    const merchantInfo = document.querySelector('#merchant-info');
                    if (merchantInfo) seller = clean(merchantInfo.innerText).replace(/^Ships from and sold by /, '');
                }

                // Image
                let image = '';
                const landingImg = document.querySelector('#landingImage, #imgBlkFront');
                if (landingImg) {
                    image = landingImg.getAttribute('data-old-hires') ||
                            landingImg.getAttribute('data-a-dynamic-image')?.match(/"(https[^"]+)"/)?.[1] ||
                            landingImg.src || '';
                }
                if (!image) {
                    const ogImg = document.querySelector('meta[property="og:image"]');
                    if (ogImg) image = ogImg.getAttribute('content') || '';
                }

                // Description
                let description = '';
                try {
                    const ld = JSON.parse(document.querySelector('script[type="application/ld+json"]')?.textContent || '{}');
                    const obj = Array.isArray(ld) ? ld[0] : ld;
                    description = obj?.description || '';
                } catch(e) {}
                if (!description) {
                    const featureDiv = document.querySelector('#feature-bullets, #productDescription');
                    if (featureDiv) {
                        const bullets = [...featureDiv.querySelectorAll('li span, p')]
                            .map(e => clean(e.innerText))
                            .filter(t => t.length > 5)
                            .slice(0, 5);
                        description = bullets.join(' | ');
                    }
                }
                if (!description) {
                    const meta = document.querySelector('meta[property="og:description"],meta[name="description"]');
                    if (meta) description = meta.getAttribute('content') || '';
                }

                // Rating
                const ratingEl = document.querySelector('#acrPopover .a-size-base, span[data-hook="rating-out-of-text"]');
                const rating = ratingEl ? clean(ratingEl.innerText).split(' ')[0] : '';

                const reviewEl = document.querySelector('#acrCustomerReviewText');
                const reviews = reviewEl ? clean(reviewEl.innerText).replace(/[^\d]/g,'') : '';

                return { title, price, seller, image, description, rating, reviews };
            }
        """)

        if not data or not data.get("title"):
            return None

        return {
            "url": url,
            "product_name": data["title"],
            "price": data["price"],
            "image": data["image"],
            "description": data["description"],
            "seller_name": data["seller"],
            "seller_phone": "",
            "rating": data.get("rating", ""),
            "reviews": data.get("reviews", ""),
            "platform": "amazon",
        }

    # ── Myntra ──────────────────────────────────────────────────────

    async def _extract_myntra(self, page: Page, url: str) -> dict | None:
        data: dict = await _safe_evaluate(page, r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';
                const title = clean(document.querySelector('h1.pdp-name, h1.pdp-title, h1')?.innerText) || document.title;
                let price = clean(document.querySelector('span.pdp-price strong, .pdp-discount-container strong')?.innerText);
                if (!price) {
                    const m = (document.body.innerText||'').match(/₹[\d,]+/);
                    if (m) price = m[0];
                }
                const brand = clean(document.querySelector('h1.pdp-title, .pdp-brand-name-container h1')?.innerText);
                const img = document.querySelector('img.image-grid-image, img.pdp-image');
                const image = img ? img.src : '';
                const desc = clean(document.querySelector('.pdp-product-description-content, .product-desc-content')?.innerText);
                return { title, price, brand, image, description: desc };
            }
        """)
        if not data or not data.get("title"):
            return None
        return {
            "url": url,
            "product_name": data["title"],
            "price": data["price"],
            "image": data["image"],
            "description": data.get("description", ""),
            "seller_name": data.get("brand", ""),
            "seller_phone": "",
            "platform": "myntra",
        }

    # ── Snapdeal ────────────────────────────────────────────────────

    async def _extract_snapdeal(self, page: Page, url: str) -> dict | None:
        data: dict = await _safe_evaluate(page, r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';
                const title = clean(document.querySelector('h1#pdp-product-name, h1.pdp-e-i-head, h1')?.innerText) || document.title;
                const priceEl = document.querySelector('span.payBlkBig, div.pdpPricingInfo .price');
                let price = priceEl ? clean(priceEl.innerText) : '';
                if (!price) {
                    const m = (document.body.innerText||'').match(/₹[\d,]+/);
                    if (m) price = m[0];
                }
                const img = document.querySelector('img.cloudzoom, img#mainimage');
                const image = img ? img.src : '';
                const desc = clean(document.querySelector('.description-main-details, #product-description')?.innerText);
                return { title, price, image, description: desc };
            }
        """)
        if not data or not data.get("title"):
            return None
        return {
            "url": url,
            "product_name": data["title"],
            "price": data["price"],
            "image": data["image"],
            "description": data.get("description", ""),
            "seller_name": "",
            "seller_phone": "",
            "platform": "snapdeal",
        }

    # ── Generic (Nykaa, unknown sites) ──────────────────────────────

    async def _extract_generic(self, page: Page, url: str, site: str) -> dict | None:
        data: dict = await _safe_evaluate(page, r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';

                // Title
                const h1 = document.querySelector('h1');
                const title = h1 ? clean(h1.innerText) : document.title;

                // Price — largest element matching ₹XXX pattern
                let price = '';
                const priceEls = [...document.querySelectorAll('div,span,p')].filter(e => {
                    const t = (e.innerText||'').trim();
                    return /^[₹$][\d,]+(\.\d+)?$/.test(t) || /^[\d,]+(\.\d+)?\s?[₹$]$/.test(t);
                });
                if (priceEls.length) {
                    priceEls.sort((a,b) => parseFloat(getComputedStyle(b).fontSize||0) - parseFloat(getComputedStyle(a).fontSize||0));
                    price = clean(priceEls[0].innerText);
                }
                if (!price) {
                    const m = (document.body.innerText||'').match(/[₹$][\d,]+(\.\d+)?/);
                    if (m) price = m[0];
                }

                // Image — og:image is most reliable
                const ogImg = document.querySelector('meta[property="og:image"]');
                let image = ogImg ? ogImg.getAttribute('content') : '';
                if (!image) {
                    const img = document.querySelector('img[class*="product"],img[class*="item"],img[class*="main"],img#landingImage');
                    if (img) image = img.src;
                }

                // Description — JSON-LD → og:description → meta description → bullet list
                let description = '';
                try {
                    const ld = JSON.parse(document.querySelector('script[type="application/ld+json"]')?.textContent || 'null');
                    if (ld) {
                        const obj = Array.isArray(ld) ? ld[0] : ld;
                        description = obj?.description || '';
                        if (!description && obj?.['@graph']) {
                            const prod = obj['@graph'].find(x => x['@type'] === 'Product');
                            if (prod) description = prod.description || '';
                        }
                    }
                } catch(e) {}
                if (!description) {
                    const meta = document.querySelector('meta[property="og:description"],meta[name="description"]');
                    if (meta) description = meta.getAttribute('content') || '';
                }
                if (!description) {
                    const bullets = [...document.querySelectorAll('ul li')]
                        .filter(li => {
                            let el = li;
                            while (el) {
                                const tag = (el.tagName||'').toLowerCase();
                                if (['nav','header','footer'].includes(tag)) return false;
                                el = el.parentElement;
                            }
                            const t = (li.innerText||'').trim();
                            return t.length > 8 && t.length < 200 &&
                                   !/profile|seller|gift|cart|login|sign|wish|notify/i.test(t);
                        })
                        .slice(0, 5)
                        .map(li => clean(li.innerText));
                    description = bullets.join(' | ');
                }

                // Seller — generic patterns
                let seller = '';
                const soldPatterns = [
                    document.querySelector('a[href*="/seller"], a[href*="seller/"]'),
                ];
                for (const el of soldPatterns) {
                    if (el) { seller = clean(el.innerText); break; }
                }

                return { title, price, seller, image, description };
            }
        """)

        if not data or not data.get("title"):
            return None

        return {
            "url": url,
            "product_name": data["title"],
            "price": data["price"],
            "image": data["image"],
            "description": data.get("description", ""),
            "seller_name": data.get("seller", ""),
            "seller_phone": "",
            "platform": site,
        }
