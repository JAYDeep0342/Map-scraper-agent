"""Debug script for Zepto fetch"""
import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, "playwright-agent")

from browser.browser_manager import BrowserManager
import json

async def main():
    async with BrowserManager() as mgr:
        ctx = await mgr.new_context()
        page = await ctx.new_page()

        print(f"Loading Zepto homepage...")
        await page.goto("https://www.zepto.com/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)
        
        # Inject our fetch test
        lat = "12.96902"
        lon = "77.75395"
        cid = "64374cfe-d06f-4a01-898e-c07c46462c36"
        scid = "b4827798-fcb6-4520-ba5b-0f2bd9bd7208"
        
        for page_type in ("SUBCATEGORY", "CATEGORY", "PLP"):
            api_url = (
                f"https://bff-gateway.zepto.com/lms/api/v2/get_page"
                f"?latitude={lat}&longitude={lon}&page_type={page_type}"
                + (f"&category_id={cid}" if cid else "")
                + (f"&sub_category_id={scid}" if scid else "")
            )
            print(f"\nTrying fetch: {api_url}")
            
            resp = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch({repr(api_url)}, {{
                            headers: {{
                                "storeid": "b4dc8d65-ed2e-4142-81b6-373982b13500", 
                                "tenant": "ZEPTO"
                            }},
                            credentials: 'include'
                        }});
                        return {{status: r.status, text: await r.text()}};
                    }} catch(e) {{
                        return {{error: e.toString()}};
                    }}
                }}
            """)
            print(f"Status: {resp.get('status')}")
            text = resp.get('text', '')
            print(f"Response: {text[:200]}")
            
            # also test without headers
            print(f"Trying fetch without explicit headers:")
            resp2 = await page.evaluate(f"""
                async () => {{
                    try {{
                        const r = await fetch({repr(api_url)}, {{credentials: 'include'}});
                        return {{status: r.status, text: await r.text()}};
                    }} catch(e) {{
                        return {{error: e.toString()}};
                    }}
                }}
            """)
            print(f"Status: {resp2.get('status')}")
            print(f"Response: {resp2.get('text', '')[:200]}")

        await ctx.close()

asyncio.run(main())
