"""Extended site coverage test — tests 12 more Indian e-commerce sites."""
import asyncio, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING)

from agents.ecommerce_agent import EcommerceAgent
from browser.browser_manager import BrowserManager

SITES = [
    ("TataCliq",       "https://www.tatacliq.com/search/?searchCategory=all&text=moisturizer",   2),
    ("BigBasket",      "https://www.bigbasket.com/ps/?q=moisturizer",                             2),
    ("JioMart",        "https://www.jiomart.com/search/moisturizer",                              2),
    ("Croma",          "https://www.croma.com/searchB?q=headphones",                             2),
    ("Blinkit",        "https://blinkit.com/s/?q=moisturizer",                                    2),
    ("Zepto",          "https://www.zeptonow.com/search?query=moisturizer",                       2),
    ("Pepperfry",      "https://www.pepperfry.com/search?q=chair",                               2),
    ("FirstCry",       "https://www.firstcry.com/search?q=moisturizer",                           2),
    ("Shopsy",         "https://www.shopsy.in/search?q=kurti",                                    2),
    ("Bewakoof",       "https://www.bewakoof.com/t-shirts",                                       2),
    ("Limeroad",       "https://www.limeroad.com/search?q=kurti",                                 2),
    ("Indiamart",      "https://www.indiamart.com/search.mp?ss=moisturizer",                      2),
]

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; E = "\033[0m"

async def test_site(agent, name, url, limit):
    t0 = time.time()
    try:
        results = await agent.scrape(url, limit=limit, enrich_sellers=False)
        elapsed = round(time.time() - t0, 1)
        ok        = len(results)
        has_price = sum(1 for r in results if r.get("price","").strip() and r["price"] not in ("₹0",""))
        has_name  = sum(1 for r in results if len(r.get("product_name","")) > 5)
        has_img   = sum(1 for r in results if r.get("image","").startswith("http"))
        status    = f"{G}PASS{E}" if ok >= 1 else f"{R}FAIL{E}"
        print(f"\n{'='*55}")
        print(f"[{status}] {name}  ({elapsed}s)  {ok}/{limit} products")
        print(f"  price:{has_price}/{ok}  name:{has_name}/{ok}  img:{has_img}/{ok}")
        for i, r in enumerate(results[:2], 1):
            print(f"  [{i}] {r.get('product_name','')[:60]!r}")
            print(f"       price={r.get('price','')!r}")
        return name, ok, has_price, has_name, elapsed
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        print(f"\n{'='*55}")
        print(f"[{R}ERROR{E}] {name} ({elapsed}s): {type(e).__name__}: {str(e)[:80]}")
        return name, 0, 0, 0, elapsed

async def main():
    print("Testing 12 more e-commerce sites (limit=2 each)...\n")
    async with BrowserManager(headless=True) as mgr:
        agent = EcommerceAgent(mgr)
        rows = []
        for name, url, limit in SITES:
            print(f"→ {name}: {url[:60]}")
            row = await test_site(agent, name, url, limit)
            rows.append(row)

    print(f"\n{'='*55}")
    print("FINAL SUMMARY:")
    print(f"{'Site':<14} {'Result':<6} {'Products':>8} {'Prices':>7} {'Names':>6} {'Time':>6}")
    print("-"*55)
    for name, ok, prices, names, t in rows:
        result = f"{G}OK{E}  " if ok >= 1 else f"{R}FAIL{E}"
        print(f"{name:<14} {result}  {ok:>8}  {prices:>7}  {names:>6}  {t:>5}s")

asyncio.run(main())
