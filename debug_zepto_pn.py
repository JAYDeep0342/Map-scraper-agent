"""Debug script to check if Zepto product URLs are blocked"""
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
        
        prod_url = "https://www.zepto.com/pn/little-joys-nutrimix-chocolate-drink-mix-for-2-6-years-kids/pvid/7584659c-b0cb-4591-9f97-d4970a6b1cab"
        print(f"Navigating to product URL: {prod_url}")
        await page.goto(prod_url, wait_until="domcontentloaded", timeout=20000)
        
        print(f"Title: {await page.title()}")

        await ctx.close()

asyncio.run(main())
