"""Debug Meesho category page — see what DOM looks like."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager

URL = "https://www.meesho.com/smartphones/pl/3y5b"

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

        print(f"Title: {await page.title()}")
        print(f"URL after nav: {page.url}")

        # Check for product links with various patterns
        result = await page.evaluate(r"""
            () => {
                const clean = t => t ? t.replace(/\n/g,' ').replace(/\s+/g,' ').trim() : '';

                // Check various selectors
                const checks = {
                    'a[href*="/p/"]': document.querySelectorAll('a[href*="/p/"]').length,
                    'a[href*="/pl/"]': document.querySelectorAll('a[href*="/pl/"]').length,
                    '[class*="Card"]': document.querySelectorAll('[class*="Card"]').length,
                    '[class*="product"]': document.querySelectorAll('[class*="product"]').length,
                    'a[href*="meesho"]': document.querySelectorAll('a[href]').length,
                    'img': document.querySelectorAll('img').length,
                };

                // Sample /p/ links found
                const productLinks = [...document.querySelectorAll('a[href*="/p/"]')]
                    .slice(0, 5)
                    .map(a => ({ href: a.href.substring(0, 80), text: clean(a.innerText).substring(0, 40) }));

                // Sample all anchors
                const allLinks = [...document.querySelectorAll('a[href]')]
                    .filter(a => a.href.includes('meesho.com') && !a.href.includes('seller'))
                    .slice(0, 10)
                    .map(a => a.href.substring(0, 80));

                // Body text sample
                const bodyText = (document.body.innerText || '').substring(0, 300);

                // Price elements
                const prices = [...document.querySelectorAll('*')]
                    .filter(e => {
                        const t = (e.innerText || '').trim();
                        return /^₹[\d,]+$/.test(t) && e.children.length === 0;
                    })
                    .slice(0, 5)
                    .map(e => e.innerText.trim());

                return { checks, productLinks, allLinks, bodyText, prices };
            }
        """)

        print(f"\nSelector counts: {result['checks']}")
        print(f"\nProduct links (/p/):")
        for l in result['productLinks']:
            print(f"  {l['href']} | {l['text']}")
        print(f"\nAll Meesho links (sample):")
        for l in result['allLinks']:
            print(f"  {l}")
        print(f"\nPrices found: {result['prices']}")
        print(f"\nBody text:\n{result['bodyText']}")

        # Now scroll and check again
        print("\n--- Scrolling 3 times ---")
        for i in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            count = await page.evaluate("document.querySelectorAll('a[href*=\"/p/\"]').length")
            print(f"  Scroll {i+1}: /p/ links = {count}")

        # Final state
        final = await page.evaluate(r"""
            () => {
                const anchors = [...document.querySelectorAll('a[href*="/p/"]')];
                return anchors.slice(0, 3).map(a => ({
                    href: a.href.substring(0, 80),
                    text: (a.innerText || '').trim().substring(0, 60)
                }));
            }
        """)
        print(f"\nFinal /p/ links after scroll:")
        for f in final:
            print(f"  {f['href']} | {f['text']}")

        await ctx.close()

asyncio.run(main())
