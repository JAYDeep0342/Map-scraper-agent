# main.py
"""Entry point for the Google Maps Lead Scraper API.

Usage:
    python main.py                      # start on 0.0.0.0:8000
    python main.py --port 9000          # custom port
    python main.py --host 127.0.0.1     # localhost only
"""

import argparse
import asyncio
import sys

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Maps Lead Scraper API")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print(f"""
    ================================================
      Google Maps Lead Scraper API
      http://{args.host}:{args.port}

      Docs:  http://localhost:{args.port}/docs
      POST:  /scrape/sync
      GET:   /health
    ================================================
    """)

    uvicorn.run(
        "api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
