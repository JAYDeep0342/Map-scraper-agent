import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        print("Navigating...")
        await page.goto("https://www.flipkart.com/apple-iphone-15-black-128-gb/p/itm6ac6485515ae4", timeout=60000)
        await page.wait_for_timeout(4000)
        
        data = await page.evaluate("""
            () => {
                let result = { title: "", price: "", seller: "" };
                const clean = t => t ? t.replace(/\\n/g, ' ').trim() : '';
                
                let priceEls = Array.from(document.querySelectorAll('div, span, p')).filter(e => {
                    let t = e.innerText || '';
                    return t.match(/^[₹$]\\s?[\\d,]+(\\.\\d+)?$/) || t.match(/^[\\d,]+(\\.\\d+)?\\s?[₹$]$/);
                });
                if (priceEls.length > 0) {
                    priceEls.sort((a, b) => {
                        let fsA = parseFloat(window.getComputedStyle(a).fontSize) || 0;
                        let fsB = parseFloat(window.getComputedStyle(b).fontSize) || 0;
                        return fsB - fsA;
                    });
                    result.price = clean(priceEls[0].innerText);
                }
                
                let sellerLabels = Array.from(document.querySelectorAll('*')).filter(e => {
                    let t = e.innerText || '';
                    return t.includes('Seller') && !t.includes('Become a Seller');
                });
                if (sellerLabels.length > 0) {
                    // Just dump the HTML of the first match to see what it is
                    result.sellerHTML = sellerLabels[0].innerHTML.substring(0, 500);
                    
                    let possibleSellers = Array.from(sellerLabels[0].querySelectorAll('a, span[class*="font-bold"]'));
                    if (possibleSellers.length > 0) {
                        result.seller = clean(possibleSellers[0].innerText);
                    }
                }
                return result;
            }
        """)
        
        print("Data extracted:", data)
        await browser.close()

asyncio.run(main())
