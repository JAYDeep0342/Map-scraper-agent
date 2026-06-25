"""Test seller extraction on product detail pages."""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
from agents.ecommerce_agent import EcommerceAgent

async def main():
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        
        # Test Flipkart â€” single product (limit=1 triggers _extract_flipkart with seller)
        print("=== FLIPKART SINGLE PRODUCT (seller test) ===")
        url = "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac17b81e6b18?pid=MOBGTAGPTB3VS24W"
        results = await agent.scrape(url, limit=1, )
        for r in results:
            print(f"  Name:   {r.get('product_name', '')[:50]}")
            print(f"  Price:  {r.get('price', '')}")
            print(f"  Seller: {r.get('seller_name', '')}")
            print(f"  Rating: {r.get('rating', '')} | Reviews: {r.get('reviews', '')}")

        # Test Flipkart â€” search (limit=2, uses _scrape_new_tabs â†’ _extract_flipkart)
        print("\n=== FLIPKART SEARCH (seller from tabs) ===")
        url2 = "https://www.flipkart.com/search?q=iphone+15"
        results2 = await agent.scrape(url2, limit=2, )
        for r in results2:
            print(f"  Name:   {r.get('product_name', '')[:50]}")
            print(f"  Price:  {r.get('price', '')}")
            print(f"  Seller: {r.get('seller_name', '')}")
            print(f"  Rating: {r.get('rating', '')} | Reviews: {r.get('reviews', '')}")

        # Test Myntra â€” cards (brand as seller)
        print("\n=== MYNTRA CARDS (brand=seller) ===")
        url3 = "https://www.myntra.com/face-wash-and-cleanser"
        results3 = await agent.scrape(url3, limit=3, )
        for r in results3:
            print(f"  Name:   {r.get('product_name', '')[:50]}")
            print(f"  Price:  {r.get('price', '')}")
            print(f"  Seller: {r.get('seller_name', '')}")
            print(f"  Rating: {r.get('rating', '')} | Reviews: {r.get('reviews', '')}")

asyncio.run(main())


