import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from agents.ecommerce_agent import EcommerceAgent
from browser.browser_manager import BrowserManager


async def test_meesho_homepage():
    """Test Meesho with homepage URL (the failing case from user)"""
    print("\n=== TEST: Meesho Homepage URL (limit=10) ===")
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        results = await agent.scrape("https://www.meesho.com/", limit=10)
    print(f"  Got {len(results)} products")
    for i, r in enumerate(results[:5], 1):
        print(f"  [{i}] name={r['product_name'][:50]!r} | price={r['price']} | rating={r.get('rating','')}")


async def test_meesho_search():
    """Test Meesho with search URL"""
    print("\n=== TEST: Meesho Search URL (limit=10) ===")
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        results = await agent.scrape("https://www.meesho.com/search?q=kurta+for+women", limit=10)
    print(f"  Got {len(results)} products")
    for i, r in enumerate(results[:5], 1):
        print(f"  [{i}] name={r['product_name'][:50]!r} | price={r['price']} | rating={r.get('rating','')}")


async def test_flipkart_seller():
    """Test Flipkart seller fix"""
    print("\n=== TEST: Flipkart Seller Fix ===")
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        results = await agent.scrape(
            "https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4",
            limit=1,
        )
    if results:
        r = results[0]
        print(f"  product : {r['product_name'][:60]}")
        print(f"  price   : {r['price']}")
        print(f"  seller  : {r['seller_name']!r}")


if __name__ == "__main__":
    asyncio.run(test_meesho_homepage())
    asyncio.run(test_meesho_search())
    asyncio.run(test_flipkart_seller())
    print("\nAll done!")
