"""Debug: Visit Flipkart sellers page and extract first seller name."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager

SELLERS_URL = "https://www.flipkart.com/sellers?pid=MOBGTAGPTB3VS24W"

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()
        await page.goto(SELLERS_URL, wait_until="domcontentloaded", timeout=20000)
        try:
            await page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            pass

        result = await page.evaluate(r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';

                // Get page title
                const title = document.title;

                // Try to find seller names - they're usually in list items or divs
                // Look for the first seller name in the list
                let sellers = [];

                // Strategy: look for divs/spans that appear to be seller names
                // Flipkart sellers page typically shows seller name, rating, price
                const candidates = [...document.querySelectorAll('div, span, p, a')]
                    .filter(e => {
                        const t = (e.innerText || '').trim();
                        // Seller names are typically 3-50 chars, no special chars
                        return t.length > 2 && t.length < 60 &&
                               !t.includes('₹') &&
                               !/^(add to cart|buy now|view all|see|click|sold|flipkart|login|sign|cart|wish)/i.test(t) &&
                               e.children.length === 0;  // leaf elements only
                    })
                    .slice(0, 20)
                    .map(e => ({
                        tag: e.tagName,
                        text: clean(e.innerText),
                        class: e.className.substring(0, 50),
                    }));

                // Body text to search for seller
                const bodyText = (document.body.innerText || '').substring(0, 2000);

                return { title, candidates, bodyText };
            }
        """)

        print(f"Title: {result['title']}")
        print(f"\nBody text (first 500 chars):")
        print(result['bodyText'][:500])
        print(f"\nLeaf element candidates:")
        for c in result['candidates'][:15]:
            print(f"  [{c['tag']}] class={c['class']!r} text={c['text']!r}")

        await ctx.close()

asyncio.run(main())
