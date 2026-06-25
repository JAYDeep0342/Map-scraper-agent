import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from api.routers.ecommerce import EcommerceRequest
from browser.browser_manager import BrowserManager
from agents.ecommerce_agent import EcommerceAgent

async def test_zepto_search():
    async with BrowserManager() as mgr:
        agent = EcommerceAgent(mgr)
        url = 'https://www.zepto.com/search?q=fresh+vegetables'
        print('\nScraping Zepto Search:', url)
        results = await agent.scrape(url, limit=3, pages=1)
        print(f'Found {len(results)} products on Zepto search')
        for r in results:
            print(f"  - {r.get('product_name', '')[:40]} | {r.get('price')} | {r.get('url')[:60]}")

async def test_myntra_validator():
    try:
        r = EcommerceRequest(url='https://www.myntra.com/shop/kids', limit=10)
        print('❌ Myntra Validator FAIL: Should have blocked /shop/ URL!')
    except Exception as e:
        print('✅ Myntra Validator OK:')
        print(str(e))

async def main():
    await test_myntra_validator()
    await test_zepto_search()

asyncio.run(main())
