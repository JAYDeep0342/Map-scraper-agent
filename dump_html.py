import asyncio
from playwright.async_api import async_playwright

async def get_html():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://smartshop.lk-ea.com/category/mcb", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html = await page.content()
        with open("smartshop_dump.html", "w", encoding="utf-8") as f:
            f.write(html)
        await browser.close()

asyncio.run(get_html())
