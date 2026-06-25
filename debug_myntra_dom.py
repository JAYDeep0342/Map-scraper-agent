"""Debug Myntra card DOM structure to understand price/name layout."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager

URL = "https://www.myntra.com/face-wash-and-cleanser"

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        print(f"Navigating to: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except: pass

        # Scroll to load cards
        for i in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.5)

        result = await page.evaluate(r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';
                
                // Find product card anchors
                const anchors = [...document.querySelectorAll('a[href]')].filter(a => {
                    return /\/\d+\/buy/.test(new URL(a.href, location.href).pathname);
                });
                
                const cards = [];
                const seen = new Set();
                
                for (const a of anchors.slice(0, 3)) {
                    const href = a.href.split('?')[0];
                    if (seen.has(href)) continue;
                    seen.add(href);
                    
                    // Get inner HTML structure
                    const children = [...a.querySelectorAll('*')];
                    const elements = children.map(el => ({
                        tag: el.tagName,
                        className: (el.className || '').toString().substring(0, 60),
                        text: clean(el.textContent).substring(0, 80),
                        childCount: el.children.length
                    })).filter(e => e.text && e.childCount === 0);
                    
                    // Find specific elements
                    const brand = a.querySelector('[class*="product-brand"]');
                    const prodName = a.querySelector('[class*="product-product"]');
                    const price = a.querySelector('[class*="product-discountedPrice"]');
                    const strike = a.querySelector('[class*="product-strike"]');
                    const discount = a.querySelector('[class*="product-discountPercentage"]');
                    const rating = a.querySelector('[class*="product-ratingsCount"]');
                    const sizes = a.querySelector('[class*="product-sizeInventoryPresent"]');
                    
                    cards.push({
                        href: href.substring(0, 80),
                        fullText: clean(a.textContent).substring(0, 200),
                        brand: brand ? clean(brand.textContent) : null,
                        prodName: prodName ? clean(prodName.textContent) : null,
                        price: price ? clean(price.textContent) : null,
                        strikePrice: strike ? clean(strike.textContent) : null,
                        discount: discount ? clean(discount.textContent) : null,
                        rating: rating ? clean(rating.textContent) : null,
                        sizes: sizes ? clean(sizes.textContent) : null,
                        leafElements: elements.slice(0, 15)
                    });
                }
                
                return { total: anchors.length, cards };
            }
        """)

        print(f"\nTotal product anchors: {result['total']}")
        for i, card in enumerate(result['cards']):
            print(f"\n--- Card {i+1} ---")
            print(f"URL: {card['href']}")
            print(f"Full text: {card['fullText']}")
            print(f"Brand: {card['brand']}")
            print(f"Product: {card['prodName']}")
            print(f"Price: {card['price']}")
            print(f"Strike: {card['strikePrice']}")
            print(f"Discount: {card['discount']}")
            print(f"Rating: {card['rating']}")
            print(f"Sizes: {card['sizes']}")
            print(f"Leaf elements:")
            for e in card['leafElements']:
                print(f"  {e['tag']}.{e['className'][:30]} => {e['text']}")

        await ctx.close()

asyncio.run(main())
