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

            domain = urllib.parse.urlparse(url).netloc.lower()
            is_meesho = 'meesho.com' in domain

            is_nykaa = 'nykaa.com' in domain

            product_links: list[str] = []
            if limit > 1 and not is_meesho:
                # Nykaa needs longer waits — React renders cards slowly
                scroll_wait = 2500 if is_nykaa else 1500
                scroll_passes = max(2, limit // 8) if is_nykaa else max(1, limit // 10)

                for _ in range(scroll_passes):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(scroll_wait)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(600)

                hrefs = await page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.href)"
                )
                seen: set[str] = set()
                for h in hrefs:
                    h_clean = h.split('?')[0].rstrip('/')
                    # product link patterns across sites
                    is_product = (
                        "/p/" in h or            # Flipkart, Nykaa, Snapdeal, Ajio, Shopsy, Limeroad
                        "/dp/" in h or           # Amazon
                        "/product/" in h or      # Snapdeal alternate
                        "/pd/" in h or           # BigBasket (/pd/id/name/)
                        "/prd/" in h or          # Blinkit (/prd/name--id)
                        h_clean.endswith('/buy') or  # Myntra
                        (
                            "/products/" in h and  # FirstCry
                            ("/pr_" in h or h_clean.endswith(".aspx"))
                        )
                    )
                    if is_product and h_clean not in seen:
                        seen.add(h_clean)
                        product_links.append(h)

                product_links = product_links[:limit]
                logger.info(f"[LINKS] Found {len(product_links)} product links on search page")

            if not product_links:
                product_links = [url]

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
        # Bot/block check
        page_title = (await page.title()).lower()
        if any(kw in page_title for kw in BOT_KEYWORDS):
            logger.warning(f"[MEESHO] Blocked: {await page.title()}")
            return []

        # Scroll multiple passes to load lazy cards
        # Each pass: scroll down then wait for React to render
        scroll_passes = max(3, limit // 6)
        for i in range(scroll_passes):
            await page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
            await page.wait_for_timeout(1800)
            count = await page.evaluate("document.querySelectorAll('a[href*=\"/p/\"]').length")
            logger.info(f"[MEESHO] Scroll pass {i+1}/{scroll_passes}: {count} product links visible")
            if count >= limit:
                break

        cards = await page.evaluate(r"""
            (limit) => {
                const clean = t => t ? t.replace(/\n/g, ' ').replace(/\s+/g,' ').trim() : '';
                let results = [];
                let seen = new Set();

                // Meesho product cards are always wrapped in <a href*="/p/">
                for (let card of document.querySelectorAll('a[href*="/p/"]')) {
                    if (results.length >= limit) break;
                    let href = card.href.split('?')[0];
                    if (seen.has(href)) continue;
                    seen.add(href);

                    let img = card.querySelector('img');
                    let fullText = (card.textContent || '').replace(/\s+/g,' ').trim();

                    // Price: ₹ followed by digits/commas
                    let priceMatch = fullText.match(/₹\s?[\d,]+/);
                    let price = priceMatch ? priceMatch[0].replace(/\s/g,'') : '';

                    // Name: text before the price (or before MRP/Free Delivery)
                    let name = '';
                    if (price) {
                        let idx = fullText.indexOf(price);
                        if (idx > 2) name = fullText.substring(0, idx).trim();
                    }
                    // Remove leading "+N More" or "Free Delivery" junk
                    name = name.replace(/^(\+\d+\s*More\s*|Free Delivery\s*)+/i, '').trim();

                    if (!name) {
                        // Fallback: explicit text elements inside card
                        let el = card.querySelector('p[class*="Text"], span[class*="Text"], p, h4, h3');
                        if (el) name = clean(el.innerText);
                    }

                    // Rating & reviews
                    let ratingMatch = fullText.match(/([1-5]\.?\d*)\s*\(?\s*(\d[\d,]*)\s*\)?\s*Review/i);
                    let rating = ratingMatch ? ratingMatch[1] : '';
                    let reviews = ratingMatch ? ratingMatch[2] : '';

                    // Seller name: text after "Sold by" if present
                    let sellerMatch = fullText.match(/Sold\s+by\s+([^\n|₹]{3,40})/i);
                    let sellerName = sellerMatch ? sellerMatch[1].trim() : '';

                    if (!name && !price) continue;

                    results.push({
                        url: card.href,
                        product_name: name.substring(0, 150),
                        price: price,
                        image: img ? img.src : '',
                        description: [
                            rating ? `Rating: ${rating}` : '',
                            reviews ? `Reviews: ${reviews}` : '',
                        ].filter(Boolean).join(' | '),
                        seller_name: sellerName,
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
                    name  = result.get('product_name', '').strip()
                    price = result.get('price', '').replace('₹','').replace('Rs','').strip().replace(',','').split('.')[0]
                    # Skip pages that aren't real product pages
                    _junk_words = (
                        'nykaa', 'myntra', 'amazon', 'flipkart', 'snapdeal', 'meesho', 'ajio',
                        'bigbasket', 'jiomart', 'croma', 'firstcry', 'shopsy', 'bewakoof',
                        'online shopping', 'page not found', 'showing results',
                        'search results', "india's online", 'shop online',
                        'grooming products', 'skin care, bath',
                    )
                    is_junk_name  = len(name) < 5 or any(j in name.lower() for j in _junk_words)
                    is_zero_price = price in ('0', '', ' 0')  # ₹0 = search/category page
                    if is_junk_name or (is_zero_price and len(name) > 25):
                        logger.warning(f"[SKIP] Non-product page: {name!r} price={price!r} — {link[:60]}")
                        return None
                    logger.info(f"[OK] {link[:60]} → title={name[:40]!r} price={result.get('price','')!r}")
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

                const clean = t => t ? t.replace(/\n/g, ' ').replace(/\s+/g,' ').trim() : '';
                const host  = location.hostname;
                const isAmazon   = host.includes('amazon.');
                const isMyntra   = host.includes('myntra.');
                const isNykaa    = host.includes('nykaa.');
                const isSnapdeal = host.includes('snapdeal.');
                const isFlipkart = host.includes('flipkart.');

                // ── 1. TITLE ─────────────────────────────────────────────
                if (isAmazon) {
                    // Amazon: #productTitle is the canonical title span
                    let el = document.querySelector('#productTitle, #title_feature_div h1');
                    if (el) result.title = clean(el.innerText);
                } else if (isMyntra) {
                    let brand = document.querySelector('.pdp-title');
                    let name  = document.querySelector('.pdp-name');
                    if (brand || name)
                        result.title = clean((brand ? brand.innerText : '') + ' ' + (name ? name.innerText : ''));
                } else if (isNykaa) {
                    let el = document.querySelector('h1[class*="product"], h1[class*="Product"], .product-title h1, .css-xrzmfa, h1');
                    if (el) result.title = clean(el.innerText);
                } else if (isSnapdeal) {
                    let el = document.querySelector('.pdp-e-i-head, h1.pdp-e-i-head, .buyNow-text, h1');
                    if (el) result.title = clean(el.innerText);
                }
                if (!result.title) {
                    const h1 = document.querySelector('h1');
                    if (h1) result.title = clean(h1.innerText);
                }
                if (!result.title) {
                    // Strip site-name suffixes from document.title
                    result.title = document.title
                        .replace(/\s*[\|–—]\s*[^|–—]{0,40}$/, '')  // "Title | SiteName"
                        .replace(/\s+Price in India\s*$/i, '')       // Shopsy/Flipkart
                        .replace(/\s+-\s+Buy.*$/i, '')               // "Product - Buy Online..."
                        .replace(/\s+Online.*$/i, '')                // "Buy X Online"
                        .split('|')[0].split(' - ')[0].trim();
                }

                // ── 2. PRICE ─────────────────────────────────────────────
                if (isAmazon) {
                    // Amazon: .a-price .a-offscreen has the full "₹XXX" string
                    let el = document.querySelector('.a-price .a-offscreen, .a-price-whole, #priceblock_ourprice, #priceblock_dealprice, #apex_desktop_newAccordionRow .a-price .a-offscreen');
                    if (el) result.price = clean(el.innerText).split('\n')[0];
                } else if (isMyntra) {
                    let el = document.querySelector('.pdp-price strong');
                    if (el) result.price = clean(el.innerText);
                } else if (isSnapdeal) {
                    // Snapdeal: .payBlkBig or .product-price span
                    let el = document.querySelector('.payBlkBig, .product-price, [class*="price"] [class*="value"], .lfloat.product-price');
                    if (el) result.price = clean(el.innerText).match(/[\d,]+/)?.[0] || '';
                    if (result.price) result.price = '₹' + result.price;
                } else if (isNykaa) {
                    let el = document.querySelector('[class*="price"] span, .css-111z9ua, [class*="Price"]');
                    if (el) {
                        let txt = clean(el.innerText);
                        let m = txt.match(/₹\s?[\d,]+/);
                        if (m) result.price = m[0];
                    }
                }

                if (!result.price) {
                    // Generic: find element whose full visible text is exactly a price
                    let priceEls = Array.from(document.querySelectorAll('span,div,p')).filter(e => {
                        let t = (e.innerText || '').trim();
                        return (/^₹\s?[\d,]+(\.\d+)?$/.test(t) || /^Rs\.?\s?[\d,]+/.test(t)) && e.children.length === 0;
                    });
                    if (priceEls.length) {
                        priceEls.sort((a,b) => (parseFloat(window.getComputedStyle(b).fontSize)||0) - (parseFloat(window.getComputedStyle(a).fontSize)||0));
                        result.price = clean(priceEls[0].innerText);
                    }
                }
                if (!result.price) {
                    let m = (document.body.innerText||'').match(/₹\s?[\d,]+/);
                    if (m) result.price = m[0];
                }

                // ── 3. SELLER ────────────────────────────────────────────
                if (isMyntra) {
                    let el = document.querySelector('.pdp-title');
                    if (el) result.seller = clean(el.innerText);
                } else if (isAmazon) {
                    let el = document.querySelector('#sellerProfileTriggerId, #merchant-info a, a#bylineInfo, #bylineInfo');
                    if (el) result.seller = clean(el.innerText);
                } else if (isFlipkart) {
                    let link = document.querySelector('a[href*="/sellers?pid"]');
                    if (link) {
                        let container = link;
                        for (let i = 0; i < 6; i++) container = container.parentElement || container;
                        let m = (container.textContent||'').match(/(?:Sold by|Fulfilled by)\s+([^0-9\n]{2,60}?)(?=\d|See other)/);
                        if (m) result.seller = m[1].trim();
                    }
                    if (!result.seller) {
                        let m = (document.body.innerText||'').match(/Seller:\s*([^\n]{3,60})/);
                        if (m) result.seller = m[1].trim();
                    }
                } else if (isNykaa) {
                    // Nykaa JSON-LD often has brand.name
                    try {
                        let scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                        for (let s of scripts) {
                            let ld = JSON.parse(s.textContent);
                            if (Array.isArray(ld)) ld = ld[0];
                            if (ld && ld.brand && ld.brand.name) { result.seller = ld.brand.name; break; }
                        }
                    } catch(e) {}
                    if (!result.seller) {
                        // Nykaa puts brand as first clickable word in breadcrumb or h1 context
                        let el = document.querySelector('a[href*="/brand/"], .brand-name-text, [data-testid="brand-name"]');
                        if (el && el.innerText && el.innerText.length > 1 && el.innerText.length < 50)
                            result.seller = clean(el.innerText);
                    }
                } else if (isSnapdeal) {
                    let m = (document.body.innerText||'').match(/Sold by[:\s]+([^\n|₹]{3,50})/i);
                    if (m) result.seller = m[1].trim();
                }

                // Fallback: "from <Brand> at Rs." pattern in description
                if (!result.seller && result.description) {
                    let m = result.description.match(/from\s+([A-Za-z0-9 &+'.]{2,50}?)\s+at\s+Rs\./i);
                    if (m) result.seller = m[1].trim();
                }

                // ── 4. IMAGE ─────────────────────────────────────────────
                let ogImage = document.querySelector('meta[property="og:image"]');
                if (ogImage) result.image = ogImage.getAttribute('content');
                if (!result.image) {
                    let img = document.querySelector(
                        'img[src*="rukminim"], img[src*="meesho"], img[src*="myntassets"], ' +
                        'img[src*="nykaa"], img[src*="snapdeal"], img[src*="bigbasket"], ' +
                        'img#landingImage, img.a-dynamic-image, ' +
                        '.image-grid-image img, .pdp-image img'
                    );
                    if (img) {
                        // BigBasket and others use data-src for lazy-loading
                        result.image = img.getAttribute('data-src') || img.src || '';
                    }
                }
                if (!result.image) {
                    // Generic lazy-load fallback: any img with data-src
                    let lazyImg = document.querySelector('img[data-src*="http"]');
                    if (lazyImg) result.image = lazyImg.getAttribute('data-src');
                }

                // ── 5. DESCRIPTION ───────────────────────────────────────
                // JSON-LD first (most reliable across all sites)
                try {
                    let scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                    for (let s of scripts) {
                        let ld = JSON.parse(s.textContent);
                        if (Array.isArray(ld)) ld = ld[0];
                        let desc = '';
                        if (ld && ld.description) desc = ld.description;
                        else if (ld && ld['@graph']) {
                            let prod = ld['@graph'].find(x => x['@type'] === 'Product');
                            if (prod && prod.description) desc = prod.description;
                        }
                        if (desc) { result.description = clean(desc); break; }
                    }
                } catch(e) {}

                if (!result.description) {
                    let el = document.querySelector('meta[property="og:description"], meta[name="description"]');
                    if (el) result.description = el.getAttribute('content');
                }

                if (!result.description) {
                    let bullets = Array.from(document.querySelectorAll('ul li')).filter(li => {
                        let ancestor = li;
                        while (ancestor) {
                            if (['nav','header','footer'].includes((ancestor.tagName||'').toLowerCase())) return false;
                            ancestor = ancestor.parentElement;
                        }
                        let t = (li.innerText||'').trim();
                        return t.length > 8 && t.length < 200 &&
                               !/profile|seller|gift card|notification|wish|cart|login|sign/i.test(t);
                    }).slice(0,5).map(li => (li.innerText||'').trim());
                    if (bullets.length) result.description = bullets.join(' | ');
                }

                return result;
            }
        """)

        seller = data["seller"]
        # URL-based brand fallback for Myntra: /category/brand-slug/product.../id/buy
        if not seller:
            parts = urllib.parse.urlparse(url).path.strip('/').split('/')
            if len(parts) >= 2 and parts[-1] == 'buy':
                brand_slug = parts[1]
                seller = brand_slug.replace('+', ' ').replace('-', ' ').title()

        import re as _re
        title = data["title"]
        # Strip common site-appended suffixes from any title source (h1, og:title, etc.)
        for pat in (
            r'\s+Price in India\s*$',
            r'\s*[|\-–—]\s*(Buy|Shop|Online|Flipkart|Amazon|Myntra|Nykaa|BigBasket|FirstCry|Shopsy).*$',
            r'\s+Online\s*$',
        ):
            title = _re.sub(pat, '', title, flags=_re.IGNORECASE).strip()

        return {
            "url": url,
            "product_name": title,
            "price": data["price"],
            "image": data["image"],
            "description": data["description"],
            "seller_name": seller,
            "seller_phone": data["seller_phone"],
        }
