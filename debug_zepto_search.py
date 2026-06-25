"""Debug script for Zepto search"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
import urllib.parse

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        print(f"Loading Zepto homepage...")
        await page.goto("https://www.zepto.com/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        
        search_url = "https://www.zepto.com/search?q=fresh%20vegetables"
        print(f"Navigating to search URL: {search_url}")
        
        resp = await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        
        print(f"Status: {resp.status}")
        print(f"Title: {await page.title()}")
        print(f"URL: {page.url}")
        
        if "Access Denied" in await page.title():
            print("Search is ALSO blocked!")
        else:
            print("Search WORKS! Let's check products.")
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except: pass
            
            result = await page.evaluate(r"""
                () => {
                    const priceEls = [...document.querySelectorAll('*')].filter(e => {
                        const t = (e.innerText || '').trim();
                        return /^₹[\d,]+$/.test(t) && e.children.length === 0;
                    });
                    return priceEls.length;
                }
            """)
            print(f"Found {result} prices on search page.")

        await ctx.close()

asyncio.run(main())
