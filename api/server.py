

"""FastAPI server — Google Maps Lead Scraper (Sync API).

Single endpoint designed for Spring Boot integration:

    POST /scrape/sync   → scrape Google Maps, return leads as JSON directly

Run:
    python main.py
    # or: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
import logging
import math
import sys
import time
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from agents.maps_agent import MapsAgent
from agents.web_search_agent import WebSearchAgent
from browser.browser_manager import BrowserManager
from skills.extract_skill import ExtractSkill
from config.india_cities import INDIA_CITIES, INDIA_STATES
from config.city_areas import get_city_areas

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
# Decoupled Routers (E-commerce & Social Media)
# ---------------------------------------------------------------------------
from api.routers import ecommerce, social, universal
app.include_router(ecommerce.router)
app.include_router(social.router)
app.include_router(universal.router)


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
        le=100_000,
        description="Maximum number of leads to return (up to 100,000)",
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


@app.post(
    "/scrape/web",
    response_model=ScrapeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Scraping failed"},
    },
    tags=["Web Scraper"],
)
async def scrape_web(req: ScrapeRequest):
    """Standalone Web Search scraper — searches Google web results instead of Google Maps.

    **How it works:**
    - Searches Google web for ``\"<keyword> in <location>\"``
    - Finds Google Maps place links embedded in web search results
    - Scrapes each found Maps place page for business details
    - Optionally visits business websites to extract emails & social links
    - Returns structured leads JSON

    **When to use this instead of /scrape/sync:**
    - When Maps returns very few results (rare businesses, small cities)
    - When you want results discovered via web search ranking, not Maps ranking
    - As a complement to Maps-based scraping
    """
    query = f"{req.keyword} in {req.location}"
    logger.info(
        "[WebScraper] Request: query=%r  limit=%d  find_emails=%s",
        query, req.limit, req.find_emails,
    )
    start = time.time()

    try:
        leads = await _run_web_scrape(query, req.limit, req.find_emails)
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        logger.error("[WebScraper] Failed after %ss: %s", elapsed, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "Web scraping failed",
                "detail": str(exc),
            },
        )

    elapsed = round(time.time() - start, 2)
    logger.info(
        "[WebScraper] Done: %d leads in %ss for %r",
        len(leads), elapsed, query,
    )

    return ScrapeResponse(
        success=True,
        total_leads=len(leads),
        query=query,
        time_seconds=elapsed,
        leads=[LeadItem(**lead) for lead in leads],
    )


# Number of Maps searches per browser session before restarting
# Lower = more reliable, Higher = faster startup
BROWSER_BATCH_SIZE = 25

# Generic search prefix variations — applied to the ORIGINAL keyword
QUERY_PREFIXES = ["best", "top rated", "popular", "famous", "top"]


def _build_query_list(query: str, limit: int) -> list[str]:
    """Build the full list of location queries to try, ordered by priority.

    Strategy (queries added as needed based on limit):
    1. Primary query
    2. Area-based queries (same city, different zones) — ~20 new results each
    3. Prefix variations (best/top/popular + original keyword)
    4. Multi-city across 600+ India cities/districts
    5. State-level queries
    """
    queries: list[str] = []

    keyword = query.split(" in ")[0].strip() if " in " in query else query
    location = query.split(" in ", 1)[1].strip() if " in " in query else ""
    location_lower = location.lower()

    # 1. Primary
    queries.append(query)

    if limit <= 20:
        return queries

    # 2. Area-based (same city, different zones) — best source of new leads
    if location:
        areas = get_city_areas(location)
        for area in areas:
            queries.append(f"{keyword} in {area} {location}")

    # 3. Prefix variations
    if location:
        for prefix in QUERY_PREFIXES:
            queries.append(f"{prefix} {keyword} in {location}")

    if limit <= 300:
        return queries

    # 4. Multi-city (600+ India cities/districts)
    for city in INDIA_CITIES:
        if city.strip().lower() == location_lower:
            continue
        queries.append(f"{keyword} in {city}")

    if limit <= 15000:
        return queries

    # 5. State-level (for very large limits)
    for state in INDIA_STATES:
        queries.append(f"{keyword} in {state}")

    return queries


async def _run_scrape(
    query: str,
    limit: int,
    find_emails: bool,
) -> list[dict]:
    """Scrape leads using batch-based browser sessions for crash resilience.

    Splits all location queries into batches of BROWSER_BATCH_SIZE.
    Each batch gets a fresh browser instance, so a crash in one batch
    does NOT stop the whole job.
    """
    all_leads: list[dict] = []
    seen: set[tuple] = set()

    def _add_leads(new_leads: list[dict]) -> int:
        added = 0
        for lead in new_leads:
            if len(all_leads) >= limit:
                break
            key = (lead["name"].strip().lower(), lead["address"].strip().lower())
            if key not in seen:
                seen.add(key)
                all_leads.append(lead)
                added += 1
        return added

    # Build the full ordered list of queries to run
    query_list = _build_query_list(query, limit)
    total_queries = len(query_list)
    logger.info(
        "Query plan: %d location searches queued for query %r (limit %d)",
        total_queries, query, limit
    )

    # Process queries in batches — fresh browser per batch
    queries_done = 0
    for batch_start in range(0, total_queries, BROWSER_BATCH_SIZE):
        if len(all_leads) >= limit:
            logger.info("Limit reached (%d/%d). Stopping search.", len(all_leads), limit)
            break

        batch = query_list[batch_start: batch_start + BROWSER_BATCH_SIZE]
        batch_num = batch_start // BROWSER_BATCH_SIZE + 1
        total_batches = math.ceil(total_queries / BROWSER_BATCH_SIZE)
        logger.info(
            "--- Batch %d/%d | Leads so far: %d/%d ---",
            batch_num, total_batches, len(all_leads), limit
        )

        try:
            async with BrowserManager() as manager:
                agent = MapsAgent(manager)
                for loc_query in batch:
                    if len(all_leads) >= limit:
                        break
                    queries_done += 1
                    remaining = limit - len(all_leads)
                    logger.info(
                        "[%d/%d] Searching: %r (need %d more)",
                        queries_done, total_queries, loc_query, remaining
                    )
                    try:
                        leads = await agent.scrape(
                            loc_query,
                            max_results=min(remaining, 30),
                            progress=_log_progress,
                        )
                        added = _add_leads(leads)
                        if added > 0:
                            logger.info(
                                "  +%d new leads (total: %d/%d)",
                                added, len(all_leads), limit
                            )
                    except Exception as exc:
                        logger.warning("  Search failed for %r: %s", loc_query, exc)
                        continue

        except Exception as batch_exc:
            logger.warning(
                "Batch %d/%d browser error: %s — starting fresh browser for next batch",
                batch_num, total_batches, batch_exc
            )
            await asyncio.sleep(2)  # brief pause before next batch
            continue

    all_leads = all_leads[:limit]
    logger.info("Scraping done: %d unique leads collected", len(all_leads))

    # Email enrichment in a SEPARATE fresh browser session
    if all_leads and find_emails:
        sites = sum(1 for l in all_leads if l.get("website"))
        logger.info("Starting email enrichment for %d websites...", sites)
        try:
            async with BrowserManager() as enrich_manager:
                context = await enrich_manager.new_context()
                try:
                    await ExtractSkill(context).enrich_leads(all_leads, progress=_log_progress)
                finally:
                    await context.close()
            with_email = sum(1 for l in all_leads if l.get("emails"))
            logger.info("Enrichment done: %d/%d leads have emails", with_email, len(all_leads))
        except Exception as exc:
            logger.warning("Email enrichment failed (leads still returned without emails): %s", exc)

    return all_leads


def _log_progress(msg: str) -> None:
    """Callback for agents to log progress."""
    logger.info(msg.strip())


# ---------------------------------------------------------------------------
# Standalone Web Search Scrape logic
# ---------------------------------------------------------------------------
async def _run_web_scrape(
    query: str,
    limit: int,
    find_emails: bool,
) -> list[dict]:
    """Search Google web for Maps links, scrape them, optionally enrich with emails."""

    async with BrowserManager() as manager:
        web_agent = WebSearchAgent(manager)
        maps_agent = MapsAgent(manager)
        all_leads: list[dict] = []
        seen: set[tuple] = set()

        def _add_leads(new_leads: list[dict]) -> int:
            added = 0
            for lead in new_leads:
                if len(all_leads) >= limit:
                    break
                key = (lead["name"].strip().lower(), lead["address"].strip().lower())
                if key not in seen:
                    seen.add(key)
                    all_leads.append(lead)
                    added += 1
            return added

        # --- Phase 1: Google Web Search -> collect Maps place links ---
        logger.info("[WebScraper] Searching Google web for %r (limit %d)...", query, limit)
        web_links = await web_agent.find_maps_links(
            query=query,
            max_links=limit + 2,  # small buffer for deduplication
            progress=_log_progress,
        )
        logger.info("[WebScraper] Found %d Maps links from web search", len(web_links))

        # --- Phase 2: Scrape each Maps place link for business details ---
        if web_links:
            web_context = await manager.new_context()
            try:
                raw_leads = await maps_agent._scrape_details(
                    web_context, web_links, query, limit, _log_progress
                )
                _add_leads(raw_leads)
            finally:
                await web_context.close()

        # --- Phase 3: If still need more, fall back to Maps search directly ---
        if len(all_leads) < limit:
            remaining = limit - len(all_leads)
            logger.info(
                "[WebScraper] Only %d leads found so far, falling back to Maps search (need %d more)...",
                len(all_leads), remaining
            )
            maps_leads = await maps_agent.scrape(
                query, max_results=remaining, progress=_log_progress
            )
            _add_leads(maps_leads)

        all_leads = all_leads[:limit]
        logger.info("[WebScraper] Got %d leads total", len(all_leads))

        # --- Phase 4: Enrich with emails & social links ---
        if all_leads and find_emails:
            sites = sum(1 for l in all_leads if l.get("website"))
            logger.info("[WebScraper] Enriching %d websites for emails...", sites)
            enrich_context = await manager.new_context()
            try:
                await ExtractSkill(enrich_context).enrich_leads(all_leads, progress=_log_progress)
            finally:
                await enrich_context.close()
            with_email = sum(1 for l in all_leads if l.get("emails"))
            logger.info("[WebScraper] Enrichment done: %d leads have emails", with_email)

    return all_leads


# ---------------------------------------------------------------------------
# Entry point (when run directly: python -m api.server)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn


    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run(app, host="0.0.0.0", port=8000)
