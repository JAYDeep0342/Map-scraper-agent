"""Debug script for Flipkart store page"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
import json

URL = "https://www.flipkart.com/aw-base-new-inline-2025-at-store?pageUID=1782288510255"

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        print(f"Navigating to: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)

        # Wait a bit for React to hydrate
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
            
        # Scroll down to trigger lazy loading
        for i in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

        print(f"Title: {await page.title()}")
        print(f"URL after nav: {page.url}")

        result = await page.evaluate(r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';
                
                const allLinks = [...document.querySelectorAll('a[href]')];
                
                // Find elements containing ₹
                const priceEls = [...document.querySelectorAll('*')].filter(e => {
                    const t = (e.innerText || '').trim();
                    return /^₹[\d,]+$/.test(t) && e.children.length === 0;
                });
                
                // For each price element, find the closest anchor
                const products = [];
                for(const p of priceEls.slice(0, 5)) {
                    let curr = p;
                    let anchor = null;
                    for(let i=0; i<5 && curr; i++) {
                        curr = curr.parentElement;
                        if(curr && curr.tagName === 'A') {
                            anchor = curr;
                            break;
                        }
                        if(curr && curr.querySelector('a')) {
                            anchor = curr.querySelector('a');
                            break;
                        }
                    }
                    if(anchor) {
                        products.push({
                            price: clean(p.innerText),
                            href: anchor.href.substring(0, 100),
                            text: clean(anchor.innerText).substring(0, 50)
                        });
                    }
                }
                
                // Grab all anchors that don't point to generic pages
                const exclude = ['/account/', '/wishlist', '/plus', '/search', 'seller.', 'about.'];
                const potentialProducts = allLinks
                    .filter(a => !exclude.some(ex => a.href.includes(ex)))
                    .map(a => a.href)
                    .filter((v, i, a) => a.indexOf(v) === i); // unique

                return {
                    priceCount: priceEls.length,
                    productsWithPrices: products,
                    potentialProducts: potentialProducts.slice(0, 15)
                };
            }
        """)

        print(f"\nPrice elements found: {result['priceCount']}")
        print("\nProducts found near prices:")
        for p in result['productsWithPrices']:
            print(f"  Price: {p['price']} | Href: {p['href']} | Text: {p['text']}")
            
        print("\nPotential product links:")
        for l in result['potentialProducts']:
            print(f"  {l}")

        await ctx.close()

asyncio.run(main())
