"""Final test: Myntra + Flipkart."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
from agents.ecommerce_agent import EcommerceAgent

async def main():
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        
        # Test 1: Myntra
        print("=== MYNTRA ===")
        url = "https://www.myntra.com/face-wash-and-cleanser"
        results = await agent.scrape(url, limit=5, pages=1)
        print(f"Found {len(results)} products")
        for r in results:
            name = r.get('product_name', '')[:50]
            price = r.get('price', '')
            desc = r.get('description', '')[:60]
            print(f"  {name} | {price} | {desc}")

        # Test 2: Flipkart
        print("\n=== FLIPKART ===")
        url2 = "https://www.flipkart.com/search?q=iphone"
        results2 = await agent.scrape(url2, limit=3, pages=1)
        print(f"Found {len(results2)} products")
        for r in results2:
            name = r.get('product_name', '')[:50]
            price = r.get('price', '')
            print(f"  {name} | {price}")

asyncio.run(main())
