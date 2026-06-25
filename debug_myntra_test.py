"""Test Myntra card extraction with new selectors."""
import asyncio, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
from agents.ecommerce_agent import EcommerceAgent

async def main():
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        
        url = "https://www.myntra.com/face-wash-and-cleanser"
        print(f"Scraping: {url}")
        results = await agent.scrape(url, limit=5, pages=1)
        
        print(f"\nFound {len(results)} products")
        for r in results:
            print(f"\n  Name:  {r['product_name']}")
            print(f"  Price: {r['price']}")
            print(f"  Desc:  {r['description']}")
            print(f"  Image: {r['image'][:60]}...")
            print(f"  URL:   {r['url'][:70]}...")

asyncio.run(main())
