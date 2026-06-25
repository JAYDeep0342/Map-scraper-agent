"""Debug script for Zepto SPA Next.js routing"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager

URL = "https://www.zepto.com/cn/fruits-vegetables/fresh-vegetables/cid/64374cfe-d06f-4a01-898e-c07c46462c36/scid/b4827798-fcb6-4520-ba5b-0f2bd9bd7208"

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        async def on_response(response):
            if "json" in response.headers.get("content-type", ""):
                try:
                    data = await response.json()
                    url = response.url
                    if "bff-gateway" in url and "get_page" in url:
                        print(f"Captured API: {url[:100]}")
                        if "pageLayout" in data:
                            print("  -> Found pageLayout")
                except:
                    pass

        page.on("response", on_response)

        print("Loading homepage...")
        await page.goto("https://www.zepto.com/", wait_until="domcontentloaded", timeout=20000)
        try: await page.wait_for_load_state("networkidle", timeout=8000)
        except: pass

        print("\nTriggering Next.js router push...")
        path = URL.replace("https://www.zepto.com", "")
        # Look for a real link to click instead, since Next.js might be hidden
        res = await page.evaluate(f"""
            async () => {{
                if (window.next && window.next.router) {{
                    window.next.router.push('{path}');
                    return "router.push";
                }} else {{
                    const a = document.createElement('a');
                    a.href = '{path}';
                    a.innerHTML = 'Test Link';
                    // Need to find an existing Next.js Link container, usually Next uses data-nextjs-route
                    // Just prepending it might not hook it.
                    // Instead, let's look for ANY link on the page and change its href, then click it!
                    const existingLink = document.querySelector('a');
                    if(existingLink) {{
                        existingLink.href = '{path}';
                        existingLink.click();
                        return "clicked modified existing link";
                    }}
                    return "no link found";
                }}
            }}
        """)
        print(f"Router trigger result: {res}")
        
        await asyncio.sleep(5)
        print(f"URL after push: {page.url}")

        await ctx.close()

asyncio.run(main())
