"""FastAPI server — Google Maps Lead Scraper (Sync API).

Single endpoint designed for Spring Boot integration:

    POST /scrape/sync   → scrape Google Maps, return leads as JSON directly

Run:
    python main.py
    # or: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import math
import sys
import time
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from agents.maps_agent import MapsAgent
from browser.browser_manager import BrowserManager
from skills.extract_skill import ExtractSkill

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scraper-api")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Google Maps Lead Scraper API",
    version="2.0",
    description="Sync API for scraping Google Maps business leads. "
                "Designed for Spring Boot backend integration.",
)

# CORS — allow all origins so Spring Boot (any port) can call freely.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ScrapeRequest(BaseModel):
    """What to scrape from Google Maps."""

    keyword: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='Business type to search, e.g. "restaurant"',
        examples=["restaurant", "b tech college", "electronic shop"],
    )
    location: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='City or area, e.g. "Indore"',
        examples=["Indore", "Delhi", "Mumbai"],
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Maximum number of leads to return",
    )
    find_emails: bool = Field(
        default=True,
        description="Visit each business website to extract emails and social links",
    )

    @field_validator("keyword", "location", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


class LeadItem(BaseModel):
    """One scraped business lead."""

    query: str = ""
    name: str = ""
    category: str = ""
    rating: str = ""
    reviews: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    emails: str = ""
    social_links: str = ""
    plus_code: str = ""
    latitude: str = ""
    longitude: str = ""
    maps_url: str = ""


class ScrapeResponse(BaseModel):
    """Response returned by /scrape/sync."""

    success: bool
    total_leads: int
    query: str
    time_seconds: float
    leads: list[LeadItem]


class ErrorResponse(BaseModel):
    """Error envelope."""

    success: bool = False
    error: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    """Quick health check — Spring Boot can ping this to verify the service is up."""
    return {"status": "ok", "service": "google-maps-lead-scraper"}


@app.post(
    "/scrape/sync",
    response_model=ScrapeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Scraping failed"},
    },
)
async def scrape_sync(req: ScrapeRequest):
    """Scrape Google Maps and return leads directly (synchronous).

    - Builds the query: ``"<keyword> in <location>"``
    - Scrolls the Maps results feed to collect listings
    - Opens each listing to extract details
    - Optionally visits business websites for emails & social links
    - Returns everything as a JSON array
    """
    query = f"{req.keyword} in {req.location}"
    logger.info(
        "Scrape request: query=%r  limit=%d  find_emails=%s",
        query, req.limit, req.find_emails,
    )
    start = time.time()

    try:
        leads = await _run_scrape(query, req.limit, req.find_emails)
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        logger.error("Scrape failed after %ss: %s", elapsed, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "Scraping failed",
                "detail": str(exc),
            },
        )

    elapsed = round(time.time() - start, 2)
    logger.info(
        "Scrape done: %d leads in %ss for %r",
        len(leads), elapsed, query,
    )

    return ScrapeResponse(
        success=True,
        total_leads=len(leads),
        query=query,
        time_seconds=elapsed,
        leads=[LeadItem(**lead) for lead in leads],
    )


# ---------------------------------------------------------------------------
# Core scraping logic
# ---------------------------------------------------------------------------
async def _run_scrape(
    query: str,
    limit: int,
    find_emails: bool,
) -> list[dict]:
    """Open a browser, scrape Maps, optionally enrich with emails, return leads."""

    async with BrowserManager() as manager:
        # Step 1: Scrape Google Maps listings
        agent = MapsAgent(manager)
        logger.info("Scraping Maps for %r (limit %d)...", query, limit)
        leads = await agent.scrape(query, max_results=limit, progress=_log_progress)

        # Cap to requested limit
        leads = leads[:limit]
        logger.info("Got %d leads from Maps", len(leads))

        # Step 2: Enrich with emails & social links
        if leads and find_emails:
            sites = sum(1 for l in leads if l.get("website"))
            logger.info("Enriching %d websites for emails...", sites)
            context = await manager.new_context()
            try:
                await ExtractSkill(context).enrich_leads(leads, progress=_log_progress)
            finally:
                await context.close()
            with_email = sum(1 for l in leads if l.get("emails"))
            logger.info("Enrichment done: %d leads have emails", with_email)

    return leads


def _log_progress(msg: str) -> None:
    """Callback for agents to log progress."""
    logger.info(msg.strip())


# ---------------------------------------------------------------------------
# Entry point (when run directly: python -m api.server)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run(app, host="0.0.0.0", port=8000)
