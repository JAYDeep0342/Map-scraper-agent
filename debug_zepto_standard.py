"""Debug script to test Zepto DOM scraping"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
from agents.ecommerce_agent import EcommerceAgent

async def main():
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        
        url = "https://www.zepto.com/search?q=apple"
        print(f"Scraping Zepto Search: {url}")
        results = await agent.scrape(url, limit=3, pages=1)
        
        print(f"\nScraped {len(results)} products!")
        for r in results:
            print(f"- {r['product_name']} | {r['price']}")

asyncio.run(main())
