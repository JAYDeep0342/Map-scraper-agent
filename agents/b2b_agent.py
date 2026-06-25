"""B2B Electrical Component Scraper Agent."""

import asyncio
import logging
from playwright.async_api import Page
from browser.browser_manager import BrowserManager

logger = logging.getLogger("b2b-agent")

class B2BAgent:
    """Agent to scrape B2B electrical component sites (e.g., eleczo.com)."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def scrape(self, url: str, limit: int = 10) -> list[dict]:
        """Scrape a list of products from a category or search URL."""
        context = await self.browser_manager.new_context()
        try:
            page = await context.new_page()
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
                seen = set()
                page_num = 1
                base_url = url.split('?')[0]
                
                while len(product_links) < limit:
                    if page_num > 1:
                        next_url = f"{base_url}?p={page_num}"
                        logger.info(f"Navigating to page {page_num}: {next_url}")
                        try:
                            await page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                            await page.wait_for_timeout(3000)
                        except Exception as e:
                            logger.warning(f"[B2B-PAGINATION] Error navigating to {next_url}: {e}")
                            break

                    # Extract all links and filter heuristically
                    hrefs = await page.evaluate("""
                        () => {
                            return Array.from(document.querySelectorAll('.desktoptd1 a, .product-item-info a, a.product-item-link'))
                                .map(a => a.href)
                                .filter(href => {
                                    if (!href.endsWith('.html')) return false;
                                    if (href.includes('?')) return false;
                                    if (href.includes('#')) return false;
                                    return true;
                                });
                        }
                    """)
                    
                    if not hrefs:
                        break
                        
                    new_links_found = 0
                    for h in hrefs:
                        if h and h not in seen and base_url not in h: # Prevent adding the category URL itself
                            seen.add(h)
                            product_links.append(h)
                            new_links_found += 1
                            if len(product_links) >= limit:
                                break
                                
                    if new_links_found == 0:
                        break # No new products found, probably reached the end
                        
                    page_num += 1
            
            logger.info(f"[B2B] Found {len(product_links)} product links. Limit is {limit}.")

            results = []
            for link in product_links:
                logger.info(f"[B2B] Scraping product: {link}")
                try:
                    product_data = await self._extract_product(page, link)
                    if product_data:
                        results.append(product_data)
                except Exception as e:
                    logger.error(f"[B2B] Failed to scrape {link}: {e}")
            
            return results
        finally:
            await context.close()

    async def _extract_product(self, page: Page, url: str) -> dict:
        """Extract detailed specs from a single B2B product page."""
        try:
            if page.url != url:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)
        except Exception:
            pass

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

                // 1. Basic Info
                let h1 = document.querySelector('h1.page-title, h1');
                if (h1) result.product_name = clean(h1.innerText);
                if (!result.product_name) result.product_name = document.title;

                let skuEl = document.querySelector('.product.attribute.sku .value, [itemprop="sku"]');
                if (skuEl) result.sku = clean(skuEl.innerText);

                // 2. Pricing
                let finalPrice = document.querySelector('.price-wrapper .price, [data-price-type="finalPrice"] .price');
                if (finalPrice) result.price_with_tax = clean(finalPrice.innerText);

                let basePrice = document.querySelector('.price-wrapper[data-price-type="basePrice"] .price, .old-price .price');
                if (basePrice) result.base_price = clean(basePrice.innerText);

                // Try to find GST in text
                let gstEl = Array.from(document.querySelectorAll('span, div, p')).find(e => /GST/i.test(e.innerText) && /%/.test(e.innerText));
                if (gstEl) {
                    let m = gstEl.innerText.match(/(\d+%?)\s*GST/i);
                    if (m) result.gst = m[1];
                }

                // 3. Media
                let img = document.querySelector('.gallery-placeholder img, [itemprop="image"]');
                if (img) result.image = img.src;

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
                    if (/brand/i.test(k) || k === 'manufacturer') result.brand = v;
                    else if (/model/i.test(k)) result.model = v;
                    else if (/reference/i.test(k) || /part no/i.test(k)) result.reference_no = v;
                    else result.specifications[k] = v;
                }

                // 5. Fallbacks for null values
                if (!result.sku && result.reference_no) result.sku = result.reference_no;
                
                if (!result.gst && result.price_with_tax && result.base_price) {
                    let pTax = parseFloat(result.price_with_tax.replace(/[^0-9.]/g, ''));
                    let pBase = parseFloat(result.base_price.replace(/[^0-9.]/g, ''));
                    if (pBase > 0 && pTax > pBase) {
                        let pct = Math.round(((pTax - pBase) / pBase) * 100);
                        if (pct > 0) result.gst = pct + "%";
                    }
                }

                return result;
            }
        """)
        return data
