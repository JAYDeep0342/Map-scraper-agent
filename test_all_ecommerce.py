"""Quick smoke-test — runs EcommerceAgent on 5 major Indian sites, limit=3 each."""
import asyncio, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import logging
logging.basicConfig(level=logging.WARNING)   # suppress verbose playwright logs

from agents.ecommerce_agent import EcommerceAgent
from browser.browser_manager import BrowserManager

SITES = [
    ("Amazon",    "https://www.amazon.in/s?k=moisturizer",                    3),
    ("Flipkart",  "https://www.flipkart.com/search?q=moisturizer",            3),
    ("Meesho",    "https://www.meesho.com/women-kurtis/pl/3j0",               3),
    ("Myntra",    "https://www.myntra.com/moisturiser",                       3),
    ("Nykaa",     "https://www.nykaa.com/skin/moisturizers-and-creams/c/1478",3),
    ("Snapdeal",  "https://www.snapdeal.com/search?keyword=moisturizer",       3),
    ("Ajio",      "https://www.ajio.com/women-kurtas-and-kurtis/c/830218014", 3),
]

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

async def test_site(agent, name, url, limit):
    t0 = time.time()
    try:
        results = await agent.scrape(url, limit=limit, enrich_sellers=False)
        elapsed = round(time.time() - t0, 1)
        ok = len(results)
        has_price  = sum(1 for r in results if r.get("price","").strip() and r["price"] != "₹0")
        has_name   = sum(1 for r in results if len(r.get("product_name","")) > 5)
        has_image  = sum(1 for r in results if r.get("image","").startswith("http"))
        has_seller = sum(1 for r in results if r.get("seller_name","").strip())
        status = GREEN + "PASS" + RESET if ok >= 1 else RED + "FAIL" + RESET
        print(f"\n{'='*60}")
        print(f"[{status}] {name}  ({elapsed}s)  got {ok}/{limit} products")
        print(f"  price:{has_price}/{ok}  name:{has_name}/{ok}  image:{has_image}/{ok}  seller:{has_seller}/{ok}")
        for i, r in enumerate(results, 1):
            print(f"  [{i}] {r.get('product_name','')[:55]!r}")
            print(f"       price={r.get('price','')!r}  seller={r.get('seller_name','')!r}")
            print(f"       img={'OK' if r.get('image','').startswith('http') else 'MISSING'}")
        return ok, has_price, has_name
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        print(f"\n{'='*60}")
        print(f"[{RED}ERROR{RESET}] {name} ({elapsed}s): {type(e).__name__}: {e}")
        return 0, 0, 0

async def main():
    print("Starting EcommerceAgent multi-site test...\n")
    async with BrowserManager(headless=True) as mgr:
        agent = EcommerceAgent(mgr)
        totals = []
        for name, url, limit in SITES:
            print(f"Testing {name}: {url}")
            res = await test_site(agent, name, url, limit)
            totals.append((name, *res))

    print(f"\n{'='*60}")
    print("SUMMARY:")
    for name, ok, prices, names in totals:
        icon = "OK" if ok >= 1 else "FAIL"
        print(f"  [{icon:4s}] {name:<12} products={ok}  prices={prices}  names={names}")

asyncio.run(main())
