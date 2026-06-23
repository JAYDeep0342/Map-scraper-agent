import asyncio, sys, io, logging
logging.basicConfig(level=logging.INFO)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from browser.browser_manager import BrowserManager

async def test():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        p = await ctx.new_page()
        await p.goto('https://www.meesho.com/search?q=kurti', wait_until='domcontentloaded', timeout=20000)
        await p.wait_for_timeout(4000)
        print("Title:", await p.title())

        # Try to extract product cards directly from search page
        data = await p.evaluate(r"""
        () => {
            // Meesho search result cards
            // Each card has: image, product name, price
            let results = [];

            // Try to find product cards — look for elements with price AND image
            let cards = Array.from(document.querySelectorAll('a[href*="/p/"]'));
            for (let card of cards.slice(0, 5)) {
                let img = card.querySelector('img');
                let price = Array.from(card.querySelectorAll('*')).find(e => {
                    let t = (e.innerText || '').trim();
                    return /^[₹]\s?[\d,]+$/.test(t);
                });
                let name = card.querySelector('p, h4, h3, span[class*="name"], div[class*="name"]');
                // Fallback: get all text from card
                let fullText = (card.textContent || '').trim().substring(0, 200);

                results.push({
                    url: card.href,
                    image: img ? img.src : '',
                    price: price ? price.innerText.trim() : '',
                    name_attempt: name ? name.innerText.trim().substring(0, 80) : '',
                    full_text: fullText.substring(0, 150)
                });
            }
            return results;
        }
        """)

        print(f"\nFound {len(data)} product cards on search page")
        for i, item in enumerate(data[:3]):
            print(f"\n--- Card {i+1} ---")
            for k, v in item.items():
                print(f"  {k}: {str(v)[:100]}")

        await ctx.close()

asyncio.run(test())
