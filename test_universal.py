import asyncio
import json
import sys
import logging
from browser.browser_manager import BrowserManager
from agents.b2b_agent import B2BAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

urls = [
    "https://smartshop.lk-ea.com/category/mcb", # Added category path to test actual products
    "https://eshop.se.com/in/products/mcb.html", # Adding category path
    "https://havells.com/switches/modular-range.html",
    "https://www.industrybuying.com/mcb-miniature-circuit-breaker-agri-pro-189617/" # direct product or category
]

async def main():
    browser_manager = BrowserManager()
    await browser_manager.start()
    agent = B2BAgent(browser_manager)

    results = {}
    for url in urls:
        logging.info(f"--- TESTING: {url} ---")
        try:
            data = await agent.scrape(url, limit=3)
            results[url] = data
            logging.info(f"SUCCESS: Extracted {len(data)} items.")
        except Exception as e:
            logging.error(f"FAILED on {url}: {e}")
            results[url] = {"error": str(e)}

    with open("test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)
        
    await browser_manager.close()

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
asyncio.run(main())
