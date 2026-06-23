import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4", timeout=30000)
        await page.wait_for_timeout(3000)
        
        # Dump price
        price_el = await page.query_selector("div:has-text('₹')")
        if price_el:
            print("Found Rupee symbol")
            
        # Let's get all div classes that contain ₹
        divs = await page.eval_on_selector_all("div", "els => els.filter(e => e.innerText && e.innerText.startsWith('₹')).map(e => e.className)")
        print("Price classes:", divs[:5])
          # Let's get seller info
        # Look for the word "Seller" and get its parent
        seller_html = await page.evaluate("""
            () => {
                let sellerEls = Array.from(document.querySelectorAll('div, span')).filter(e => e.innerText && e.innerText.includes('Seller'));
                return sellerEls.map(e => e.innerHTML).slice(-2);
            }
        """)
        print("Seller HTML:", seller_html)
        
        await browser.close()

asyncio.run(main())
