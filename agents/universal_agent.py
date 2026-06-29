"""B2B Electrical Component Scraper Agent."""

import asyncio
import logging
from playwright.async_api import Page
from browser.browser_manager import BrowserManager
from utils.site_detector import SiteDetector

logger = logging.getLogger("b2b-agent")

class UniversalAgent:
    """Agent to universally extract products from B2B and E-commerce websites."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def scrape(self, url: str, limit: int = 10) -> list[dict]:
        """Scrape a list of products from a category or search URL."""
        context = await self.browser_manager.new_context()
        try:
            page = await context.new_page()
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "font", "media", "imageset"] else route.continue_())
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"[B2B-GOTO] Error navigating to {url}: {e}")

            await page.wait_for_timeout(3000)

            # Check if it's already a product page vs a category page
            is_product_page = await page.evaluate("""
                () => document.querySelector('#product-addtocart-button, .product-info-main .tocart, .product-add-form .add-to-cart') !== null
            """)

            product_links = []
            if is_product_page:
                product_links.append(url)
            else:
                platform = await SiteDetector.detect(page)
                logger.info(f"[B2B] Detected platform: {platform} for URL: {url}")
                
                seen = set()
                page_num = 1
                base_url = url.split('?')[0]
                
                while len(product_links) < limit:
                    if page_num > 1:
                        if platform == 'shopify':
                            next_url = f"{base_url}?page={page_num}"
                        elif platform == 'woocommerce':
                            curr_base = base_url if base_url.endswith('/') else base_url + '/'
                            next_url = f"{curr_base}page/{page_num}/"
                        else:
                            next_url = f"{base_url}?p={page_num}"
                            
                        logger.info(f"Navigating to page {page_num}: {next_url}")
                        try:
                            await page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                            await page.wait_for_timeout(3000)
                        except Exception as e:
                            logger.warning(f"[B2B-PAGINATION] Error navigating to {next_url}: {e}")
                            break

                    # Smooth Auto-scroll to trigger lazy loading grids (Infinite Scroll)
                    await page.evaluate("""
                        async () => {
                            await new Promise((resolve) => {
                                let totalHeight = 0;
                                let distance = 300;
                                let scrolls = 0;
                                let timer = setInterval(() => {
                                    let scrollHeight = document.body.scrollHeight;
                                    window.scrollBy(0, distance);
                                    totalHeight += distance;
                                    scrolls += 1;
                                    // Ensure we scroll at least 10 times to let DOM catch up, or stop if we hit absolute bottom
                                    if(scrolls >= 15 || (scrolls >= 5 && totalHeight >= scrollHeight)){
                                        clearInterval(timer);
                                        resolve();
                                    }
                                }, 150);
                            });
                        }
                    """)
                    await page.wait_for_timeout(2000)

                    # Universal Link Extraction Strategy
                    hrefs = await page.evaluate(f"""
                        () => {{
                            const platform = '{platform}';
                            let links = [];
                            
                            if (platform === 'magento') {{
                                links = Array.from(document.querySelectorAll(
                                    '.product-item-info a.product-item-photo, ' +
                                    '.product-item-info a.product-item-link, ' +
                                    '.product-item a.product-item-photo, ' +
                                    '.product-item a.product-item-link, ' +
                                    'a.product-item-photo, ' +
                                    'a.product-item-link, ' +
                                    '.desktoptd1 a'
                                )).map(a => a.href);
                            }} else if (platform === 'shopify') {{
                                links = Array.from(document.querySelectorAll('.product-card a, .grid__item a[href*="/products/"]')).map(a => a.href);
                            }} else if (platform === 'woocommerce') {{
                                links = Array.from(document.querySelectorAll('ul.products li a.woocommerce-loop-product__link')).map(a => a.href);
                            }}
                            
                            // Advanced Heuristic Fallback
                            if (links.length === 0) {{
                                const allLinks = Array.from(document.querySelectorAll('a'));
                                links = allLinks.filter(a => {{
                                    const href = a.href.toLowerCase();
                                    if (!href || href.includes('#') || href.includes('javascript:') || href.includes('mailto:') || href.includes('tel:')) return false;
                                    
                                    const host = window.location.hostname;
                                    
                                    // Domain specific logic
                                    if (host.includes('industrybuying.com')) {{
                                        return href.includes('industrybuying.com/') && href.split('-').length > 2 && !href.includes('/category/') && !href.includes('/brand/');
                                    }}
                                    if (host.includes('justdial.com')) {{
                                        return href.includes('justdial.com/') && !href.includes('/login') && !href.includes('analytics');
                                    }}
                                    if (host.includes('se.com')) {{
                                        return href.includes('/product/') || href.includes('-id-');
                                    }}
                                    if (host.includes('siemens.com')) {{
                                        return href.includes('/products/') && href.match(/[a-z0-9]{4,}-[a-z0-9]{4,}/);
                                    }}
                                    if (host.includes('smartshop.lk-ea.com')) {{
                                        return href.includes('.html') && !href.includes('shop-by-category') && !href.includes('customer');
                                    }}
                                    
                                    // Reject utility links
                                    const badKeywords = ['/cart', '/login', '/account', '/wishlist', '/compare', '/contact', '/about', '/checkout', '/search', 'grievance'];
                                    if (badKeywords.some(kw => href.includes(kw))) return false;
                                    
                                    const hasImage = a.querySelector('img') !== null;
                                    const parentHasProductClass = a.closest('[class*="product-item"], [class*="product-card"], .product-item, .product-card, [class*="grid"] [class*="product"]') !== null;
                                    
                                    return hasImage || parentHasProductClass;
                                }}).map(a => a.href);
                            }}

                            // Deduplicate before returning
                            return [...new Set(links)];
                        }}
                    """)
                    
                    if not hrefs:
                        break
                        
                    new_links_found = 0
                    for h in hrefs:
                        # Prevent adding the exact category URL itself, but allow other links
                        if h and h not in seen and h != url and h != base_url:
                            seen.add(h)
                            product_links.append(h)
                            new_links_found += 1
                            if len(product_links) >= limit:
                                break
                                
                    if new_links_found == 0:
                        break # No new products found, probably reached the end
                        
                    page_num += 1
            
            logger.info(f"[UNIVERSAL] Found {len(product_links)} product links. Limit is {limit}. Launching parallel extraction...")

            results = []
            sem = asyncio.Semaphore(10) # Process up to 10 tabs at once
            
            async def scrape_task(link):
                async with sem:
                    new_page = await context.new_page()
                    # BLOCK IMAGES AND CSS FOR 5x SPEED!
                    await new_page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "font", "media", "imageset"] else route.continue_())
                    try:
                        logger.info(f"[UNIVERSAL] Parallel task started: {link}")
                        return await self._extract_product(new_page, link)
                    except Exception as e:
                        logger.error(f"[UNIVERSAL] Error on {link}: {e}")
                        return None
                    finally:
                        await new_page.close()
                        
            tasks = [scrape_task(l) for l in product_links]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in gathered:
                if isinstance(res, dict) and res.get('product_name'):
                    # Remove all keys where the value is None (null in JSON)
                    clean_res = {k: v for k, v in res.items() if v is not None}
                    results.append(clean_res)
            
            return results
        finally:
            await context.close()

    async def _extract_product(self, page: Page, url: str) -> dict:
        """Extract detailed specs from a single B2B product page."""
        try:
            if page.url != url:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"[B2B-PRODUCT-GOTO] Failed to navigate to {url}: {e}")
            if "net::" in str(e) or "NameNotResolved" in str(e) or "ERR_" in str(e) or "Timeout" in str(e):
                return {}

        data = await page.evaluate(r"""
            () => {
                const clean = t => t ? t.replace(/\s+/g, ' ').trim() : null;
                
                let result = {
                    url: window.location.href,
                    product_name: null,
                    sku: null,
                    price_with_tax: null,
                    base_price: null,
                    gst: null,
                    image: null,
                    pdf_url: null,
                    brand: null,
                    model: null,
                    reference_no: null,
                    ampere_rating: null,
                    breaking_capacity: null,
                    no_of_poles: null,
                    tripping_type: null,
                    protection_type: null,
                    rated_voltage: null
                };

                // Try parsing JSON-LD structured data first for clean metadata
                try {
                    const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
                    for (const script of scripts) {
                        try {
                            let text = script.textContent;
                            // Clean single-line and multi-line comments
                            text = text.replace(/\/\/.*/g, '');
                            text = text.replace(/\/\*[\s\S]*?\*\//g, '');
                            
                            const data = JSON.parse(text.trim());
                            
                            const extractFromProduct = (prod) => {
                                if (prod.name) result.product_name = clean(prod.name);
                                if (prod.sku) result.sku = clean(prod.sku);
                                if (prod.brand) {
                                    if (typeof prod.brand === 'string') result.brand = clean(prod.brand);
                                    else if (prod.brand.name) result.brand = clean(prod.brand.name);
                                }
                                if (prod.manufacturer) {
                                    if (typeof prod.manufacturer === 'string') result.brand = clean(prod.manufacturer);
                                    else if (prod.manufacturer.name) result.brand = clean(prod.manufacturer.name);
                                }
                                if (prod.image) {
                                    if (typeof prod.image === 'string') result.image = prod.image;
                                    else if (Array.isArray(prod.image) && prod.image.length > 0) result.image = prod.image[0];
                                }
                                if (prod.offers) {
                                    let offer = Array.isArray(prod.offers) ? prod.offers[0] : prod.offers;
                                    if (offer.price) {
                                        let currency = offer.priceCurrency || '₹';
                                        result.base_price = currency + clean(offer.price.toString());
                                    }
                                }
                            };

                            if (data) {
                                if (data['@type'] === 'Product') {
                                    extractFromProduct(data);
                                } else if (Array.isArray(data)) {
                                    for (const item of data) {
                                        if (item['@type'] === 'Product') extractFromProduct(item);
                                    }
                                } else if (data['@graph'] && Array.isArray(data['@graph'])) {
                                    for (const item of data['@graph']) {
                                        if (item['@type'] === 'Product') extractFromProduct(item);
                                    }
                                }
                            }
                        } catch (e) {
                            // script parse error
                        }
                    }
                } catch (e) {
                    // JSON-LD general error
                }

                // 1. Basic Info
                if (!result.product_name) {
                    let h1 = document.querySelector('h1.page-title, h1.product_title, h1.product-title, h1[itemprop="name"], h1');
                    if (h1) result.product_name = clean(h1.innerText);
                }
                if (!result.product_name) result.product_name = document.title;

                if (!result.sku) {
                    let skuEl = document.querySelector('.product.attribute.sku .value, .product.attribute.product-sku-top .value, .product-sku-top .value, [itemprop="sku"], .sku_wrapper .sku, .product-sku');
                    if (skuEl) result.sku = clean(skuEl.innerText);
                }

                // 2. Pricing
                if (!result.price_with_tax) {
                    let finalPrice = document.querySelector('.price-wrapper .price, [data-price-type="finalPrice"] .price, .woocommerce-Price-amount, .price-item--sale, [itemprop="price"]');
                    if (finalPrice) result.price_with_tax = clean(finalPrice.innerText);
                }

                if (!result.base_price) {
                    let basePrice = document.querySelector('.price-wrapper[data-price-type="basePrice"] .price, .old-price .price, .price-item--regular, del .woocommerce-Price-amount');
                    if (basePrice) result.base_price = clean(basePrice.innerText);
                }

                // Try to find GST in text
                let gstEl = Array.from(document.querySelectorAll('span, div, p')).find(e => /GST/i.test(e.innerText) && /%/.test(e.innerText));
                if (gstEl) {
                    let m = gstEl.innerText.match(/(\d+%?)\s*GST/i);
                    if (m) result.gst = m[1];
                }

                // 3. Media
                if (!result.image) {
                    let img = document.querySelector('.gallery-placeholder img, [itemprop="image"]');
                    if (img) result.image = img.src;
                }

                // PDFs
                let pdfLink = document.querySelector('a[href$=".pdf"]');
                if (pdfLink) result.pdf_url = pdfLink.href;

                // 4. Technical Specs
                // We'll scrape all tables and definition lists
                const specMap = {};
                
                // Parse standard tables
                const rows = document.querySelectorAll('table tr, .additional-attributes tr');
                for (const row of rows) {
                    const th = row.querySelector('th, td.label');
                    const td = row.querySelector('td.data, td:not(.label)');
                    if (th && td) {
                        let key = clean(th.innerText).toLowerCase();
                        let val = clean(td.innerText);
                        specMap[key] = val;
                    }
                }

                // Also check definition lists
                const dls = document.querySelectorAll('dl');
                for (const dl of dls) {
                    const dts = dl.querySelectorAll('dt');
                    const dds = dl.querySelectorAll('dd');
                    dts.forEach((dt, idx) => {
                        if (dds[idx]) {
                            specMap[clean(dt.innerText).toLowerCase()] = clean(dds[idx].innerText);
                        }
                    });
                }

                // Map specific keys to our schema and keep the rest in 'specifications'
                result.specifications = {};
                for (const [k, v] of Object.entries(specMap)) {
                    const cleanKey = k.trim().toLowerCase();
                    let mapped = false;
                    
                    if (/brand/i.test(cleanKey) || cleanKey === 'manufacturer') {
                        result.brand = v;
                        mapped = true;
                    }
                    if (/model/i.test(cleanKey)) {
                        result.model = v;
                        mapped = true;
                    }
                    if (/reference/i.test(cleanKey) || /part no/i.test(cleanKey) || /catalog no/i.test(cleanKey) || /cat\.? number/i.test(cleanKey)) {
                        result.reference_no = v;
                        mapped = true;
                    }
                    if (/ampere|current/i.test(cleanKey) && !/leakage|insulation/i.test(cleanKey)) {
                        result.ampere_rating = v;
                        mapped = true;
                    }
                    if (/breaking/i.test(cleanKey) && !/service|ics/i.test(cleanKey)) {
                        result.breaking_capacity = v;
                        mapped = true;
                    }
                    if (/poles|pole/i.test(cleanKey)) {
                        result.no_of_poles = v;
                        mapped = true;
                    }
                    if (/tripping|release|curve/i.test(cleanKey)) {
                        result.tripping_type = v;
                        mapped = true;
                    }
                    if (/protection|ip/i.test(cleanKey)) {
                        result.protection_type = v;
                        mapped = true;
                    }
                    if (/voltage/i.test(cleanKey) && /operational|operating|rated/i.test(cleanKey) && !/insulation|impulse/i.test(cleanKey)) {
                        result.rated_voltage = v;
                        mapped = true;
                    }
                    
                    result.specifications[k] = v;
                }

                // 5. Fallbacks for null values
                if (!result.sku && result.reference_no) result.sku = result.reference_no;
                if (!result.reference_no && result.sku) result.reference_no = result.sku;
                
                // Model extraction from name
                if (!result.model && result.product_name) {
                    let nameWords = result.product_name.split(/[\s,]+/);
                    for (let word of nameWords) {
                        let cleanWord = word.trim().replace(/[(),;]/g, '');
                        if (/[A-Z]+.*\d+|\d+.*[A-Z]+/i.test(cleanWord) && cleanWord.length >= 3 && cleanWord.length <= 25) {
                            if (/^\d+(a|ka|v|p|w|hz|kw)$/i.test(cleanWord) || /^ip\d+$/i.test(cleanWord)) continue;
                            result.model = cleanWord;
                            break;
                        }
                    }
                }
                if (!result.model && result.sku) result.model = result.sku;
                
                if (!result.gst && result.price_with_tax && result.base_price) {
                    let pTax = parseFloat(result.price_with_tax.replace(/[^0-9.]/g, ''));
                    let pBase = parseFloat(result.base_price.replace(/[^0-9.]/g, ''));
                    if (pBase > 0 && pTax > pBase) {
                        let pct = Math.round(((pTax - pBase) / pBase) * 100);
                        if (pct > 0) result.gst = pct + "%";
                    }
                }

                // Brand fallback from name
                if (!result.brand && result.product_name) {
                    const nameLower = result.product_name.toLowerCase();
                    if (nameLower.includes("lauritz knudsen") || nameLower.includes("l&t")) {
                        result.brand = "Lauritz Knudsen";
                    } else if (nameLower.includes("siemens")) {
                        result.brand = "Siemens";
                    } else if (nameLower.includes("schneider")) {
                        result.brand = "Schneider";
                    } else if (nameLower.includes("abb")) {
                        result.brand = "ABB";
                    } else if (nameLower.includes("havells")) {
                        result.brand = "Havells";
                    } else if (nameLower.includes("legrand")) {
                        result.brand = "Legrand";
                    }
                }

                if (!result.price_with_tax && result.base_price) result.price_with_tax = result.base_price;
                if (!result.base_price && result.price_with_tax) result.base_price = result.price_with_tax;

                return result;
            }
        """)
        return data
