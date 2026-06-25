"""Test only the fixable new sites."""
import asyncio, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import logging; logging.basicConfig(level=logging.WARNING)

from agents.ecommerce_agent import EcommerceAgent
from browser.browser_manager import BrowserManager

SITES = [
    ("BigBasket",  "https://www.bigbasket.com/ps/?q=moisturizer",       3),
    ("FirstCry",   "https://www.firstcry.com/search?q=baby+lotion",     3),
    ("Shopsy",     "https://www.shopsy.in/search?q=kurti",              3),
    ("Bewakoof",   "https://www.bewakoof.com/t-shirts",                 3),
]

G = "\033[92m"; R = "\033[91m"; E = "\033[0m"

async def main():
    async with BrowserManager(headless=True) as mgr:
        agent = EcommerceAgent(mgr)
        for name, url, limit in SITES:
            t0 = time.time()
            print(f"\n{'='*55}")
            print(f"Testing {name}...")
            try:
                results = await agent.scrape(url, limit=limit, enrich_sellers=False)
                ok = len(results)
                status = f"{G}PASS{E}" if ok >= 1 else f"{R}FAIL{E}"
                print(f"[{status}] {ok}/{limit} products  ({round(time.time()-t0,1)}s)")
                for i, r in enumerate(results, 1):
                    print(f"  [{i}] {r.get('product_name','')[:60]!r}")
                    print(f"       price={r.get('price','')!r}  seller={r.get('seller_name','')!r}")
                    print(f"       img={'OK' if r.get('image','').startswith('http') else 'MISS'}")
            except Exception as e:
                print(f"[{R}ERROR{E}] {type(e).__name__}: {e}")

asyncio.run(main())
