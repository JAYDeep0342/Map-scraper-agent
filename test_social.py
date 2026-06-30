import asyncio
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from agents.social_agent import SocialAgent
from browser.browser_manager import BrowserManager

TEST_URLS = [
    "https://www.instagram.com/instagram/",
    "https://x.com/GitHub",
    "https://www.youtube.com/@Google",
    "https://www.facebook.com/zuck",
    "https://www.linkedin.com/in/williamhgates",
]

async def test_social_scraper():
    async with BrowserManager() as mgr:
        agent = SocialAgent(mgr)
        for url in TEST_URLS:
            print(f"\nScraping: {url} ...")
            try:
                result = await agent.scrape(url)
                print(f"Result: {result}")
            except Exception as e:
                print(f"Failed scraping {url}: {e}")

if __name__ == "__main__":
    asyncio.run(test_social_scraper())
