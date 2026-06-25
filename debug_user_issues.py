import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
from agents.ecommerce_agent import EcommerceAgent

async def main():
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        
        # Test Zepto search
        print("\n=== Testing Zepto Search ===")
        url_zepto = "https://www.zepto.com/search?q=apple"
        results_z = await agent.scrape(url_zepto, limit=5, pages=1)
        print(f"Zepto got {len(results_z)} results")
        for r in results_z[:2]: print(r['product_name'])

        # Test Myntra Kids Shop
        print("\n=== Testing Myntra Kids ===")
        url_myntra = "https://www.myntra.com/shop/kids"
        results_m = await agent.scrape(url_myntra, limit=5, pages=1)
        print(f"Myntra got {len(results_m)} results")
        for r in results_m[:2]: print(r['product_name'])

asyncio.run(main())
