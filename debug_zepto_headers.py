"""Debug script to copy headers from HOME request and use for CATEGORY fetch"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        headers_to_copy = {}

        async def on_request(req):
            if "get_page" in req.url and "page_type=HOM" in req.url:
                headers_to_copy.update(req.headers)
                print(f"Captured headers from HOME: {len(headers_to_copy)} keys")

        page.on("request", on_request)

        print("Loading homepage...")
        await page.goto("https://www.zepto.com/", wait_until="domcontentloaded", timeout=20000)
        try: await page.wait_for_load_state("networkidle", timeout=8000)
        except: pass

        if headers_to_copy:
            # remove some restricted headers that fetch cannot set
            for k in ["host", "connection", "content-length", "origin", "referer", "accept-encoding", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site"]:
                headers_to_copy.pop(k, None)
            
            lat = "12.96902"
            lon = "77.75395"
            cid = "64374cfe-d06f-4a01-898e-c07c46462c36"
            scid = "b4827798-fcb6-4520-ba5b-0f2bd9bd7208"
            
            api_url = f"https://bff-gateway.zepto.com/lms/api/v2/get_page?latitude={lat}&longitude={lon}&page_type=SUBCATEGORY&category_id={cid}&sub_category_id={scid}"
            print(f"\nTrying fetch with copied headers: {api_url}")
            
            resp = await page.evaluate(f"""
                async (headers) => {{
                    try {{
                        const r = await fetch('{api_url}', {{
                            headers: headers,
                            credentials: 'include'
                        }});
                        return {{status: r.status, text: await r.text()}};
                    }} catch(e) {{
                        return {{error: e.toString()}};
                    }}
                }}
            """, headers_to_copy)
            
            print(f"Status: {resp.get('status')}")
            print(f"Response snippet: {resp.get('text', '')[:200]}")
        else:
            print("Failed to capture HOME headers")

        await ctx.close()

asyncio.run(main())
