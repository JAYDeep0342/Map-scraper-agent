"""Debug script to grab Zepto search page HTML"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        print("Loading homepage...")
        await page.goto("https://www.zepto.com/", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        
        search_url = "https://www.zepto.com/search?q=apple"
        print(f"Navigating to search URL: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        
        try:
            await page.wait_for_selector('a[href*="/pn/"]', timeout=10000)
            print("Found /pn/ links!")
        except:
            print("No /pn/ links found.")
            
        result = await page.evaluate(r"""
            () => {
                const links = [...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => h.includes('/pn/'));
                const allText = document.body.innerText.substring(0, 1000);
                return {
                    pn_links: links.slice(0, 5),
                    text: allText
                };
            }
        """)
        
        print(f"PN Links: {result['pn_links']}")
        print(f"Text snippet:\n{result['text']}")

        await ctx.close()

asyncio.run(main())
