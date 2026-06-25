import asyncio
import csv
import logging
from browser.browser_manager import BrowserManager
from agents.b2b_agent import B2BAgent

logging.basicConfig(level=logging.INFO, format='%(message)s')

async def main():
    # URL from the PDF data (Siemens MCCB category)
    urls = [
        "https://www.eleczo.com/siemens-3vj1010-0da32-0aa0-100-amp-3-pole-mccb.html",
        "https://www.eleczo.com/siemens-3vj1002-0da32-0aa0-20-amp-3-pole-10-ka-ftfm-mccb.html",
        "https://www.eleczo.com/siemens-3va1125-3ed22-0aa0-25-amp-2-pole-mccb.html"
    ]
    
    print(f"Testing direct product scraping...")
    
    async with BrowserManager() as manager:
        agent = B2BAgent(manager)
        results = []
        for u in urls:
            res = await agent.scrape(u, limit=1)
            results.extend(res)
        
        if not results:
            print("No data extracted. The site might have blocked us or changed layout.")
            return

        print(f"\nSuccessfully extracted {len(results)} products!")
        
        # Save to CSV
        csv_file = "b2b_data_output.csv"
        headers = results[0].keys()
        
        with open(csv_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(results)
            
        print(f"Data saved to {csv_file}")
        
        # Print first result as sample
        print("\n--- SAMPLE EXTRACTED DATA ---")
        for k, v in results[0].items():
            print(f"{k}: {v}")

if __name__ == "__main__":
    if __import__("sys").platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
