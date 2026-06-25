"""Debug script for Zepto category page"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
import json

URL = "https://www.zepto.com/cn/fruits-vegetables/fresh-vegetables/cid/64374cfe-d06f-4a01-898e-c07c46462c36/scid/b4827798-fcb6-4520-ba5b-0f2bd9bd7208"

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        print(f"Loading Zepto homepage to set cookies...")
        await page.goto("https://www.zepto.com/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)

        api_responses = []

        async def on_response(response):
            if "json" in response.headers.get("content-type", ""):
                try:
                    data = await response.json()
                    url = response.url
                    if "bff-gateway" in url and "get_page" in url:
                        api_responses.append(data)
                        print(f"Captured get_page API: {url[:120]}")
                        if "pageLayout" in data:
                            print("  Found pageLayout")
                            layout = data["pageLayout"]
                            if "sections" in layout:
                                print(f"  Found {len(layout['sections'])} sections")
                                for i, sec in enumerate(layout['sections']):
                                    widgets = sec.get("widgets", [])
                                    print(f"    Section {i}: {len(widgets)} widgets")
                                    for j, w in enumerate(widgets[:3]):
                                        wdata = w.get("data", {})
                                        print(f"      Widget {j} type={w.get('widgetType')} data_keys={list(wdata.keys())[:8] if isinstance(wdata, dict) else '?'}")
                                        if isinstance(wdata, dict):
                                            for k, v in wdata.items():
                                                if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) and "name" in v[0]:
                                                    print(f"        Found items in data key '{k}', sample name: {v[0].get('name')}")
                        if "storeServiceableResponse" in data:
                            print(f"  storeId: {data['storeServiceableResponse'].get('storeId')}")
                except Exception as e:
                    pass

        page.on("response", on_response)

        print(f"Navigating to: {URL}")
        await page.goto(URL, wait_until="domcontentloaded", timeout=25000)

        # Wait a bit for React to hydrate
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        print("\n--- Scrolling 3 times ---")
        for i in range(3):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        
        await ctx.close()
        
        with open("zepto_debug.json", "w") as f:
            json.dump(api_responses, f, indent=2)

asyncio.run(main())
