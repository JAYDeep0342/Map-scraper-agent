"""Check what product links each site actually has in DOM after scroll."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import logging; logging.basicConfig(level=logging.WARNING)

from browser.browser_manager import BrowserManager

SITES = [
    ("BigBasket",  "https://www.bigbasket.com/ps/?q=moisturizer"),
    ("JioMart",    "https://www.jiomart.com/search/moisturizer"),
    ("Croma",      "https://www.croma.com/searchB?q=headphones"),
    ("Blinkit",    "https://blinkit.com/s/?q=moisturizer"),
    ("Zepto",      "https://www.zeptonow.com/search?query=moisturizer"),
    ("FirstCry",   "https://www.firstcry.com/search?q=moisturizer"),
    ("Shopsy",     "https://www.shopsy.in/search?q=kurti"),
    ("Limeroad",   "https://www.limeroad.com/search?q=kurti"),
]

async def check_site(mgr, name, url):
    ctx = await mgr.new_context()
    try:
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"  GOTO error: {e}")

        await page.wait_for_timeout(3000)

        # Scroll twice to trigger lazy load
        for _ in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

        # Check what link patterns exist
        result = await page.evaluate("""
            () => {
                const allHrefs = [...document.querySelectorAll('a[href]')].map(a => a.href);
                const patterns = {
                    '/p/' : allHrefs.filter(h => h.includes('/p/')).length,
                    '/dp/': allHrefs.filter(h => h.includes('/dp/')).length,
                    '/pd/': allHrefs.filter(h => h.includes('/pd/')).length,
                    '/product/': allHrefs.filter(h => h.includes('/product/')).length,
                    '/products/': allHrefs.filter(h => h.includes('/products/')).length,
                    '/prd/': allHrefs.filter(h => h.includes('/prd/')).length,
                    '/buy' : allHrefs.filter(h => h.trimEnd('/').endsWith('/buy')).length,
                    '.html': allHrefs.filter(h => h.endsWith('.html')).length,
                };
                // Sample the most common non-nav internal hrefs
                const internal = [...new Set(
                    allHrefs.filter(h => h.includes(location.hostname) && !h.includes('#'))
                )].slice(0, 6);
                return { patterns, sample: internal, total: allHrefs.length };
            }
        """)

        print(f"\n{'='*55}")
        print(f"[{name}] {url[:55]}")
        print(f"  Total links: {result['total']}")
        print(f"  Patterns found:")
        for pat, cnt in result['patterns'].items():
            if cnt > 0:
                print(f"    {pat:15s}: {cnt}")
        print(f"  Sample hrefs:")
        for h in result['sample']:
            print(f"    {h[:80]}")
    finally:
        await ctx.close()

async def main():
    async with BrowserManager(headless=True) as mgr:
        for name, url in SITES:
            print(f"Checking {name}...")
            await check_site(mgr, name, url)

asyncio.run(main())
